"""Siren platform."""

from __future__ import annotations

from typing import Any

from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .platform_setup import async_setup_platform_entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up sirens."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.SIREN, EcoSiren)


class EcoSiren(EcoEntity, SirenEntity):
    """A boolean alarm switch."""

    _attr_supported_features = SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF

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
