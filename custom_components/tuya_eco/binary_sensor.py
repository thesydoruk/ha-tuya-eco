"""Binary sensor platform."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import BitmapType, EntityDef
from .platform_setup import async_setup_platform_entities

_OFF_WORDS = {"none", "normal", "0", "false", "off", "closed", "close", "no", "idle"}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up binary sensors."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.BINARY_SENSOR, EcoBinarySensor)


class EcoBinarySensor(EcoEntity, BinarySensorEntity):
    """Boolean / enum / bitmap DP as on-off."""

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        labels = list(definition.meta.get("labels") or [])
        self._bitmap = BitmapType(labels) if labels else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """For bitmap DPs (fault codes): the names of the bits that are set."""
        if self._bitmap is None:
            return None
        value = self.dp_value("state")
        return {"active": self._bitmap.active(value) if value is not None else []}

    @property
    def is_on(self) -> bool | None:
        """State."""
        value = self.dp_value("state")
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        on_values = [str(v).lower() for v in self.definition.meta.get("on_values", [])]
        if isinstance(value, (int, float)):
            if on_values:
                return str(int(value)) in on_values
            return value != 0
        text = str(value).lower()
        if on_values:
            return text in on_values
        return text not in _OFF_WORDS
