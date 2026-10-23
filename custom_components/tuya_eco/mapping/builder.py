"""Build entity definitions from a device's DP schema (and optional YAML profile).

Priority:
  1. an explicit YAML profile for the product id (``profiles/``),
  2. composite builders (light, cover, climate, fan) that claim several DPs,
  3. per-code knowledge tables (``codes.py``),
  4. generic fallback by DP type.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, Platform

from ..providers.base import CloudDevice, DpSpec, DpType
from . import codes as C
from .model import EntityDef
from .types import EnumType, IntegerType

_LOGGER = logging.getLogger(__name__)

LIGHT_CATEGORIES = {"dj", "dd", "fwd", "xdd", "dc", "tgq", "tgkg", "fsd", "dsd", "jsq", "sxd", "ykq", "zb"}
COVER_CATEGORIES = {"cl", "clkg", "ckmkzq", "jdcljqr", "mc"}
CLIMATE_CATEGORIES = {"kt", "wk", "wkf", "qn", "rs", "wkcz", "xfj", "cs", "kj"}
FAN_CATEGORIES = {"fs", "fskg", "kj", "cs", "fsd"}
SENSOR_CATEGORIES = {
    "ldcg",
    "wsdcg",
    "pm2.5",
    "co2bj",
    "hjjcy",
    "jqbj",
    "rqbj",
    "ywbj",
    "pir",
    "mcs",
    "sj",
    "zd",
    "wnykq",
    "dlq",
    "ms",
}
PRESENCE_CATEGORIES = {"hps", "pir"}


class _Ctx:
    """Mutable state shared by the builders."""

    def __init__(self, device: CloudDevice) -> None:
        self.device = device
        self.by_code: dict[str, DpSpec] = {spec.code: spec for spec in device.dp_schema.values()}
        self.claimed: set[int] = set()
        self.defs: list[EntityDef] = []

    def spec(self, code: str) -> DpSpec | None:
        spec = self.by_code.get(code)
        if spec is None or spec.dp_id in self.claimed:
            return None
        return spec

    def claim(self, *specs: DpSpec | None) -> None:
        for spec in specs:
            if spec is not None:
                self.claimed.add(spec.dp_id)

    def add(self, entity: EntityDef) -> None:
        self.defs.append(entity)
        self.claimed.update(entity.dps.values())


def _suffixes() -> list[str]:
    return ["", "_1", "_2", "_3", "_4", "_5", "_6"]


# ---------------------------------------------------------------------- lights


def _build_lights(ctx: _Ctx) -> None:
    cat = ctx.device.category
    for suffix in _suffixes():
        switch = ctx.spec(f"switch_led{suffix}")
        if switch is None and suffix == "" and cat in LIGHT_CATEGORIES:
            switch = ctx.spec("switch") or ctx.spec("switch_1")
        if switch is None or switch.type != DpType.BOOLEAN:
            continue
        n = suffix
        bright = ctx.spec(f"bright_value{n}") or (ctx.spec("bright_value_v2") if n == "" else None)
        temp = ctx.spec(f"temp_value{n}") or (ctx.spec("temp_value_v2") if n == "" else None)
        colour = ctx.spec(f"colour_data{n}") or (ctx.spec("colour_data_v2") if n == "" else None)
        mode = ctx.spec(f"work_mode{n}") if n else ctx.spec("work_mode")
        bmin = ctx.spec(f"brightness_min{n}")
        bmax = ctx.spec(f"brightness_max{n}")
        dps: dict[str, int] = {"switch": switch.dp_id}
        meta: dict[str, Any] = {}
        if bright:
            dps["brightness"] = bright.dp_id
            meta["brightness"] = _int_meta(
                bright, default_max=1000 if "v2" in bright.code or bright.values.get("max", 0) > 255 else 255
            )
        if temp:
            dps["color_temp"] = temp.dp_id
            meta["color_temp"] = _int_meta(
                temp, default_max=1000 if "v2" in temp.code or temp.values.get("max", 0) > 255 else 255
            )
        if colour:
            dps["color"] = colour.dp_id
            meta["color"] = {
                "v2": colour.code.endswith("v2") or (bright is not None and bright.values.get("max", 0) > 255)
            }
        if mode:
            dps["mode"] = mode.dp_id
            meta["modes"] = EnumType.from_values(mode.values).range
        if bmin:
            dps["brightness_min"] = bmin.dp_id
        if bmax:
            dps["brightness_max"] = bmax.dp_id
        name = None if (n == "" or (n == "_1" and not ctx.spec("switch_led_2"))) else f"Light {n[1:]}"
        ctx.add(EntityDef(Platform.LIGHT, f"light{n}", dps, name=name, meta=meta))
        ctx.claim(bmin, bmax)


def _int_meta(spec: DpSpec, *, default_max: int) -> dict[str, Any]:
    t = IntegerType.from_values(spec.values)
    if t.max <= t.min:
        t = IntegerType(min=0 if default_max == 255 else 10, max=default_max, scale=0, step=1, unit=t.unit)
    return {"min": t.min, "max": t.max, "scale": t.scale, "step": t.step, "unit": t.unit}


# ---------------------------------------------------------------------- covers


def _build_covers(ctx: _Ctx) -> None:
    for suffix in _suffixes():
        control = ctx.spec(f"control{suffix}")
        if control is None or control.type != DpType.ENUM:
            continue
        options = EnumType.from_values(control.values).range
        lowered = {o.lower() for o in options}
        if not ({"open", "close"} & lowered or {"up", "down"} & lowered):
            continue
        position = ctx.spec(f"percent_control{suffix}")
        state = ctx.spec(f"percent_state{suffix}") or ctx.spec(f"position{suffix}")
        dps: dict[str, int] = {"control": control.dp_id}
        meta: dict[str, Any] = {"options": options}
        if position:
            dps["position"] = position.dp_id
            meta["position"] = _int_meta(position, default_max=100)
        if state:
            dps["position_state"] = state.dp_id
            meta["position_state"] = _int_meta(state, default_max=100)
        name = None if suffix in ("", "_1") else f"Cover {suffix[1:]}"
        ctx.add(EntityDef(Platform.COVER, f"cover{suffix}", dps, name=name, meta=meta))
    # Garage door openers: switch_1 + doorcontact_state
    if ctx.device.category == "ckmkzq":
        sw = ctx.spec("switch_1") or ctx.spec("switch")
        contact = ctx.spec("doorcontact_state")
        if sw and contact:
            ctx.add(
                EntityDef(
                    Platform.COVER,
                    "garage",
                    {"switch": sw.dp_id, "contact": contact.dp_id},
                    device_class="garage",
                    meta={"garage": True},
                )
            )


# ---------------------------------------------------------------------- climate


_HVAC_MODE_MAP = {
    "auto": "auto",
    "cold": "cool",
    "cool": "cool",
    "hot": "heat",
    "heat": "heat",
    "wind": "fan_only",
    "fan": "fan_only",
    "wet": "dry",
    "dry": "dry",
    "off": "off",
    "manual": "heat",
    "program": "auto",
    "holiday": "auto",
    "eco": "heat",
    "comfort": "heat",
}


def _build_climate(ctx: _Ctx) -> None:
    temp_set = ctx.spec("temp_set")
    if temp_set is None or temp_set.type != DpType.INTEGER:
        return
    if ctx.device.category not in CLIMATE_CATEGORIES and ctx.spec("temp_current") is None:
        return
    switch = ctx.spec("switch")
    current = ctx.spec("temp_current")
    mode = ctx.spec("mode")
    fan = ctx.spec("fan_speed_enum") or ctx.spec("level") or ctx.spec("windspeed")
    humidity = ctx.spec("humidity_current") or ctx.spec("humidity")
    dps: dict[str, int] = {"temp_set": temp_set.dp_id}
    meta: dict[str, Any] = {"temp_set": _int_meta(temp_set, default_max=35)}
    if switch:
        dps["switch"] = switch.dp_id
    if current:
        dps["temp_current"] = current.dp_id
        meta["temp_current"] = _int_meta(current, default_max=100)
    if mode and mode.type == DpType.ENUM:
        options = EnumType.from_values(mode.values).range
        hvac = {o: _HVAC_MODE_MAP.get(o.lower()) for o in options}
        if ctx.device.category == "kt" and "auto" in hvac:
            hvac["auto"] = "heat_cool"
        known = {o: m for o, m in hvac.items() if m}
        if known:
            dps["mode"] = mode.dp_id
            meta["hvac_modes"] = known
        else:
            dps["preset"] = mode.dp_id
            meta["presets"] = options
    if fan and fan.type == DpType.ENUM:
        dps["fan"] = fan.dp_id
        meta["fan_modes"] = EnumType.from_values(fan.values).range
    if humidity and humidity.type == DpType.INTEGER:
        dps["humidity"] = humidity.dp_id
        meta["humidity"] = _int_meta(humidity, default_max=100)
    unit = temp_set.values.get("unit") or (current.values.get("unit") if current else "") or "°C"
    meta["unit"] = "°F" if "f" in str(unit).lower() else "°C"
    ctx.add(EntityDef(Platform.CLIMATE, "climate", dps, meta=meta))


# ---------------------------------------------------------------------- fans


def _build_fans(ctx: _Ctx) -> None:
    if ctx.device.category not in FAN_CATEGORIES:
        return
    switch = ctx.spec("switch_fan") or ctx.spec("switch") or ctx.spec("fan_switch")
    if switch is None or switch.type != DpType.BOOLEAN:
        return
    speed = ctx.spec("fan_speed_percent") or ctx.spec("fan_speed")
    direction = ctx.spec("fan_direction")
    osc = ctx.spec("switch_horizontal") or ctx.spec("switch_vertical") or ctx.spec("oscillate")
    mode = ctx.spec("mode") or ctx.spec("fan_mode")
    dps: dict[str, int] = {"switch": switch.dp_id}
    meta: dict[str, Any] = {}
    if speed:
        dps["speed"] = speed.dp_id
        if speed.type == DpType.ENUM:
            meta["speed_options"] = EnumType.from_values(speed.values).range
        else:
            meta["speed"] = _int_meta(speed, default_max=100)
    if direction and direction.type == DpType.ENUM:
        dps["direction"] = direction.dp_id
    if osc and osc.type == DpType.BOOLEAN:
        dps["oscillate"] = osc.dp_id
    if mode and mode.type == DpType.ENUM:
        dps["preset"] = mode.dp_id
        meta["presets"] = EnumType.from_values(mode.values).range
    ctx.add(EntityDef(Platform.FAN, "fan", dps, meta=meta))


# ---------------------------------------------------------------------- simple codes


def _build_by_code(ctx: _Ctx) -> None:
    for code, spec in ctx.by_code.items():
        if spec.dp_id in ctx.claimed or code in C.IGNORED_CODES:
            continue
        base, _idx = C.split_channel(code)
        label = C.pretty(code)
        # ---- binary sensors
        if code in C.BINARY_SENSORS and spec.type in (DpType.BOOLEAN, DpType.ENUM, DpType.BITMAP, DpType.INTEGER):
            rule = C.BINARY_SENSORS[code]
            ctx.add(
                EntityDef(
                    Platform.BINARY_SENSOR,
                    code,
                    {"state": spec.dp_id},
                    name=label,
                    device_class=rule.device_class,
                    entity_category=rule.entity_category,
                    meta={"on_values": list(rule.on_values), "type": str(spec.type)},
                )
            )
            continue
        # ---- switches
        if spec.type == DpType.BOOLEAN and not spec.read_only:
            rule = C.SWITCHES.get(code) or C.SWITCHES.get(base)
            ctx.add(
                EntityDef(
                    Platform.SWITCH,
                    code,
                    {"switch": spec.dp_id},
                    name=(rule.name if rule and rule.name else label) if not _is_main_switch(code, ctx) else None,
                    device_class=rule.device_class if rule else None,
                    entity_category=rule.entity_category if rule else None,
                    icon=rule.icon if rule else None,
                )
            )
            continue
        if spec.type == DpType.BOOLEAN:
            ctx.add(
                EntityDef(Platform.BINARY_SENSOR, code, {"state": spec.dp_id}, name=label, meta={"type": "Boolean"})
            )
            continue
        # ---- integers
        if spec.type == DpType.INTEGER:
            t = IntegerType.from_values(spec.values)
            sensor_rule = C.SENSORS.get(code) or C.SENSORS.get(base)
            if code == "bright_value" and ctx.device.category in SENSOR_CATEGORIES:
                sensor_rule = C.SENSORS["bright_value_sensor"]
            number_rule = C.NUMBERS.get(code) or C.NUMBERS.get(base)
            if not spec.read_only and (number_rule or sensor_rule is None):
                ctx.add(
                    EntityDef(
                        Platform.NUMBER,
                        code,
                        {"value": spec.dp_id},
                        name=(number_rule.name if number_rule and number_rule.name else label),
                        device_class=number_rule.device_class if number_rule else None,
                        unit=(number_rule.unit if number_rule and number_rule.unit else t.unit or None),
                        entity_category=number_rule.entity_category if number_rule else EntityCategory.CONFIG,
                        icon=number_rule.icon if number_rule else None,
                        meta={"min": t.min, "max": t.max, "scale": t.scale, "step": t.step},
                    )
                )
                continue
            scale = t.scale
            if sensor_rule and sensor_rule.default_scale is not None and t.scale == 0 and not spec.values.get("scale"):
                scale = sensor_rule.default_scale
            ctx.add(
                EntityDef(
                    Platform.SENSOR,
                    code,
                    {"value": spec.dp_id},
                    name=label,
                    device_class=sensor_rule.device_class if sensor_rule else None,
                    state_class=(sensor_rule.state_class if sensor_rule else SensorStateClass.MEASUREMENT),
                    unit=(sensor_rule.unit if sensor_rule and sensor_rule.unit else t.unit or None),
                    entity_category=sensor_rule.entity_category if sensor_rule else None,
                    icon=sensor_rule.icon if sensor_rule else None,
                    meta={"scale": scale, "type": "Integer"},
                )
            )
            continue
        # ---- enums
        if spec.type == DpType.ENUM:
            options = EnumType.from_values(spec.values).range
            if not spec.read_only:
                rule = C.SELECTS.get(code) or C.SELECTS.get(base)
                ctx.add(
                    EntityDef(
                        Platform.SELECT,
                        code,
                        {"value": spec.dp_id},
                        name=(rule.name if rule and rule.name else label),
                        entity_category=rule.entity_category if rule else EntityCategory.CONFIG,
                        icon=rule.icon if rule else None,
                        meta={"options": options},
                    )
                )
            else:
                sensor_rule = C.SENSORS.get(code)
                ctx.add(
                    EntityDef(
                        Platform.SENSOR,
                        code,
                        {"value": spec.dp_id},
                        name=label,
                        device_class=SensorDeviceClass.ENUM,
                        entity_category=sensor_rule.entity_category if sensor_rule else None,
                        icon=sensor_rule.icon if sensor_rule else None,
                        meta={"options": options, "type": "Enum"},
                    )
                )
            continue
        # ---- strings / bitmaps as diagnostic sensors
        if spec.type in (DpType.STRING, DpType.BITMAP) and (spec.read_only or spec.type == DpType.BITMAP):
            ctx.add(
                EntityDef(
                    Platform.SENSOR,
                    code,
                    {"value": spec.dp_id},
                    name=label,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    meta={"type": str(spec.type), "labels": list(spec.values.get("label") or [])},
                )
            )


def _is_main_switch(code: str, ctx: _Ctx) -> bool:
    if code == "switch":
        return True
    return code == "switch_1" and "switch_2" not in ctx.by_code and "switch" not in ctx.by_code


# ---------------------------------------------------------------------- detected DPs (no schema)


def synthesize_schema(dps: dict[str, Any]) -> dict[int, DpSpec]:
    """Guess a schema from live DP values (manual devices without cloud data)."""
    schema: dict[int, DpSpec] = {}
    for key, value in dps.items():
        try:
            dp_id = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, bool):
            dp_type, values = DpType.BOOLEAN, {}
        elif isinstance(value, int):
            dp_type, values = DpType.INTEGER, {"min": 0, "max": max(1000, value), "scale": 0, "step": 1}
        elif isinstance(value, str):
            dp_type, values = DpType.STRING, {}
        else:
            dp_type, values = DpType.RAW, {}
        schema[dp_id] = DpSpec(
            dp_id=dp_id, code=f"dp_{dp_id}", type=dp_type, values=values, read_only=dp_type != DpType.BOOLEAN
        )
    return schema


# ---------------------------------------------------------------------- entry point


def build_entities(device: CloudDevice) -> list[EntityDef]:
    """Return the entity definitions for a device."""
    from .profiles import profile_entities  # noqa: PLC0415 - avoid import cycle at module load

    profile = profile_entities(device)
    if profile:
        return profile
    ctx = _Ctx(device)
    _build_lights(ctx)
    _build_covers(ctx)
    _build_climate(ctx)
    _build_fans(ctx)
    _build_by_code(ctx)
    _LOGGER.debug(
        "%s (%s): %d entities from %d DPs", device.name, device.category, len(ctx.defs), len(device.dp_schema)
    )
    return ctx.defs
