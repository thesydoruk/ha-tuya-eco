"""Button platform (write-only boolean actions)."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import power_on
from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import EntityDef
from .platform_setup import async_setup_platform_entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up buttons."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.BUTTON, EcoButton)


class EcoButton(EcoEntity, ButtonEntity):
    """Pressing writes True (or ``press_value`` from meta); a power-memory button stores the light state."""

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        if definition.device_class:
            self._attr_device_class = ButtonDeviceClass(definition.device_class)

    async def async_press(self) -> None:
        """Press."""
        if self.definition.meta.get("power_memory"):
            # Store the light's current state as its custom power-on state.
            await self.async_write("value", power_on.snapshot(self).encode())
            return
        await self.async_write("press", self.definition.meta.get("press_value", True))
