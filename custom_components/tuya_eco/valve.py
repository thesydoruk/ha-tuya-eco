"""Valve platform (irrigation controllers)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.valve import ValveDeviceClass, ValveEntity, ValveEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .platform_setup import async_setup_platform_entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up valves."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.VALVE, EcoValve)


class EcoValve(EcoEntity, ValveEntity):
    """A boolean water valve."""

    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE
    _attr_reports_position = False
    _attr_device_class = ValveDeviceClass.WATER

    @property
    def is_closed(self) -> bool | None:
        """Closed."""
        value = self.value("switch")
        return None if value is None else not bool(value)

    async def async_open_valve(self, **kwargs: Any) -> None:
        """Open."""
        await self.async_write("switch", True)

    async def async_close_valve(self, **kwargs: Any) -> None:
        """Close."""
        await self.async_write("switch", False)
