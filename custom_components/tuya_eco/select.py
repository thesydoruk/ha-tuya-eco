"""Select platform."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import color as color_util
import voluptuous as vol

from . import power_on
from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import EntityDef, PowerMemory
from .mapping.codes import POWER_MEMORY_MODES
from .platform_setup import async_setup_platform_entities

SERVICE_SET_POWER_ON_STATE = "set_power_on_state"
ATTR_RGB_COLOR = "rgb_color"
ATTR_BRIGHTNESS_PCT = "brightness_pct"
ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"

SET_POWER_ON_STATE_SCHEMA: dict[Any, Any] = {
    vol.Optional(ATTR_RGB_COLOR): vol.All(vol.Coerce(tuple), vol.ExactSequence((cv.byte, cv.byte, cv.byte))),
    vol.Optional(ATTR_BRIGHTNESS_PCT): vol.All(vol.Coerce(float), vol.Range(min=1, max=100)),
    vol.Optional(ATTR_COLOR_TEMP_KELVIN): vol.All(vol.Coerce(int), vol.Range(min=1000, max=10000)),
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up selects."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.SELECT, EcoSelect)
    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_SET_POWER_ON_STATE, SET_POWER_ON_STATE_SCHEMA, "async_set_power_on_state"
    )


def scene_number(data: Any) -> int | None:
    """Scene number of scene data: the first byte of the LAN hex string.

    The cloud reports ``scene_data_v2`` as JSON whose ``scene_num`` counts from 1.
    """
    if isinstance(data, str) and data.lstrip().startswith("{"):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    try:
        if isinstance(data, dict):
            return int(data["scene_num"]) - 1
        if isinstance(data, str):
            return int(data[:2], 16)
    except (KeyError, TypeError, ValueError):
        return None
    return None


class EcoSelect(EcoEntity, SelectEntity):
    """Writable enum DP."""

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        # Light scenes: option -> hex scene data, matched on the scene number (first byte).
        self._scenes: dict[str, str] = dict(definition.meta.get("scenes") or {})
        # Light power-on behaviour: raw power_memory DP, options are the mode names.
        self._power_memory = bool(definition.meta.get("power_memory"))
        self._attr_options = list(self._scenes) or [str(o) for o in definition.meta.get("options", [])]

    @property
    def current_option(self) -> str | None:
        """Current."""
        value = self.dp_value("value")
        if self._scenes:
            number = scene_number(value) if self.dp_value("mode") == "scene" else None
            if number is None:
                return None
            return next((name for name, data in self._scenes.items() if scene_number(data) == number), None)
        if self._power_memory:
            memory = power_on.stored(self)
            return POWER_MEMORY_MODES.get(memory.mode) if memory else None
        if value is None:
            return None
        text = str(value)
        if text not in self._attr_options:
            # Some devices report values the cloud schema does not list (e.g. "off"
            # instead of "power_off"): expose them rather than showing "unknown".
            self._attr_options = [*self._attr_options, text]
        return text

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The stored custom power-on state."""
        return power_on.attributes(self) if self._power_memory else None

    async def async_select_option(self, option: str) -> None:
        """Write."""
        if self._scenes:
            mode_dp, scene_dp = self.dp("mode"), self.dp("value")
            assert mode_dp is not None and scene_dp is not None
            await self.async_write_dps({mode_dp: "scene", scene_dp: self._scenes[option]})
            return
        if self._power_memory:
            if option == "custom":
                memory = power_on.snapshot(self)
            else:
                # Only the mode byte changes: the stored custom state is kept.
                memory = power_on.stored(self) or PowerMemory()
                memory.mode = next(mode for mode, name in POWER_MEMORY_MODES.items() if name == option)
            await self.async_write("value", memory.encode())
            return
        await self.async_write("value", option)

    async def async_set_power_on_state(
        self,
        rgb_color: tuple[int, int, int] | None = None,
        brightness_pct: float | None = None,
        color_temp_kelvin: int | None = None,
    ) -> None:
        """Store an explicit custom power-on state and switch the light to it."""
        if not self._power_memory:
            raise ServiceValidationError(f"{self.entity_id} is not a power-on behavior select")
        memory = power_on.explicit(
            self,
            hs_color=color_util.color_RGB_to_hs(*rgb_color) if rgb_color is not None else None,
            brightness_pct=brightness_pct,
            color_temp_kelvin=color_temp_kelvin,
        )
        await self.async_write("value", memory.encode())
