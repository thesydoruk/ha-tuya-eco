"""Number platform."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import EntityDef, IntegerType
from .platform_setup import async_setup_platform_entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up numbers."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.NUMBER, EcoNumber)


class EcoNumber(EcoEntity, NumberEntity):
    """Writable integer DP."""

    _attr_mode = NumberMode.BOX

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        self._type = IntegerType.from_values(definition.meta)
        if self._type.max <= self._type.min:
            self._type.max = max(self._type.min + 1, 1000)
        self._attr_native_min_value = self._type.min_scaled
        self._attr_native_max_value = self._type.max_scaled
        self._attr_native_step = self._type.step_scaled
        if definition.unit:
            self._attr_native_unit_of_measurement = definition.unit
        if self._type.max - self._type.min <= 100 * self._type.step:
            self._attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> float | None:
        """Value."""
        return self._type.scale_value(self.dp_value("value"))

    async def async_set_native_value(self, value: float) -> None:
        """Write."""
        await self.async_write("value", self._type.scale_back(value))
