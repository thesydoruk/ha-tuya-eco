"""Shared platform setup: create entities for existing devices and for devices added later."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SIGNAL_NEW_DEVICES
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import EntityDef

EntityFactory = Callable[[EcoHub, EcoDevice, EntityDef], Entity]


@callback
def async_setup_platform_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    platform: Platform,
    factory: EntityFactory,
) -> None:
    """Wire a platform to the hub."""
    hub: EcoHub = entry.runtime_data
    created: set[str] = set()

    @callback
    def _add(device_ids: list[str]) -> None:
        # Entities are added per home subentry so the UI groups them with their device.
        groups: dict[str | None, list[Entity]] = {}
        for device_id in device_ids:
            device = hub.devices.get(device_id)
            if device is None:
                continue
            for definition in hub.entity_defs(device):
                if definition.platform != platform:
                    continue
                uid = f"{device.id}_{definition.key}"
                if uid in created:
                    continue
                created.add(uid)
                groups.setdefault(hub.subentry_for(device), []).append(factory(hub, device, definition))
        for subentry_id, entities in groups.items():
            if subentry_id is None:
                async_add_entities(entities)
                continue
            try:
                async_add_entities(entities, config_subentry_id=subentry_id)
            except TypeError:  # Home Assistant < 2025.3
                async_add_entities(entities)

    _add(list(hub.devices))
    entry.async_on_unload(async_dispatcher_connect(hass, f"{SIGNAL_NEW_DEVICES}_{entry.entry_id}", _add))
