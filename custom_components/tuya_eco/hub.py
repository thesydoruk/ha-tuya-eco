"""EcoHub: per config-entry manager tying provider, discovery and devices together."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import inspect
import logging
import time
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigEntry

try:  # Home Assistant ≥ 2025.3
    from homeassistant.config_entries import ConfigSubentry
except ImportError:  # pragma: no cover - older cores
    ConfigSubentry = None  # type: ignore[assignment,misc]
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
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
    OPT_HOMES,
    OPT_POLL_INTERVAL,
    SIGNAL_NEW_DEVICES,
)
from .local.device import EcoDevice
from .local.discovery import TuyaDiscovery
from .mapping import EntityDef, build_entities, synthesize_schema
from .mapping.profiles import preload_profiles
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
        self.provider.selected_homes = self.selected_homes
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
        # Devices skipped because another tuya_eco entry already controls them: id → that entry's title.
        self.conflicts: dict[str, str] = {}
        # home id → config subentry id (one subentry per home groups devices in the UI)
        self._subentry_by_home: dict[str, str] = {}

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
    def selected_homes(self) -> set[str] | None:
        """Home ids to include (``None`` = all)."""
        homes = self.entry.options.get(OPT_HOMES)
        return {str(h) for h in homes} if homes else None

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

        await self.hass.async_add_executor_job(preload_profiles)
        self._async_sync_home_subentries()
        await self.discovery.async_acquire()
        for info in sorted(self.cloud_devices.values(), key=lambda i: bool(i.gateway_id)):
            self._ensure_device(info)
        await asyncio.gather(*(d.async_start() for d in self.devices.values()))
        self._async_purge_stale_entities()

        hours = float(self.entry.options.get(OPT_CLOUD_REFRESH_HOURS, DEFAULT_CLOUD_REFRESH_HOURS))
        if hours > 0:
            self._unsub_timer = async_track_time_interval(self.hass, self._scheduled_refresh, timedelta(hours=hours))

    @callback
    def _async_purge_stale_entities(self) -> int:
        """Remove registry entries of this entry whose unique_id is no longer generated.

        Keys change when a DP disappears from the schema, when a schema-less device
        finally gets its real codes, or when a newer version renames a key; without
        this, such entities would linger as "unavailable" forever. Devices whose
        entity set is not known yet (no schema, never connected) are left alone, and
        so are disabled devices.
        """
        registry = er.async_get(self.hass)
        expected: dict[str, set[str]] = {}
        for device in self.devices.values():
            defs = self.entity_defs(device)
            if defs:
                expected[device.id] = {f"{device.id}_{d.key}" for d in defs}
        known_ids = set(self.cloud_devices) | self.disabled_devices
        removed = 0
        for reg_entry in er.async_entries_for_config_entry(registry, self.entry.entry_id):
            uid = str(reg_entry.unique_id)
            owner = next((dev_id for dev_id in known_ids if uid.startswith(f"{dev_id}_")), None)
            if owner is None:
                stale = True  # device is not in the account any more
            elif owner in expected:
                stale = uid not in expected[owner]
            else:
                stale = False  # entity set unknown yet, keep
            if stale:
                _LOGGER.info("%s: removing stale entity %s (%s)", self.entry.title, reg_entry.entity_id, uid)
                registry.async_remove(reg_entry.entity_id)
                removed += 1
        return removed

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

    def _owned_elsewhere(self, dev_id: str) -> str | None:
        """Title of another loaded tuya_eco entry that already controls `dev_id`, if any."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id == self.entry.entry_id:
                continue
            other = getattr(entry, "runtime_data", None)
            if isinstance(other, EcoHub) and dev_id in other.devices:
                return entry.title
        return None

    def _ensure_device(self, info: CloudDevice) -> EcoDevice | None:
        if info.id in self.disabled_devices:
            return None
        if info.id not in self.devices and (owner := self._owned_elsewhere(info.id)):
            # Tuya devices accept a single LAN session: two entries fighting over one
            # device would make it flap, and the entities would collide anyway.
            if self.conflicts.get(info.id) != owner:
                _LOGGER.warning(
                    "%s: device %s (%s) is already controlled by the entry '%s'; skipping it here",
                    self.entry.title,
                    info.name,
                    info.id,
                    owner,
                )
            self.conflicts[info.id] = owner
            return None
        self.conflicts.pop(info.id, None)
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
        extra: dict[str, Any] = {}
        if info.gateway_id:
            gateway = registry.async_get_device(identifiers={(DOMAIN, info.gateway_id)})
            if gateway is not None:
                extra["via_device_id"] = gateway.id
        subentry_id = self._subentry_by_home.get(info.home_id) if info.home_id else None
        existing = registry.async_get_device(identifiers={(DOMAIN, info.id)})
        # A new device is created straight in its home; an existing one is moved
        # explicitly below (re-registering it under another subentry is deprecated).
        if subentry_id and existing is None:
            extra["config_subentry_id"] = subentry_id
        device_entry = registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, info.id)},
            manufacturer=self.provider.name.split(" (")[0],
            model=info.product_name or info.product_id or None,
            model_id=info.product_id or None,
            name=info.name,
            **extra,
        )
        self._async_move_to_subentry(registry, device_entry, subentry_id)

    def subentry_for(self, device: EcoDevice) -> str | None:
        """Config subentry (home) a device's entities belong to."""
        return self._subentry_by_home.get(device.info.home_id) if device.info.home_id else None

    @callback
    def _async_sync_home_subentries(self, devices: list[CloudDevice] | None = None) -> None:
        """One config subentry per home so the integration page groups devices by home.

        Subentries exist since Home Assistant 2025.3; older cores keep a flat list.
        """
        if ConfigSubentry is None or not hasattr(self.hass.config_entries, "async_add_subentry"):
            return
        source = devices if devices is not None else list(self.cloud_devices.values())
        homes: dict[str, str] = {}
        for info in source:
            if info.home_id:
                homes[info.home_id] = info.home_name or f"Home {info.home_id}"
        if not homes:
            return
        existing = {sub.unique_id: sub for sub in self.entry.subentries.values() if sub.subentry_type == "home"}
        for home_id, name in homes.items():
            sub = existing.get(home_id)
            if sub is None:
                sub = ConfigSubentry(data=MappingProxyType({}), subentry_type="home", title=name, unique_id=home_id)
                self.hass.config_entries.async_add_subentry(self.entry, sub)
            elif sub.title != name:
                self.hass.config_entries.async_update_subentry(self.entry, sub, title=name)
            self._subentry_by_home[home_id] = sub.subentry_id
        for home_id, sub in existing.items():
            if home_id not in homes and home_id is not None:
                self.hass.config_entries.async_remove_subentry(self.entry, sub.subentry_id)
                self._subentry_by_home.pop(home_id, None)

    def _async_move_to_subentry(self, registry: dr.DeviceRegistry, device_entry: Any, subentry_id: str | None) -> None:
        """Make sure an already registered device sits in its home's subentry.

        Works with both device models: the single-owner one (``config_subentry_id`` +
        ``new_config_subentry_id``, HA ≥ 2026.8) and the older link set.
        """
        if subentry_id is None:
            return
        try:
            if (
                hasattr(device_entry, "config_subentry_id")
                and "new_config_subentry_id" in inspect.signature(registry.async_update_device).parameters
            ):
                if device_entry.config_subentry_id != subentry_id:
                    registry.async_update_device(device_entry.id, new_config_subentry_id=subentry_id)
                return
            links = getattr(device_entry, "config_entries_subentries", {}).get(self.entry.entry_id, set())
            if None in links and subentry_id in links:
                registry.async_update_device(
                    device_entry.id, remove_config_entry_id=self.entry.entry_id, remove_config_subentry_id=None
                )
        except Exception as err:  # noqa: BLE001 - grouping is cosmetic, never break setup
            _LOGGER.debug("%s: could not move device to its home subentry: %s", device_entry.id, err)

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
            self.provider.selected_homes = self.selected_homes
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
            await self._async_apply_cloud_devices(devices)
            return True

    async def _async_apply_cloud_devices(self, devices: list[CloudDevice]) -> None:
        new_ids: list[str] = []
        seen = {info.id for info in devices}
        for info in devices:
            previous = self.cloud_devices.get(info.id)
            if previous and not info.home_id:
                info.home_id, info.home_name = previous.home_id, previous.home_name
        self._async_sync_home_subentries(devices)
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
        # Devices that disappeared from the account are removed (with their entities).
        # An empty list while we knew devices is treated as a glitch, not as "all removed".
        gone = [dev_id for dev_id in self.cloud_devices if dev_id not in seen]
        if gone and seen:
            for dev_id in gone:
                await self.async_remove_device(dev_id)
        elif gone:
            _LOGGER.warning("%s: cloud returned no devices; keeping the %d cached ones", self.entry.title, len(gone))
        self._schedule_persist()
        if new_ids:
            async_dispatcher_send(self.hass, f"{SIGNAL_NEW_DEVICES}_{self.entry.entry_id}", new_ids)

    async def async_remove_device(self, dev_id: str) -> None:
        """Forget a device: stop its link, drop caches, remove it from the device registry."""
        info = self.cloud_devices.pop(dev_id, None)
        device = self.devices.pop(dev_id, None)
        if device is not None:
            await device.async_stop()
        self._entity_defs.pop(dev_id, None)
        self._hosts.pop(dev_id, None)
        self._versions.pop(dev_id, None)
        registry = dr.async_get(self.hass)
        reg_entry = registry.async_get_device(identifiers={(DOMAIN, dev_id)})
        if reg_entry is not None:
            registry.async_remove_device(reg_entry.id)
        _LOGGER.info("%s: removed device %s (%s)", self.entry.title, info.name if info else dev_id, dev_id)
        self._schedule_persist()

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
