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
    """A boolean DP (or a string DP with explicit ``on_value`` / ``off_value`` in meta)."""

    @property
    def is_on(self) -> bool | None:
        """State."""
        value = self.dp_value("switch")
        if value is None:
            return None
        if "on_value" in self.definition.meta:
            return value == self.definition.meta["on_value"]
        return bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """On."""
        await self.async_write("switch", self.definition.meta.get("on_value", True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Off."""
        await self.async_write("switch", self.definition.meta.get("off_value", False))
