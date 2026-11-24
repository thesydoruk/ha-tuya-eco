"""Power-on state of lights: building and describing the raw ``power_memory`` payload.

Shared by the select (mode), the button (store the light's current state) and the
``set_power_on_state`` service (an explicit state).

The payload has a colour half (H, S, V) and a white half (brightness, temperature) but no
field saying which one applies. Tuya does not document how a firmware chooses, so a custom
state always zeroes the half it does not use: that reads the same whichever half the
firmware looks at first.
"""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import ServiceValidationError

from .entity import EcoEntity
from .light import KELVIN_COLD, KELVIN_WARM, MODE_COLOUR, decode_colour
from .mapping import IntegerType, PowerMemory

MODE_CUSTOM = 2
_FULL = 1000  # saturation and value are always 0-1000 in this DP


def stored(entity: EcoEntity) -> PowerMemory | None:
    """The payload the device reports."""
    return PowerMemory.decode(entity.dp_value("value"))


def _has_colour(entity: EcoEntity) -> bool:
    return entity.dp("color") is not None


def _has_white(entity: EcoEntity) -> bool:
    return entity.dp("brightness") is not None or entity.dp("color_temp") is not None


def _range(entity: EcoEntity, role: str) -> IntegerType | None:
    if entity.dp(role) is None:
        return None
    spec = IntegerType.from_values(entity.definition.meta.get(role))
    return spec if spec.max > spec.min else IntegerType(min=0, max=_FULL)


def _current_colour(entity: EcoEntity) -> tuple[float, float, float] | None:
    meta = entity.definition.meta.get("color") or {}
    hsv = meta.get("hsv") or {}
    return decode_colour(
        entity.dp_value("color"),
        bool(meta.get("v2", True)),
        int((hsv.get("s") or {}).get("max") or 0),
        int((hsv.get("v") or {}).get("max") or 0),
    )


def _custom(entity: EcoEntity) -> PowerMemory:
    memory = stored(entity) or PowerMemory()
    memory.mode = MODE_CUSTOM
    return memory


def _set_colour(memory: PowerMemory, hue: float, saturation: float, value: float) -> None:
    memory.hue = int(round(hue)) % 361
    memory.saturation = int(round(max(0.0, min(1.0, saturation)) * _FULL))
    memory.value = int(round(max(0.0, min(1.0, value)) * _FULL))
    memory.brightness = memory.color_temp = 0


def snapshot(entity: EcoEntity) -> PowerMemory:
    """The light's current state as the custom power-on state.

    Colour when the light is in colour mode (or has no white channel), white otherwise.
    """
    memory = _custom(entity)
    if _has_colour(entity) and (entity.dp_value("mode") == MODE_COLOUR or not _has_white(entity)):
        colour = _current_colour(entity)
        if colour is not None:
            _set_colour(memory, *colour)
        else:
            memory.brightness = memory.color_temp = 0
        return memory
    memory.hue = memory.saturation = memory.value = 0
    brightness = entity.dp_value("brightness")
    memory.brightness = int(brightness) if isinstance(brightness, (int, float)) else 0
    temp = entity.dp_value("color_temp")
    memory.color_temp = int(temp) if isinstance(temp, (int, float)) else 0
    return memory


def explicit(
    entity: EcoEntity,
    *,
    hs_color: tuple[float, float] | None = None,
    brightness_pct: float | None = None,
    color_temp_kelvin: float | None = None,
) -> PowerMemory:
    """A custom power-on state given by value; with no value at all, the current state.

    A brightness without colour or temperature dims the white light, or the current colour
    of a light that has no white channel.
    """
    if hs_color is None and brightness_pct is None and color_temp_kelvin is None:
        return snapshot(entity)
    name = entity.device.name
    if hs_color is not None and color_temp_kelvin is not None:
        raise ServiceValidationError(f"{name}: give a colour or a colour temperature, not both")
    memory = _custom(entity)
    level = None if brightness_pct is None else max(1.0, min(100.0, float(brightness_pct))) / 100
    if hs_color is None and color_temp_kelvin is None and not _has_white(entity):
        if not _has_colour(entity):
            raise ServiceValidationError(f"{name}: the light has no brightness to store")
        colour = _current_colour(entity) or (float(memory.hue), memory.saturation / _FULL, 1.0)
        hs_color = (colour[0], colour[1] * 100)
    if hs_color is not None:
        if not _has_colour(entity):
            raise ServiceValidationError(f"{name}: the light has no colour")
        _set_colour(memory, hs_color[0], hs_color[1] / 100, 1.0 if level is None else level)
        return memory
    bright = _range(entity, "brightness")
    temp = _range(entity, "color_temp")
    if color_temp_kelvin is not None and temp is None:
        raise ServiceValidationError(f"{name}: the light has no colour temperature")
    memory.hue = memory.saturation = memory.value = 0
    if level is not None:
        memory.brightness = bright.remap_from(level, 0, 1) if bright else int(round(level * _FULL))
    elif not memory.brightness:
        current = entity.dp_value("brightness")
        memory.brightness = int(current) if isinstance(current, (int, float)) else (bright.max if bright else _FULL)
    if color_temp_kelvin is not None and temp is not None:
        kelvin = max(KELVIN_WARM, min(KELVIN_COLD, float(color_temp_kelvin)))
        memory.color_temp = temp.remap_from(kelvin, KELVIN_WARM, KELVIN_COLD)
    return memory


def attributes(entity: EcoEntity) -> dict[str, Any] | None:
    """The stored state in Home Assistant units (only what the light can show)."""
    memory = stored(entity)
    if memory is None:
        return None
    attrs: dict[str, Any] = {}
    if _has_colour(entity):
        attrs["hs_color"] = (memory.hue, round(memory.saturation / 10, 1))
        attrs["color_brightness_pct"] = round(memory.value / 10)
    bright = _range(entity, "brightness")
    if bright is not None:
        attrs["white_brightness_pct"] = (
            round(bright.remap_to(float(memory.brightness), 0, 100)) if memory.brightness else 0
        )
    temp = _range(entity, "color_temp")
    if temp is not None:
        attrs["color_temp_kelvin"] = round(temp.remap_to(float(memory.color_temp), KELVIN_WARM, KELVIN_COLD))
    return attrs
