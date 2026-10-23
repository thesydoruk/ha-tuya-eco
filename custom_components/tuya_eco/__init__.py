"""Tuya Eco: modular local control for the Tuya ecosystem."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

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


async def async_unload_entry(hass: HomeAssistant, entry: EcoConfigEntry) -> bool:
    """Unload."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_unload()
        hass.data.get(DOMAIN, {}).pop("options_snapshot_" + entry.entry_id, None)
    return ok
