"""Humidifier / dehumidifier platform."""

from __future__ import annotations

from homeassistant.components.humidifier import HumidifierDeviceClass, HumidifierEntity, HumidifierEntityFeature
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
    """Set up humidifiers."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.HUMIDIFIER, EcoHumidifier)


class EcoHumidifier(EcoEntity, HumidifierEntity):
    """Tuya humidifier (jsq) or dehumidifier (cs)."""

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        meta = definition.meta
        self._attr_device_class = HumidifierDeviceClass(meta.get("device_class", "humidifier"))
        self._target = IntegerType.from_values(meta.get("target")) if "target" in definition.dps else None
        self._current = IntegerType.from_values(meta.get("current")) if "current" in definition.dps else None
        if self._target is not None:
            self._attr_min_humidity = int(round(self._target.min_scaled))
            self._attr_max_humidity = (
                int(round(self._target.max_scaled)) if self._target.max > self._target.min else 100
            )
        features = HumidifierEntityFeature(0)
        if "mode" in definition.dps:
            features |= HumidifierEntityFeature.MODES
            self._attr_available_modes = [str(m) for m in meta.get("modes", [])]
        self._attr_supported_features = features

    @property
    def is_on(self) -> bool | None:
        """On."""
        value = self.value("switch")
        return None if value is None else bool(value)

    @property
    def current_humidity(self) -> float | None:
        """Measured humidity."""
        if self._current is None:
            return None
        return self._current.scale_value(self.value("current"))

    @property
    def target_humidity(self) -> float | None:
        """Target humidity."""
        if self._target is None:
            return None
        return self._target.scale_value(self.value("target"))

    @property
    def mode(self) -> str | None:
        """Mode."""
        value = self.value("mode")
        return None if value is None else str(value)

    async def async_set_humidity(self, humidity: int) -> None:
        """Target."""
        if self._target is not None:
            await self.async_write("target", self._target.scale_back(humidity))

    async def async_set_mode(self, mode: str) -> None:
        """Mode."""
        await self.async_write("mode", mode)

    async def async_turn_on(self, **kwargs: object) -> None:
        """On."""
        await self.async_write("switch", True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Off."""
        await self.async_write("switch", False)
