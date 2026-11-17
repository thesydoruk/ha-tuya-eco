"""Sensor platform."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
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
        kind = definition.meta.get("type")
        if kind == "connection":
            return EcoConnectionSensor(hub, device, definition)
        if kind == "ip_address":
            return EcoIpAddressSensor(hub, device, definition)
        if definition.meta.get("accumulate"):
            return EcoEnergyTotalSensor(hub, device, definition)
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
            "mac": getattr(device, "mac", None),
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


class EcoIpAddressSensor(EcoEntity, SensorEntity):
    """LAN address the device was last reached at (unknown until the first link)."""

    @property
    def available(self) -> bool:
        """Shown even when the device itself is unreachable."""
        return True

    @property
    def native_value(self) -> str | None:
        """Host."""
        return getattr(self.device, "host", None)


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


# A report that repeats the DP's current value this soon after the link came up is the
# device replaying its last report on connect, not new consumption.
REPLAY_WINDOW = 30.0


class EcoEnergyTotalSensor(EcoSensor, RestoreSensor):
    """Running total of an energy DP that reports increments (`add_ele`).

    Plugs push the energy used since their previous report every few minutes; the DP then
    keeps that value, so polls repeat it. Only pushed reports are added, the total survives
    restarts, and a replay of the last report right after a (re)connect is not counted twice.
    """

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._key = str(self.dp("value"))
        self._total = 0.0
        self._seen = 0
        self._last_value: Any = None
        self._was_available = False
        self._link_up = 0.0
        self._last_report: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the total and start from the current push counter."""
        last = await self.async_get_last_sensor_data()
        if last is not None and isinstance(last.native_value, int | float):
            self._total = float(last.native_value)
        self._seen = self.device.push_counts.get(self._key, 0)
        self._last_value = self.device.dps.get(self._key)
        self._was_available = self.device.available
        self._link_up = time.monotonic()
        await super().async_added_to_hass()

    @callback
    def _device_updated(self) -> None:
        now = time.monotonic()
        available = self.device.available
        if available and not self._was_available:
            self._link_up = now
        self._was_available = available
        value = self.device.dps.get(self._key)
        count = self.device.push_counts.get(self._key, 0)
        if count != self._seen:
            self._seen = count
            replay = value == self._last_value and now - self._link_up < REPLAY_WINDOW
            increment = self._scaled(value)
            if not replay and increment is not None and increment > 0:
                self._total += increment
                self._last_report = increment
        self._last_value = value
        self.async_write_ha_state()

    def _scaled(self, value: Any) -> float | None:
        try:
            return float(value) / self._scale
        except (TypeError, ValueError):
            return None

    @property
    def native_value(self) -> float:
        """Total since the entity was created."""
        return round(self._total, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The last report added to the total."""
        return None if self._last_report is None else {"last_report": round(self._last_report, 3)}
