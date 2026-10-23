"""Switch platform."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .platform_setup import async_setup_platform_entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up switches."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.SWITCH, EcoSwitch)


class EcoSwitch(EcoEntity, SwitchEntity):
    """A boolean DP."""

    @property
    def is_on(self) -> bool | None:
        """State."""
        value = self.value("switch")
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """On."""
        await self.async_write("switch", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Off."""
        await self.async_write("switch", False)
