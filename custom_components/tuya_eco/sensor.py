"""Sensor platform."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import BitmapType, EntityDef
from .platform_setup import async_setup_platform_entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up sensors."""

    def factory(hub: EcoHub, device: EcoDevice, definition: EntityDef) -> EcoSensor:
        if definition.meta.get("type") == "connection":
            return EcoConnectionSensor(hub, device, definition)
        return EcoSensor(hub, device, definition)

    async_setup_platform_entities(hass, entry, async_add_entities, Platform.SENSOR, factory)


class EcoConnectionSensor(EcoEntity, SensorEntity):
    """Which channel serves the device: local, cloud or offline. Always available."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["local", "cloud", "offline"]

    @property
    def available(self) -> bool:
        """Shown even when the device itself is unreachable."""
        return True

    @property
    def native_value(self) -> str:
        """Transport in use."""
        return self.device.transport

    @property
    def icon(self) -> str:
        """Icon per transport."""
        return {"local": "mdi:lan-connect", "cloud": "mdi:cloud-outline", "offline": "mdi:lan-disconnect"}[
            self.device.transport
        ]


class EcoSensor(EcoEntity, SensorEntity):
    """Integer / enum / string DP."""

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        meta = definition.meta
        self._scale = float(10 ** int(meta.get("scale", 0) or 0))
        self._kind = str(meta.get("type", "Integer"))
        if definition.unit:
            self._attr_native_unit_of_measurement = definition.unit
        if definition.state_class and self._kind == "Integer":
            self._attr_state_class = SensorStateClass(definition.state_class)
        if self._kind == "Enum":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(meta.get("options", []))
        self._bitmap = BitmapType(list(meta.get("labels", []))) if self._kind == "Bitmap" else None

    @property
    def native_value(self) -> Any:
        """Value."""
        value = self.dp_value("value")
        if value is None:
            return None
        if self._kind == "Integer":
            try:
                number = float(value) / self._scale
            except (TypeError, ValueError):
                return None
            return int(number) if number.is_integer() and self._scale == 1 else round(number, 3)
        if self._kind == "Enum":
            text = str(value)
            return text if not self._attr_options or text in self._attr_options else None
        if self._bitmap is not None:
            active = self._bitmap.active(value)
            return ", ".join(active) if active else "ok" if self._bitmap.labels else str(value)
        return str(value)[:255]
