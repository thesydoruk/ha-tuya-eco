"""Tuya Eco: modular local control for the Tuya ecosystem."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN, PLATFORMS
from .hub import EcoHub
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

type EcoConfigEntry = ConfigEntry[EcoHub]


async def async_setup_entry(hass: HomeAssistant, entry: EcoConfigEntry) -> bool:
    """Set up one provider account."""
    async_register_services(hass)
    hub = EcoHub(hass, entry)
    await hub.async_setup()
    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["options_snapshot_" + entry.entry_id] = dict(entry.options)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: EcoConfigEntry) -> None:
    """Reload only when options (not cached data) changed."""
    snapshot = hass.data.get(DOMAIN, {}).get("options_snapshot_" + entry.entry_id)
    if snapshot != dict(entry.options):
        await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: EcoConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow deleting a device from the UI; it comes back only if the cloud still lists it."""
    hub = entry.runtime_data
    for domain, dev_id in device_entry.identifiers:
        if domain == DOMAIN:
            if dev_id in hub.cloud_devices:
                _LOGGER.info("%s removed from the UI; it will reappear if it is still in the cloud account", dev_id)
            await hub.async_remove_device(dev_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EcoConfigEntry) -> bool:
    """Unload."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_unload()
        hass.data.get(DOMAIN, {}).pop("options_snapshot_" + entry.entry_id, None)
    return ok
