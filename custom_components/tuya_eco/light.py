"""Light platform (white / colour temperature / HS colour, Tuya v1 and v2 encodings)."""

from __future__ import annotations

import colorsys
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import EntityDef, IntegerType
from .platform_setup import async_setup_platform_entities

_LOGGER = logging.getLogger(__name__)

KELVIN_WARM = 2700
KELVIN_COLD = 6500
MODE_WHITE = "white"
MODE_COLOUR = "colour"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up lights."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.LIGHT, EcoLight)


def _decode_colour(raw: Any, v2: bool) -> tuple[float, float, float] | None:
    """Return (h 0-360, s 0-1, v 0-1) from Tuya colour data."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    try:
        if len(text) == 12 or (v2 and len(text) >= 12 and len(text) != 14):
            h = int(text[0:4], 16)
            s = int(text[4:8], 16) / 1000
            v = int(text[8:12], 16) / 1000
        elif len(text) == 14:
            h = int(text[6:10], 16)
            s = int(text[10:12], 16) / 255
            v = int(text[12:14], 16) / 255
        else:
            return None
    except ValueError:
        return None
    return float(h % 361), max(0.0, min(1.0, s)), max(0.0, min(1.0, v))


def _encode_colour(h: float, s: float, v: float, v2: bool) -> str:
    """Encode (h 0-360, s 0-1, v 0-1) as Tuya colour data."""
    h_i = int(round(h)) % 361
    if v2:
        return f"{h_i:04x}{int(round(s * 1000)):04x}{int(round(v * 1000)):04x}"
    r, g, b = colorsys.hsv_to_rgb(h / 360, s, v)
    return f"{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}{h_i:04x}{int(round(s * 255)):02x}{int(round(v * 255)):02x}"


class EcoLight(EcoEntity, LightEntity):
    """Tuya light."""

    _attr_min_color_temp_kelvin = KELVIN_WARM
    _attr_max_color_temp_kelvin = KELVIN_COLD

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        meta = definition.meta
        self._bright = IntegerType.from_values(meta.get("brightness")) if "brightness" in definition.dps else None
        self._temp = IntegerType.from_values(meta.get("color_temp")) if "color_temp" in definition.dps else None
        self._colour_v2 = bool((meta.get("color") or {}).get("v2", True))
        self._modes: list[str] = list(meta.get("modes") or [])
        modes: set[ColorMode] = set()
        if "color" in definition.dps:
            modes.add(ColorMode.HS)
        if self._temp is not None:
            modes.add(ColorMode.COLOR_TEMP)
        if not modes:
            modes.add(ColorMode.BRIGHTNESS if self._bright is not None else ColorMode.ONOFF)
        self._attr_supported_color_modes = modes

    # ------------------------------------------------------------------ state

    @property
    def is_on(self) -> bool | None:
        """On/off."""
        value = self.value("switch")
        return None if value is None else bool(value)

    @property
    def _work_mode(self) -> str | None:
        mode = self.value("mode")
        return str(mode) if mode is not None else None

    @property
    def color_mode(self) -> ColorMode | None:
        """Active colour mode."""
        modes = self._attr_supported_color_modes or set()
        if ColorMode.HS in modes and (
            self._work_mode == MODE_COLOUR or (ColorMode.COLOR_TEMP not in modes and self._bright is None)
        ):
            return ColorMode.HS
        if ColorMode.COLOR_TEMP in modes:
            return ColorMode.COLOR_TEMP
        if ColorMode.HS in modes and self._work_mode is None:
            return ColorMode.HS
        if ColorMode.BRIGHTNESS in modes:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    @property
    def brightness(self) -> int | None:
        """0-255."""
        if self.color_mode == ColorMode.HS:
            hsv = _decode_colour(self.value("color"), self._colour_v2)
            return None if hsv is None else int(round(hsv[2] * 255))
        if self._bright is None:
            return None
        raw = self.value("brightness")
        if raw is None:
            return None
        return int(round(self._bright.remap_to(float(raw), 0, 255)))

    @property
    def color_temp_kelvin(self) -> int | None:
        """Colour temperature."""
        if self._temp is None:
            return None
        raw = self.value("color_temp")
        if raw is None:
            return None
        return int(round(self._temp.remap_to(float(raw), KELVIN_WARM, KELVIN_COLD)))

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """HS colour."""
        hsv = _decode_colour(self.value("color"), self._colour_v2)
        return None if hsv is None else (hsv[0], hsv[1] * 100)

    # ------------------------------------------------------------------ commands

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on with optional brightness / colour / temperature."""
        dps: dict[int | str, Any] = {self.dp("switch"): True}  # type: ignore[dict-item]
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        hs = kwargs.get(ATTR_HS_COLOR)
        kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        mode_dp = self.dp("mode")

        if hs is not None and "color" in self.definition.dps:
            current = _decode_colour(self.value("color"), self._colour_v2)
            v = (brightness / 255) if brightness is not None else (current[2] if current else 1.0)
            dps[self.dp("color")] = _encode_colour(hs[0], hs[1] / 100, max(0.01, v), self._colour_v2)  # type: ignore[index]
            if mode_dp is not None and MODE_COLOUR in self._modes:
                dps[mode_dp] = MODE_COLOUR
        elif kelvin is not None and self._temp is not None:
            dps[self.dp("color_temp")] = self._temp.remap_from(float(kelvin), KELVIN_WARM, KELVIN_COLD)  # type: ignore[index]
            if mode_dp is not None and MODE_WHITE in self._modes:
                dps[mode_dp] = MODE_WHITE
            if brightness is not None and self._bright is not None:
                dps[self.dp("brightness")] = self._bright.remap_from(float(brightness), 0, 255)  # type: ignore[index]
        elif brightness is not None:
            if self.color_mode == ColorMode.HS and "color" in self.definition.dps:
                current = _decode_colour(self.value("color"), self._colour_v2) or (0.0, 0.0, 1.0)
                dps[self.dp("color")] = _encode_colour(
                    current[0], current[1], max(0.01, brightness / 255), self._colour_v2
                )  # type: ignore[index]
            elif self._bright is not None:
                dps[self.dp("brightness")] = self._bright.remap_from(float(brightness), 0, 255)  # type: ignore[index]
                if mode_dp is not None and MODE_WHITE in self._modes and self._work_mode not in (None, MODE_WHITE):
                    dps[mode_dp] = MODE_WHITE
        await self.async_write_dps(dps)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Off."""
        await self.async_write("switch", False)
