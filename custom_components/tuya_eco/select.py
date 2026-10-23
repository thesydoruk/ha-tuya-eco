"""Select platform."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import EntityDef
from .platform_setup import async_setup_platform_entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up selects."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.SELECT, EcoSelect)


class EcoSelect(EcoEntity, SelectEntity):
    """Writable enum DP."""

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        self._attr_options = [str(o) for o in definition.meta.get("options", [])]

    @property
    def current_option(self) -> str | None:
        """Current."""
        value = self.dp_value("value")
        if value is None:
            return None
        text = str(value)
        return text if text in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        """Write."""
        await self.async_write("value", option)
