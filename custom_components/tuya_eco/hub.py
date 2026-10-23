"""EcoHub: per config-entry manager tying provider, discovery and devices together."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_DEVICES_CACHE,
    CONF_HOSTS,
    CONF_PROVIDER,
    CONF_PROVIDER_DATA,
    CONF_VERSIONS,
    DEFAULT_CLOUD_FALLBACK,
    DEFAULT_CLOUD_REFRESH_HOURS,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    KEY_REFRESH_COOLDOWN,
    OPT_CLOUD_FALLBACK,
    OPT_CLOUD_REFRESH_HOURS,
    OPT_DISABLED_DEVICES,
    OPT_POLL_INTERVAL,
    SIGNAL_NEW_DEVICES,
)
from .local.device import EcoDevice
from .local.discovery import TuyaDiscovery
from .mapping import EntityDef, build_entities, synthesize_schema
from .providers.base import BaseProvider, CloudDevice, ProviderAuthError, ProviderError
from .providers.registry import get_provider_class

_LOGGER = logging.getLogger(__name__)

type EcoConfigEntry = ConfigEntry[EcoHub]


def get_discovery(hass: HomeAssistant) -> TuyaDiscovery:
    """Return the shared discovery service."""
    data = hass.data.setdefault(DOMAIN, {})
    if "discovery" not in data:
        data["discovery"] = TuyaDiscovery(hass)
    return data["discovery"]


class EcoHub:
    """Manages all devices of one provider account."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.entry = entry
        self.provider: BaseProvider = get_provider_class(entry.data[CONF_PROVIDER]).from_entry_data(
            hass, entry.data[CONF_PROVIDER_DATA]
        )
        self.discovery = get_discovery(hass)
        self.devices: dict[str, EcoDevice] = {}
        self.cloud_devices: dict[str, CloudDevice] = {}
        self._entity_defs: dict[str, list[EntityDef]] = {}
        self._hosts: dict[str, str] = dict(entry.data.get(CONF_HOSTS) or {})
        self._versions: dict[str, float] = {k: float(v) for k, v in (entry.data.get(CONF_VERSIONS) or {}).items()}
        self._last_cloud_refresh = 0.0
        self._refresh_lock = asyncio.Lock()
        self._unsub_timer: Any = None
        self._persist_handle: asyncio.TimerHandle | None = None
        self.last_cloud_error: str | None = None

    # ------------------------------------------------------------------ options

    @property
    def poll_interval(self) -> float:
        """Seconds between full polls."""
        return float(self.entry.options.get(OPT_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))

    @property
    def cloud_fallback(self) -> bool:
        """Send commands through the cloud when the LAN link is down."""
        return bool(self.entry.options.get(OPT_CLOUD_FALLBACK, DEFAULT_CLOUD_FALLBACK))

    @property
    def disabled_devices(self) -> set[str]:
        """Devices the user excluded."""
        return set(self.entry.options.get(OPT_DISABLED_DEVICES) or [])

    # ------------------------------------------------------------------ lifecycle

    async def async_setup(self) -> None:
        """Load devices (cloud or cache) and start them."""
        if hasattr(self.provider, "on_token_update"):
            self.provider.on_token_update = self._provider_token_updated  # type: ignore[attr-defined]

        cached = [CloudDevice.from_dict(d) for d in self.entry.data.get(CONF_DEVICES_CACHE) or []]
        self.cloud_devices = {d.id: d for d in cached}

        try:
            await self.async_refresh_cloud(force=True, raise_errors=True)
        except ProviderAuthError as err:
            if not cached:
                raise ConfigEntryAuthFailed(str(err)) from err
            _LOGGER.warning("%s: cloud auth failed (%s); using cached devices", self.entry.title, err)
            self.entry.async_start_reauth(self.hass)
        except ProviderError as err:
            if not cached:
                raise ConfigEntryNotReady(f"cloud unavailable and no cached devices: {err}") from err
            _LOGGER.warning("%s: cloud unavailable (%s); using cached devices", self.entry.title, err)

        await self.discovery.async_acquire()
        for info in self.cloud_devices.values():
            self._ensure_device(info)
        await asyncio.gather(*(d.async_start() for d in self.devices.values()))

        hours = float(self.entry.options.get(OPT_CLOUD_REFRESH_HOURS, DEFAULT_CLOUD_REFRESH_HOURS))
        if hours > 0:
            self._unsub_timer = async_track_time_interval(self.hass, self._scheduled_refresh, timedelta(hours=hours))

    async def async_unload(self) -> None:
        """Stop everything."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self._persist_handle:
            self._persist_handle.cancel()
            self._persist_now()
        await asyncio.gather(*(d.async_stop() for d in self.devices.values()), return_exceptions=True)
        self.devices.clear()
        await self.discovery.async_release()
        await self.provider.async_close()

    # ------------------------------------------------------------------ devices

    def _gateway_for(self, info: CloudDevice) -> CloudDevice | None:
        if info.gateway_id and info.gateway_id in self.cloud_devices:
            return self.cloud_devices[info.gateway_id]
        return None

    def _ensure_device(self, info: CloudDevice) -> EcoDevice | None:
        if info.id in self.disabled_devices:
            return None
        if not info.local_key and not (self._gateway_for(info) and self._gateway_for(info).local_key):  # type: ignore[union-attr]
            _LOGGER.debug("%s: no local key, skipping", info.name)
            return None
        device = self.devices.get(info.id)
        if device:
            device.update_info(info, self._gateway_for(info))
            return device
        device = EcoDevice(
            self.hass,
            info,
            self.discovery,
            poll_interval=self.poll_interval,
            host=self._hosts.get(info.id),
            version=self._versions.get(info.id),
            gateway=self._gateway_for(info),
            on_key_invalid=self._device_key_invalid,
            on_host_changed=self._device_host_changed,
        )
        self.devices[info.id] = device
        device.async_add_listener(lambda dev=device: self._device_state_changed(dev))
        self._register_device(info)
        return device

    def _register_device(self, info: CloudDevice) -> None:
        registry = dr.async_get(self.hass)
        registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, info.id)},
            manufacturer=self.provider.name.split(" (")[0],
            model=info.product_name or info.product_id or None,
            model_id=info.product_id or None,
            name=info.name,
            via_device=(DOMAIN, info.gateway_id) if info.gateway_id else None,
        )

    def entity_defs(self, device: EcoDevice) -> list[EntityDef]:
        """Entity definitions for a device (cached)."""
        if device.id not in self._entity_defs:
            if not device.info.dp_schema and device.dps:
                device.info.dp_schema = synthesize_schema(device.dps)
            if not device.info.dp_schema:
                return []
            self._entity_defs[device.id] = build_entities(device.info)
        return self._entity_defs[device.id]

    @callback
    def _device_state_changed(self, device: EcoDevice) -> None:
        # A manual device without schema: create entities once we know its DPs.
        if device.id not in self._entity_defs and device.dps and not device.info.dp_schema:
            defs = self.entity_defs(device)
            if defs:
                self.cloud_devices[device.id] = device.info
                self._schedule_persist()
                async_dispatcher_send(self.hass, f"{SIGNAL_NEW_DEVICES}_{self.entry.entry_id}", [device.id])
        if device.version and self._versions.get(device.id) != device.version:
            self._versions[device.id] = device.version
            self._schedule_persist()

    @callback
    def _device_host_changed(self, device: EcoDevice) -> None:
        if device.host:
            self._hosts[device.id] = device.host
            self._schedule_persist()

    async def _device_key_invalid(self, device: EcoDevice) -> None:
        if time.monotonic() - self._last_cloud_refresh < KEY_REFRESH_COOLDOWN:
            return
        _LOGGER.info("%s: refreshing cloud data because the local key was rejected", device.name)
        await self.async_refresh_cloud(force=False)

    # ------------------------------------------------------------------ cloud

    async def _scheduled_refresh(self, _now: Any) -> None:
        await self.async_refresh_cloud(force=False)

    async def async_refresh_cloud(self, *, force: bool, raise_errors: bool = False) -> bool:
        """Fetch devices from the provider and apply changes. Returns True on success."""
        async with self._refresh_lock:
            if not force and time.monotonic() - self._last_cloud_refresh < KEY_REFRESH_COOLDOWN:
                return False
            try:
                devices = await self.provider.async_fetch_devices()
            except ProviderAuthError as err:
                self.last_cloud_error = str(err)
                if raise_errors:
                    raise
                _LOGGER.warning("%s: cloud auth failed: %s", self.entry.title, err)
                self.entry.async_start_reauth(self.hass)
                return False
            except ProviderError as err:
                self.last_cloud_error = str(err)
                if raise_errors:
                    raise
                _LOGGER.warning("%s: cloud refresh failed: %s", self.entry.title, err)
                return False
            except Exception as err:  # noqa: BLE001
                self.last_cloud_error = str(err)
                if raise_errors:
                    raise ProviderError(str(err)) from err
                _LOGGER.exception("%s: cloud refresh crashed", self.entry.title)
                return False
            self._last_cloud_refresh = time.monotonic()
            self.last_cloud_error = None
            self._apply_cloud_devices(devices)
            return True

    def _apply_cloud_devices(self, devices: list[CloudDevice]) -> None:
        new_ids: list[str] = []
        for info in devices:
            previous = self.cloud_devices.get(info.id)
            if previous and not info.dp_schema:
                info.dp_schema = previous.dp_schema
            self.cloud_devices[info.id] = info
            if previous is not None and previous.local_key != info.local_key:
                _LOGGER.info("%s: local key changed in the cloud", info.name)
            if info.id in self.devices:
                self.devices[info.id].update_info(info, self._gateway_for(info))
                if info.dp_schema and self._entity_defs.get(info.id) == []:
                    self._entity_defs.pop(info.id, None)
            elif self.devices:  # hub already started → new device
                device = self._ensure_device(info)
                if device:
                    new_ids.append(info.id)
                    self.hass.async_create_task(device.async_start())
        # devices whose cloud entry disappeared keep running on cached data.
        self._schedule_persist()
        if new_ids:
            async_dispatcher_send(self.hass, f"{SIGNAL_NEW_DEVICES}_{self.entry.entry_id}", new_ids)

    async def async_send_cloud(self, device: EcoDevice, dps: dict[int | str, Any]) -> bool:
        """Cloud fallback for a command expressed as DP ids."""
        if not self.cloud_fallback:
            return False
        commands = []
        for dp, value in dps.items():
            spec = device.info.dp_schema.get(int(dp))
            if spec is None:
                return False
            commands.append({"code": spec.code, "value": value})
        return await self.provider.async_send_cloud_command(device.id, commands)

    # ------------------------------------------------------------------ persistence

    @callback
    def _provider_token_updated(self, _token_info: dict[str, Any]) -> None:
        self._schedule_persist()

    @callback
    def _schedule_persist(self) -> None:
        if self._persist_handle:
            self._persist_handle.cancel()
        self._persist_handle = self.hass.loop.call_later(5, self._persist_now)

    @callback
    def _persist_now(self) -> None:
        self._persist_handle = None
        if self.entry.state.recoverable is False:  # pragma: no cover - defensive
            return
        data = {
            **self.entry.data,
            CONF_PROVIDER_DATA: self.provider.to_entry_data(),
            CONF_DEVICES_CACHE: [d.as_dict() for d in self.cloud_devices.values()],
            CONF_HOSTS: dict(self._hosts),
            CONF_VERSIONS: {k: v for k, v in self._versions.items()},
        }
        if data != dict(self.entry.data):
            self.hass.config_entries.async_update_entry(self.entry, data=data)
