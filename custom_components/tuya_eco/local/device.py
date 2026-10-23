"""Per-device state machine: connection, polling, reconnect and key refresh."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import ipaddress
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback

from ..providers.base import CloudDevice, DpType
from .discovery import DiscoveredDevice, TuyaDiscovery
from .protocol import (
    SUPPORTED_VERSIONS,
    TuyaConnectionError,
    TuyaDecodeError,
    TuyaDeviceError,
    TuyaKeyError,
    TuyaLanProtocol,
    TuyaListener,
    TuyaLocalError,
    TuyaTimeoutError,
    connect,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_VERSION_ORDER: tuple[float, ...] = (3.3, 3.4, 3.5, 3.1, 3.2)
BACKOFF_MIN = 5.0
BACKOFF_MAX = 300.0
KEY_FAILURE_THRESHOLD = 2  # full connect cycles that failed only because of the key
UPDATE_DPS_WHITELIST = (18, 19, 20)  # energy DPs that most plugs only push on request

StateListener = Callable[[], None]
KeyInvalidCallback = Callable[["EcoDevice"], Awaitable[None]]
HostChangedCallback = Callable[["EcoDevice"], None]


def _is_private(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


class EcoDevice(TuyaListener):
    """One Tuya device controlled over the LAN."""

    def __init__(
        self,
        hass: HomeAssistant,
        info: CloudDevice,
        discovery: TuyaDiscovery,
        *,
        poll_interval: float = 30.0,
        host: str | None = None,
        version: float | None = None,
        gateway: CloudDevice | None = None,
        on_key_invalid: KeyInvalidCallback | None = None,
        on_host_changed: HostChangedCallback | None = None,
    ) -> None:
        """Initialise (no I/O)."""
        self.hass = hass
        self.info = info
        self.gateway = gateway
        self._discovery = discovery
        self.poll_interval = poll_interval
        self.host: str | None = host
        self.version: float | None = version
        self.dps: dict[str, Any] = {}
        self.available = False
        self.last_update: float = 0.0
        self.last_error: str | None = None
        self._on_key_invalid = on_key_invalid
        self._on_host_changed = on_host_changed
        self._listeners: list[StateListener] = []
        self._link: TuyaLanProtocol | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._wake = asyncio.Event()
        self._disconnected = asyncio.Event()
        self._unsub_discovery: Callable[[], None] | None = None
        self._key_failure_cycles = 0
        self._pending: dict[str, tuple[Any, float]] = {}
        # Per-DP counter of *pushed* reports (event entities fire on every push, not on polls).
        self.push_counts: dict[str, int] = {}

    # ------------------------------------------------------------------ identity

    @property
    def id(self) -> str:
        """Tuya device id."""
        return self.info.id

    @property
    def name(self) -> str:
        """Display name."""
        return self.info.name

    @property
    def connected(self) -> bool:
        """True while a LAN link is open."""
        return self._link is not None and self._link.connected

    @property
    def _link_device_id(self) -> str:
        return self.gateway.id if self.gateway else self.info.id

    @property
    def _link_key(self) -> str:
        return self.gateway.local_key if self.gateway else self.info.local_key

    @property
    def _node_id(self) -> str | None:
        return self.info.node_id or None if self.gateway else None

    # ------------------------------------------------------------------ listeners

    @callback
    def async_add_listener(self, listener: StateListener) -> Callable[[], None]:
        """Register an entity update callback."""
        self._listeners.append(listener)

        def _unsub() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return _unsub

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("%s: listener failed", self.id)

    # ------------------------------------------------------------------ lifecycle

    async def async_start(self) -> None:
        """Start the connection loop."""
        if self._running:
            return
        self._running = True
        self._unsub_discovery = self._discovery.async_add_listener(self._discovered, self._link_device_id)
        self._task = self.hass.loop.create_task(self._run(), name=f"tuya_eco.{self.id}")

    async def async_stop(self) -> None:
        """Stop and disconnect."""
        self._running = False
        if self._unsub_discovery:
            self._unsub_discovery()
            self._unsub_discovery = None
        self._wake.set()
        self._disconnected.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._close_link()

    async def _close_link(self) -> None:
        link, self._link = self._link, None
        if link:
            await link.close()
        if self.available:
            self.available = False
            self._notify()

    def update_info(self, info: CloudDevice, gateway: CloudDevice | None = None) -> None:
        """Apply refreshed cloud data; reconnect if the key changed."""
        key_changed = info.local_key != self.info.local_key or (
            gateway is not None and self.gateway is not None and gateway.local_key != self.gateway.local_key
        )
        if not info.dp_schema and self.info.dp_schema:
            info.dp_schema = self.info.dp_schema  # keep what we had
        self.info = info
        if gateway is not None:
            self.gateway = gateway
        if key_changed:
            _LOGGER.info("%s: local key changed, reconnecting", self.id)
            self._key_failure_cycles = 0
            self._disconnected.set()
            self._wake.set()

    @callback
    def _discovered(self, found: DiscoveredDevice) -> None:
        if found.ip != self.host:
            _LOGGER.info("%s: now at %s (was %s)", self.id, found.ip, self.host)
            self.host = found.ip
            if self._on_host_changed:
                self._on_host_changed(self)
            if self.connected:
                self._disconnected.set()  # reconnect to the new address
        if found.version:
            with contextlib.suppress(ValueError):
                ver = float(found.version)
                if ver in SUPPORTED_VERSIONS and ver != self.version:
                    self.version = ver
        if not self.connected:
            self._wake.set()

    # ------------------------------------------------------------------ connection loop

    def _candidate_hosts(self) -> list[str]:
        hosts: list[str] = []
        seen = self._discovery.get(self._link_device_id)
        if seen:
            hosts.append(seen.ip)
        if self.host and self.host not in hosts:
            hosts.append(self.host)
        hint = self.gateway.ip if self.gateway else self.info.ip
        if _is_private(hint) and hint not in hosts:
            hosts.append(hint)  # type: ignore[arg-type]
        return hosts

    def _candidate_versions(self) -> list[float]:
        order: list[float] = []
        seen = self._discovery.get(self._link_device_id)
        if seen and seen.version:
            with contextlib.suppress(ValueError):
                order.append(float(seen.version))
        if self.version:
            order.append(self.version)
        hint = self.gateway.protocol_version if self.gateway else self.info.protocol_version
        if hint:
            with contextlib.suppress(ValueError):
                order.append(float(hint))
        order.extend(DEFAULT_VERSION_ORDER)
        result: list[float] = []
        for ver in order:
            if ver in SUPPORTED_VERSIONS and ver not in result:
                result.append(ver)
        return result

    async def _run(self) -> None:
        backoff = BACKOFF_MIN
        while self._running:
            try:
                connected = await self._try_connect()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.exception("%s: unexpected error in connect", self.id)
                connected = False
            if connected:
                backoff = BACKOFF_MIN
                await self._serve()
                if not self._running:
                    return
                # Short pause before reconnecting after a drop.
                await self._wait(2.0)
                continue
            await self._wait(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def _wait(self, seconds: float) -> None:
        # A wake-up that arrived while we were busy connecting must not be lost:
        # the event is cleared *after* waiting, never before.
        try:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), seconds)
        finally:
            self._wake.clear()

    async def _try_connect(self) -> bool:
        hosts = self._candidate_hosts()
        if not hosts:
            self.last_error = "no IP address known yet (waiting for LAN discovery)"
            return False
        if not self._link_key:
            self.last_error = "no local key"
            return False
        versions = self._candidate_versions()
        key_errors = 0
        attempts = 0
        dps_to_request = sorted(self.info.dp_schema) or None
        for host in hosts:
            for version in versions:
                attempts += 1
                link: TuyaLanProtocol | None = None
                try:
                    link = await connect(
                        host,
                        self._link_device_id,
                        self._link_key,
                        version,
                        self,
                        node_id=self._node_id,
                        dps_to_request=dps_to_request,
                    )
                    dps = await link.status()
                except TuyaKeyError as err:
                    key_errors += 1
                    self.last_error = f"v{version} @ {host}: {err}"
                    _LOGGER.debug("%s: %s", self.id, self.last_error)
                except (TuyaTimeoutError, TuyaConnectionError, TuyaDecodeError, TuyaDeviceError) as err:
                    self.last_error = f"v{version} @ {host}: {err}"
                    _LOGGER.debug("%s: %s", self.id, self.last_error)
                    if isinstance(err, TuyaConnectionError) and link is None:
                        break  # host unreachable: no point trying other versions
                else:
                    self._link = link
                    self.host = host
                    self.version = version
                    self.last_error = None
                    self._key_failure_cycles = 0
                    self._disconnected.clear()
                    self._apply_dps(dps)
                    self.available = True
                    self.last_update = time.time()
                    self._notify()
                    link.start_heartbeat()
                    _LOGGER.info("%s: connected to %s (protocol %s)", self.name, host, version)
                    return True
                if link is not None:
                    await link.close()
        if attempts and key_errors == attempts:
            self._key_failure_cycles += 1
            if self._key_failure_cycles >= KEY_FAILURE_THRESHOLD and self._on_key_invalid:
                self._key_failure_cycles = 0
                _LOGGER.info("%s: local key seems invalid, asking provider for a fresh one", self.id)
                await self._on_key_invalid(self)
        return False

    async def _serve(self) -> None:
        """Poll periodically until the link drops."""
        link = self._link
        if link is None:
            return
        try:
            while self._running and link.connected:
                try:
                    await asyncio.wait_for(self._disconnected.wait(), self.poll_interval)
                    break  # disconnected or asked to reconnect
                except TimeoutError:
                    pass
                try:
                    dps = await link.status()
                    self._apply_dps(dps)
                    self.last_update = time.time()
                    self._notify()
                    await self._request_push_dps(link)
                except TuyaTimeoutError:
                    self.last_error = "poll timed out"
                    _LOGGER.debug("%s: poll timed out, reconnecting", self.id)
                    break
                except TuyaLocalError as err:
                    self.last_error = f"poll failed: {err}"
                    _LOGGER.debug("%s: %s", self.id, self.last_error)
                    break
        finally:
            await self._close_link()

    async def _request_push_dps(self, link: TuyaLanProtocol) -> None:
        wanted = [dp for dp in UPDATE_DPS_WHITELIST if str(dp) in self.dps or dp in self.info.dp_schema]
        if wanted:
            with contextlib.suppress(TuyaLocalError):
                await link.update_dps(wanted)

    # ------------------------------------------------------------------ TuyaListener

    def status_updated(self, dps: dict[str, Any]) -> None:
        """Push from the device."""
        self._apply_dps(dps, pushed=True)
        self.last_update = time.time()
        if not self.available:
            self.available = True
        self._notify()

    def disconnected(self, exc: Exception | None) -> None:
        """Link dropped."""
        if exc:
            self.last_error = f"disconnected: {exc}"
        self._disconnected.set()

    # ------------------------------------------------------------------ state

    def _apply_dps(self, dps: dict[str, Any], *, pushed: bool = False) -> None:
        now = time.monotonic()
        for dp, value in dps.items():
            key = str(dp)
            self.dps[key] = value
            if pushed:
                self.push_counts[key] = self.push_counts.get(key, 0) + 1
            pending = self._pending.get(key)
            if pending and (pending[0] == value or now - pending[1] > 5):
                self._pending.pop(key, None)
        for key in [k for k, (_, ts) in self._pending.items() if now - ts > 5]:
            self._pending.pop(key, None)

    def get_dp(self, dp_id: int | str) -> Any:
        """Current (optimistic) value of a DP."""
        key = str(dp_id)
        pending = self._pending.get(key)
        if pending and time.monotonic() - pending[1] <= 5:
            return pending[0]
        return self.dps.get(key)

    def dp_id_for(self, code: str) -> int | None:
        """DP id for a code from the schema."""
        spec = self.info.codes.get(code)
        return spec.dp_id if spec else None

    def dp_type(self, dp_id: int) -> DpType | None:
        """Declared type of a DP."""
        spec = self.info.dp_schema.get(dp_id)
        return spec.type if spec else None

    # ------------------------------------------------------------------ commands

    async def async_set_dps(self, dps: dict[int | str, Any]) -> None:
        """Write DPs over the LAN. Raises TuyaLocalError when not possible."""
        link = self._link
        if link is None or not link.connected:
            raise TuyaConnectionError("device is not connected")
        payload = {str(k): v for k, v in dps.items()}
        now = time.monotonic()
        for key, value in payload.items():
            self._pending[key] = (value, now)
        self._notify()
        try:
            await link.set_dps(payload)
        except TuyaLocalError:
            for key in payload:
                self._pending.pop(key, None)
            self._notify()
            raise
        # Many devices answer CONTROL with an ACK and push STATUS right after;
        # some (esp. 3.1) never push, so fetch once to be safe.
        if link.version < 3.3:
            with contextlib.suppress(TuyaLocalError):
                self._apply_dps(await link.status())
                self._notify()

    async def async_reconnect(self) -> None:
        """Drop the current link (if any) and reconnect at once."""
        self._disconnected.set()
        self._wake.set()

    async def async_request_refresh(self) -> None:
        """Poll now."""
        link = self._link
        if link is None:
            self._wake.set()
            return
        try:
            self._apply_dps(await link.status())
            self.last_update = time.time()
            self._notify()
        except TuyaLocalError as err:
            self.last_error = f"refresh failed: {err}"
            self._disconnected.set()

    # ------------------------------------------------------------------ diagnostics

    def as_diagnostic_dict(self) -> dict[str, Any]:
        """State summary."""
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "version": self.version,
            "available": self.available,
            "connected": self.connected,
            "last_error": self.last_error,
            "last_update": self.last_update,
            "dps": self.dps,
            "schema": [spec.as_dict() for spec in self.info.dp_schema.values()],
        }
