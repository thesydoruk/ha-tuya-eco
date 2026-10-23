"""LAN discovery of Tuya devices.

Devices announce themselves by UDP broadcast:
  * port 6666 – protocol 3.1, plain JSON
  * port 6667 – protocol 3.2+ , AES-ECB with a well-known key
  * port 7000 – protocol 3.5 replies (6699 frame, AES-GCM) to an active probe

3.4/3.5 devices are mostly silent until they receive a probe on port 7000, so a
probe is broadcast periodically. One listener instance is shared by all hubs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
import json
import logging
import socket
import sys
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from tinytuya import PREFIX_6699_VALUE, REQ_DEVINFO, TuyaMessage, decrypt_udp, pack_message, udpkey

_LOGGER = logging.getLogger(__name__)

UDP_PORTS = (6666, 6667, 7000)
PROBE_PORT = 7000
PROBE_INTERVAL = 30.0
STALE_AFTER = 180.0


@dataclass(slots=True)
class DiscoveredDevice:
    """One device seen on the LAN."""

    id: str
    ip: str
    version: str | None
    product_key: str | None
    last_seen: float

    @property
    def age(self) -> float:
        """Seconds since the last broadcast."""
        return time.monotonic() - self.last_seen


DiscoveryCallback = Callable[[DiscoveredDevice], None]


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, owner: TuyaDiscovery, port: int) -> None:
        self._owner = owner
        self._port = port

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._owner.handle_datagram(data, addr, self._port)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("discovery socket error on %s: %s", self._port, exc)


class TuyaDiscovery:
    """Shared UDP listener + active prober."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        self.hass = hass
        self.devices: dict[str, DiscoveredDevice] = {}
        self._transports: list[asyncio.DatagramTransport] = []
        self._listeners: dict[str | None, list[DiscoveryCallback]] = {}
        self._probe_task: asyncio.Task[None] | None = None
        self._probe_sock: socket.socket | None = None
        self._users = 0
        self._started = False

    # ------------------------------------------------------------------ lifecycle

    async def async_acquire(self) -> None:
        """Start (ref-counted)."""
        self._users += 1
        if not self._started:
            await self._async_start()

    async def async_release(self) -> None:
        """Stop when the last user releases."""
        self._users = max(0, self._users - 1)
        if self._users == 0 and self._started:
            await self._async_stop()

    async def _async_start(self) -> None:
        loop = asyncio.get_running_loop()
        for port in UDP_PORTS:
            try:
                kwargs: dict[str, Any] = {"local_addr": ("0.0.0.0", port), "allow_broadcast": True}
                if sys.platform != "win32":
                    kwargs["reuse_port"] = True
                transport, _ = await loop.create_datagram_endpoint(lambda port=port: _UdpProtocol(self, port), **kwargs)
                self._transports.append(transport)  # type: ignore[arg-type]
            except OSError as err:
                _LOGGER.warning("Cannot listen for Tuya broadcasts on UDP %s: %s", port, err)
        self._started = True
        self._probe_task = loop.create_task(self._probe_loop())
        _LOGGER.debug("discovery started on %d sockets", len(self._transports))

    async def _async_stop(self) -> None:
        self._started = False
        if self._probe_task:
            self._probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._probe_task
            self._probe_task = None
        for transport in self._transports:
            transport.close()
        self._transports.clear()
        if self._probe_sock:
            self._probe_sock.close()
            self._probe_sock = None

    # ------------------------------------------------------------------ listeners

    @callback
    def async_add_listener(self, cb: DiscoveryCallback, device_id: str | None = None) -> Callable[[], None]:
        """Subscribe to broadcasts of one device (or all with ``None``)."""
        self._listeners.setdefault(device_id, []).append(cb)

        def _unsub() -> None:
            with contextlib.suppress(ValueError):
                self._listeners[device_id].remove(cb)

        return _unsub

    def get(self, device_id: str) -> DiscoveredDevice | None:
        """Last broadcast of a device, if any."""
        return self.devices.get(device_id)

    # ------------------------------------------------------------------ receive

    def handle_datagram(self, data: bytes, addr: tuple[str, int], port: int) -> None:
        """Decode one broadcast."""
        try:
            text = decrypt_udp(data)
        except Exception:  # noqa: BLE001
            try:
                text = data[20:-8].decode()
            except Exception:  # noqa: BLE001
                return
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict) or payload.get("from") == "app":
            return
        dev_id = payload.get("gwId") or payload.get("id")
        if not dev_id:
            return
        ip = payload.get("ip") or addr[0]
        version = payload.get("version")
        found = DiscoveredDevice(
            id=str(dev_id),
            ip=str(ip),
            version=str(version) if version else None,
            product_key=payload.get("productKey"),
            last_seen=time.monotonic(),
        )
        previous = self.devices.get(found.id)
        self.devices[found.id] = found
        if previous is None or previous.ip != found.ip:
            _LOGGER.debug("discovered %s at %s (v%s, port %s)", found.id, found.ip, found.version, port)
        for cb in list(self._listeners.get(found.id, [])) + list(self._listeners.get(None, [])):
            try:
                cb(found)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("discovery listener failed")

    # ------------------------------------------------------------------ probe

    async def _probe_loop(self) -> None:
        while True:
            try:
                await self._async_send_probe()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("discovery probe failed: %s", err)
            await asyncio.sleep(PROBE_INTERVAL)

    async def _async_send_probe(self) -> None:
        from homeassistant.components import network  # noqa: PLC0415

        source_ip = await network.async_get_source_ip(self.hass)
        broadcasts = await network.async_get_ipv4_broadcast_addresses(self.hass)
        if not broadcasts:
            broadcasts = {"255.255.255.255"}  # type: ignore[assignment]
        payload = json.dumps({"from": "app", "ip": source_ip}).encode()
        msg = TuyaMessage(0, REQ_DEVINFO, None, payload, 0, True, PREFIX_6699_VALUE, True)
        packet = pack_message(msg, hmac_key=udpkey)
        await self.hass.async_add_executor_job(self._send_probe_sync, packet, [str(b) for b in broadcasts])

    def _send_probe_sync(self, packet: bytes, broadcasts: list[str]) -> None:
        if self._probe_sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setblocking(False)
            self._probe_sock = sock
        for bcast in broadcasts:
            try:
                self._probe_sock.sendto(packet, (bcast, PROBE_PORT))
            except OSError as err:
                _LOGGER.debug("probe to %s failed: %s", bcast, err)

    def prune(self) -> None:
        """Drop entries not seen for a long time."""
        now = time.monotonic()
        for dev_id in [d for d, v in self.devices.items() if now - v.last_seen > STALE_AFTER * 10]:
            del self.devices[dev_id]
