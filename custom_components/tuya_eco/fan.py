"""Fan platform."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import DIRECTION_FORWARD, DIRECTION_REVERSE, FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import ordered_list_item_to_percentage, percentage_to_ordered_list_item

from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import EntityDef, IntegerType
from .platform_setup import async_setup_platform_entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up fans."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.FAN, EcoFan)


class EcoFan(EcoEntity, FanEntity):
    """Fan with optional speed, direction, oscillation and presets."""

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        meta = definition.meta
        self._speed_options: list[str] = [str(o) for o in meta.get("speed_options", [])]
        self._speed = IntegerType.from_values(meta.get("speed")) if "speed" in meta else None
        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if "speed" in definition.dps:
            features |= FanEntityFeature.SET_SPEED
        if "direction" in definition.dps:
            features |= FanEntityFeature.DIRECTION
        if "oscillate" in definition.dps:
            features |= FanEntityFeature.OSCILLATE
        if "preset" in definition.dps:
            features |= FanEntityFeature.PRESET_MODE
            self._attr_preset_modes = [str(o) for o in meta.get("presets", [])]
        self._attr_supported_features = features
        if self._speed_options:
            self._attr_speed_count = len(self._speed_options)

    @property
    def is_on(self) -> bool | None:
        """On."""
        if "switch" not in self.definition.dps:
            pct = self.percentage
            return None if pct is None else pct > 0
        value = self.dp_value("switch")
        return None if value is None else bool(value)

    @property
    def percentage(self) -> int | None:
        """Speed."""
        raw = self.dp_value("speed")
        if raw is None:
            return None
        if self._speed_options:
            try:
                return ordered_list_item_to_percentage(self._speed_options, str(raw))
            except ValueError:
                return None
        if self._speed is not None:
            return int(round(self._speed.remap_to(float(raw), 1, 100)))
        return None

    @property
    def current_direction(self) -> str | None:
        """Direction."""
        value = self.dp_value("direction")
        if value is None:
            return None
        return DIRECTION_REVERSE if str(value).lower() in ("reverse", "backward", "back") else DIRECTION_FORWARD

    @property
    def oscillating(self) -> bool | None:
        """Oscillation."""
        value = self.dp_value("oscillate")
        return None if value is None else bool(value)

    @property
    def preset_mode(self) -> str | None:
        """Preset."""
        value = self.dp_value("preset")
        return None if value is None else str(value)

    async def async_turn_on(self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any) -> None:
        """On."""
        dps: dict[int | str, Any] = {}
        if "switch" in self.definition.dps:
            dps[self.dp("switch")] = True  # type: ignore[index]
        if percentage is not None and "speed" in self.definition.dps:
            dps[self.dp("speed")] = self._speed_raw(percentage)  # type: ignore[index]
        if preset_mode is not None and "preset" in self.definition.dps:
            dps[self.dp("preset")] = preset_mode  # type: ignore[index]
        if dps:
            await self.async_write_dps(dps)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Off."""
        if "switch" in self.definition.dps:
            await self.async_write("switch", False)
        elif self._speed is not None:
            await self.async_write("speed", self._speed.min)

    def _speed_raw(self, percentage: int) -> Any:
        if self._speed_options:
            return percentage_to_ordered_list_item(self._speed_options, percentage)
        if self._speed is not None:
            return self._speed.remap_from(float(percentage), 1, 100)
        return percentage

    async def async_set_percentage(self, percentage: int) -> None:
        """Speed."""
        if percentage == 0:
            await self.async_turn_off()
            return
        await self.async_write("speed", self._speed_raw(percentage))

    async def async_set_direction(self, direction: str) -> None:
        """Direction."""
        await self.async_write("direction", "reverse" if direction == DIRECTION_REVERSE else "forward")

    async def async_oscillate(self, oscillating: bool) -> None:
        """Oscillation."""
        await self.async_write("oscillate", oscillating)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Preset."""
        await self.async_write("preset", preset_mode)
