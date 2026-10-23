"""Select platform."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .hub import EcoHub
from .light import decode_colour
from .local.device import EcoDevice
from .mapping import EntityDef, PowerMemory
from .mapping.codes import POWER_MEMORY_MODES
from .platform_setup import async_setup_platform_entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up selects."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.SELECT, EcoSelect)


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
            if self.dp_value("mode") != "scene" or not isinstance(value, str):
                return None
            return next((name for name, data in self._scenes.items() if data[:2] == value[:2].lower()), None)
        if self._power_memory:
            memory = PowerMemory.decode(value)
            return POWER_MEMORY_MODES.get(memory.mode) if memory else None
        if value is None:
            return None
        text = str(value)
        if text not in self._attr_options:
            # Some devices report values the cloud schema does not list (e.g. "off"
            # instead of "power_off"): expose them rather than showing "unknown".
            self._attr_options = [*self._attr_options, text]
        return text

    async def async_select_option(self, option: str) -> None:
        """Write."""
        if self._scenes:
            mode_dp, scene_dp = self.dp("mode"), self.dp("value")
            assert mode_dp is not None and scene_dp is not None
            await self.async_write_dps({mode_dp: "scene", scene_dp: self._scenes[option]})
            return
        if self._power_memory:
            await self.async_write("value", self._power_memory_payload(option).encode())
            return
        await self.async_write("value", option)

    def _power_memory_payload(self, option: str) -> PowerMemory:
        """The current payload with the mode changed; ``custom`` snapshots the light's state."""
        memory = PowerMemory.decode(self.dp_value("value")) or PowerMemory()
        memory.mode = next(mode for mode, name in POWER_MEMORY_MODES.items() if name == option)
        if option != "custom":
            return memory
        brightness = self.dp_value("brightness")
        if isinstance(brightness, (int, float)):
            memory.brightness = int(brightness)
        temp = self.dp_value("color_temp")
        if isinstance(temp, (int, float)):
            memory.color_temp = int(temp)
        colour_meta = self.definition.meta.get("color") or {}
        hsv = colour_meta.get("hsv") or {}
        decoded = decode_colour(
            self.dp_value("color"),
            bool(colour_meta.get("v2", True)),
            int((hsv.get("s") or {}).get("max") or 0),
            int((hsv.get("v") or {}).get("max") or 0),
        )
        if decoded:
            h, sat, val = decoded
            memory.hue, memory.saturation, memory.value = int(round(h)), int(round(sat * 1000)), int(round(val * 1000))
        return memory
