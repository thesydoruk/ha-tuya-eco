"""Sensor platform."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

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
    """Which channel serves the device: local, cloud or offline. Always available.

    Its attributes are the device's diagnostics: the LAN host and protocol version, the
    last error, and the raw values of the DPs no entity covers (vendor DPs the cloud
    schema does not list show up here first, which is how a profile gets written).
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["local", "cloud", "offline"]

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        self._mapped = {str(dp) for dp in definition.meta.get("mapped_dps", [])}
        self._codes: dict[str, str] = {str(k): str(v) for k, v in (definition.meta.get("codes") or {}).items()}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Link details and the DPs that have no entity of their own."""
        device = self.device
        values = {str(dp): value for dp, value in device.dps.items()}
        unmapped = {
            self._codes.get(dp, f"dp_{dp}"): (value[:255] if isinstance(value, str) else value)
            for dp in sorted(values, key=lambda key: int(key) if key.isdigit() else 0)
            if dp not in self._mapped
            for value in (values[dp],)
        }
        last_update = getattr(device, "last_update", None)
        return {
            "host": getattr(device, "host", None),
            "protocol_version": getattr(device, "version", None),
            "last_error": getattr(device, "last_error", None),
            "last_update": dt_util.utc_from_timestamp(last_update).isoformat() if last_update else None,
            "unmapped_dps": unmapped,
        }

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
