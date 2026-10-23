"""Event platform (scene switches, doorbells).

Events fire only on *pushed* reports from the device: a polled status repeats the
last click and must not re-trigger it.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import EntityDef
from .platform_setup import async_setup_platform_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up events."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.EVENT, EcoEvent)


class EcoEvent(EcoEntity, EventEntity):
    """Fires an event for every pushed report of the DP."""

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        self._attr_event_types = [str(t) for t in definition.meta.get("event_types", ["triggered"])]
        if definition.device_class:
            self._attr_device_class = EventDeviceClass(definition.device_class)
        self._decode = definition.meta.get("decode")
        self._seen = 0

    async def async_added_to_hass(self) -> None:
        """Start from the current push counter so restarts do not replay old clicks."""
        self._seen = self.device.push_counts.get(str(self.dp("event")), 0)
        await super().async_added_to_hass()

    @callback
    def _device_updated(self) -> None:
        key = str(self.dp("event"))
        count = self.device.push_counts.get(key, 0)
        if count == self._seen:
            self.async_write_ha_state()
            return
        self._seen = count
        value = self.device.dps.get(key)
        if value is None:
            return
        attrs: dict[str, Any] = {}
        if self._decode:
            event_type = "triggered"
            attrs["message"] = self._decode_payload(value)
        else:
            event_type = str(value)
            if event_type not in self._attr_event_types:
                _LOGGER.debug("%s: unknown event value %r", self.device.name, value)
                self._attr_event_types = [*self._attr_event_types, event_type]
        self._trigger_event(event_type, attrs)
        self.async_write_ha_state()

    def _decode_payload(self, value: Any) -> str:
        try:
            if self._decode == "b64_string":
                return base64.b64decode(str(value)).decode("utf-8", "replace")
            if self._decode == "b64_raw":
                raw = value if isinstance(value, bytes) else base64.b64decode(str(value))
                return raw.decode("utf-8", "replace")
        except (ValueError, TypeError):
            pass
        return str(value)
