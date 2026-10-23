"""Build entity definitions from a device's DP schema (and optional YAML profile).

Priority:
  1. an explicit YAML profile for the product id (``profiles/``),
  2. composite builders (light, cover, climate, fan, humidifier, vacuum, alarm) that
     claim several DPs,
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

LIGHT_CATEGORIES = {
    "dj",
    "dd",
    "fwd",
    "xdd",
    "dc",
    "tgq",
    "tdq",
    "tgkg",
    "fsd",
    "dsd",
    "sxd",
    "ykq",
    "gyd",
    "hcdd",
    "tyd",
    "tyndj",
    "mbd",
    "qjdcz",
    "bzyd",
    "hxd",
    "sz",
}
# Categories where a plain `light` boolean is a config "backlight" switch, not the main light.
BACKLIGHT_CATEGORIES = {"kj", "kt", "ks", "qn", "msp", "cwwsq", "kg", "clkg", "cz", "pc"}
COVER_CATEGORIES = {"cl", "clkg", "ckmkzq", "jdcljqr"}
CLIMATE_CATEGORIES = {"kt", "wk", "wkf", "qn", "rs", "dbl", "mjj", "xfj", "ktkzq", "ntq", "bgl", "yb"}
SWITCH_ONLY_HVAC = {
    "kt": "cool",
    "wk": "heat_cool",
    "dbl": "heat",
    "mjj": "heat",
    "qn": "heat",
    "rs": "heat",
    "wkf": "heat",
}
FAN_CATEGORIES = {"fs", "fskg", "kj", "cs", "fsd", "ks", "xfj"}
HUMIDIFIER_CATEGORIES = {"jsq": "humidifier", "cs": "dehumidifier"}
VACUUM_CATEGORIES = {"sd"}
ALARM_CATEGORIES = {"mal", "wg2"}
ILLUMINANCE_CATEGORIES = {"ldcg", "wsdcg", "qxj", "tdq", "dgnbj"}
SCENE_SWITCH_CATEGORIES = {"wxkg"}
CAMERA_CATEGORIES = {"sp", "dghsxj"}


class _Ctx:
    """Mutable state shared by the builders."""

    def __init__(self, device: CloudDevice) -> None:
        self.device = device
        self.by_code: dict[str, DpSpec] = {spec.code: spec for spec in device.dp_schema.values()}
        self.claimed: set[int] = set()
        self.defs: list[EntityDef] = []

    @property
    def category(self) -> str:
        return self.device.category

    def spec(self, *codes: str, type: DpType | None = None) -> DpSpec | None:  # noqa: A002
        """First unclaimed DP among `codes` (optionally of a given type)."""
        for code in codes:
            spec = self.by_code.get(code)
            if spec is None or spec.dp_id in self.claimed:
                continue
            if type is not None and spec.type != type:
                continue
            return spec
        return None

    def claim(self, *specs: DpSpec | None) -> None:
        for spec in specs:
            if spec is not None:
                self.claimed.add(spec.dp_id)

    def add(self, entity: EntityDef) -> None:
        self.defs.append(entity)
        self.claimed.update(entity.dps.values())


def _suffixes() -> list[str]:
    return ["", "_1", "_2", "_3", "_4", "_5", "_6"]


def _int_meta(spec: DpSpec, *, default_max: int, default_min: int = 0) -> dict[str, Any]:
    t = IntegerType.from_values(spec.values)
    if t.max <= t.min:
        t = IntegerType(min=default_min, max=default_max, scale=0, step=1, unit=t.unit)
    return {"min": t.min, "max": t.max, "scale": t.scale, "step": t.step, "unit": t.unit}


def _enum(spec: DpSpec | None) -> list[str]:
    return EnumType.from_values(spec.values).range if spec else []


# ---------------------------------------------------------------------- lights


def _build_lights(ctx: _Ctx) -> None:
    cat = ctx.category
    # Remote-control style lights (ykq) use their own codes.
    ctrl = ctx.spec("switch_controller", type=DpType.BOOLEAN)
    if ctrl:
        _add_light(
            ctx, "light", ctrl, ctx.spec("bright_controller"), ctx.spec("temp_controller"), None, ctx.spec("work_mode")
        )
    # Floodlight of cameras.
    flood = ctx.spec("floodlight_switch", type=DpType.BOOLEAN)
    if flood:
        _add_light(ctx, "floodlight", flood, ctx.spec("floodlight_lightness"), None, None, None, name="Floodlight")
    indicator = ctx.spec("basic_indicator", type=DpType.BOOLEAN)
    if indicator:
        ctx.add(
            EntityDef(
                Platform.LIGHT,
                "indicator",
                {"switch": indicator.dp_id},
                name="Indicator light",
                entity_category=EntityCategory.CONFIG,
            )
        )

    for suffix in _suffixes():
        switch = ctx.spec(f"switch_led{suffix}", type=DpType.BOOLEAN)
        if switch is None and suffix == "":
            if cat in LIGHT_CATEGORIES or cat in ("fs", "fsd"):
                switch = (
                    ctx.spec("light", "switch", "switch_1", type=DpType.BOOLEAN)
                    if cat != "fs"
                    else ctx.spec("light", type=DpType.BOOLEAN)
                )
            if switch is None:
                continue
        if switch is None:
            continue
        n = suffix
        bright = ctx.spec(f"bright_value{n}") if n else ctx.spec("bright_value_v2", "bright_value")
        temp = ctx.spec(f"temp_value{n}") if n else ctx.spec("temp_value_v2", "temp_value")
        colour = ctx.spec(f"colour_data{n}") if n else ctx.spec("colour_data_v2", "colour_data", "colour_data_hsv")
        mode = ctx.spec(f"work_mode{n}") if n else ctx.spec("work_mode")
        key = f"light{n}"
        name = None if (n == "" or (n == "_1" and not ctx.spec("switch_led_2"))) else f"Light {n[1:]}"
        _add_light(ctx, key, switch, bright, temp, colour, mode, name=name, suffix=n or "_1")

    # fs/fsd: a second plain light switch next to the main one
    if cat in ("fs", "fsd"):
        extra = ctx.spec("light", "switch_led", type=DpType.BOOLEAN)
        if extra:
            bright = ctx.spec("bright_value_1")
            _add_light(ctx, "light_2", extra, bright, None, None, None, name="Light 2")
    night = ctx.spec("switch_night_light", type=DpType.BOOLEAN)
    if night:
        ctx.add(EntityDef(Platform.LIGHT, "night_light", {"switch": night.dp_id}, name="Night light"))
    if cat in BACKLIGHT_CATEGORIES:
        back = ctx.spec("light", "switch_backlight", type=DpType.BOOLEAN)
        if back:
            ctx.add(
                EntityDef(
                    Platform.LIGHT,
                    "backlight",
                    {"switch": back.dp_id},
                    name="Backlight",
                    entity_category=EntityCategory.CONFIG,
                )
            )


def _add_light(
    ctx: _Ctx,
    key: str,
    switch: DpSpec,
    bright: DpSpec | None,
    temp: DpSpec | None,
    colour: DpSpec | None,
    mode: DpSpec | None,
    *,
    name: str | None = None,
    suffix: str = "_1",
) -> None:
    dps: dict[str, int] = {"switch": switch.dp_id}
    meta: dict[str, Any] = {}
    v2 = False
    if bright:
        dps["brightness"] = bright.dp_id
        big = "v2" in bright.code or bright.values.get("max", 0) > 255
        meta["brightness"] = _int_meta(bright, default_max=1000 if big else 255, default_min=10 if big else 25)
        v2 = meta["brightness"]["max"] > 255
    if temp:
        dps["color_temp"] = temp.dp_id
        big = "v2" in temp.code or temp.values.get("max", 0) > 255 or v2
        meta["color_temp"] = _int_meta(temp, default_max=1000 if big else 255)
    if colour:
        dps["color"] = colour.dp_id
        hsv = {k: colour.values.get(k) for k in ("h", "s", "v") if isinstance(colour.values.get(k), dict)}
        meta["color"] = {"v2": colour.code.endswith("v2") or v2 or ctx.category == "dd", "hsv": hsv}
    if mode and mode.type == DpType.ENUM:
        dps["mode"] = mode.dp_id
        meta["modes"] = _enum(mode)
    bmin = ctx.spec(f"brightness_min{suffix}")
    bmax = ctx.spec(f"brightness_max{suffix}")
    if bmin:
        dps["brightness_min"] = bmin.dp_id
    if bmax:
        dps["brightness_max"] = bmax.dp_id
    ctx.add(EntityDef(Platform.LIGHT, key, dps, name=name, meta=meta))


# ---------------------------------------------------------------------- covers


def _build_covers(ctx: _Ctx) -> None:
    cat = ctx.category
    if (
        cat not in COVER_CATEGORIES
        and ctx.spec("control", type=DpType.ENUM) is None
        and ctx.spec("mach_operate") is None
    ):
        return
    channels = [("", ""), ("_2", "_2"), ("_3", "_3")]
    for suffix, label in channels:
        control = ctx.spec(f"control{suffix}", type=DpType.ENUM)
        if control is None:
            continue
        options = _enum(control)
        lowered = {o.lower() for o in options}
        if not ({"open", "close"} & lowered or {"up", "down"} & lowered):
            continue
        position = ctx.spec(f"percent_control{suffix}", type=DpType.INTEGER)
        state = ctx.spec(f"percent_state{suffix}", type=DpType.INTEGER)
        if cat == "jdcljqr" and state is None:
            state = ctx.spec("percent_state", type=DpType.INTEGER)
        dps: dict[str, int] = {"control": control.dp_id}
        meta: dict[str, Any] = {"options": options, "invert": True}
        if position:
            dps["position"] = position.dp_id
            meta["position"] = _int_meta(position, default_max=100)
        if state:
            dps["position_state"] = state.dp_id
            meta["position_state"] = _int_meta(state, default_max=100)
        situation = ctx.spec("situation_set", type=DpType.ENUM) if suffix == "" else None
        if situation:
            dps["state"] = situation.dp_id
        if cat == "clkg":
            back_mode = ctx.spec("control_back_mode", type=DpType.ENUM)
            if back_mode:
                dps["back_mode"] = back_mode.dp_id  # "back" = not inverted
        tilt = ctx.spec("angle_horizontal", "angle_vertical", type=DpType.INTEGER) if suffix == "" else None
        if tilt:
            dps["tilt"] = tilt.dp_id
            meta["tilt"] = _int_meta(tilt, default_max=180)
        name = None if suffix == "" else f"Curtain {label[1:]}"
        ctx.add(EntityDef(Platform.COVER, f"cover{suffix}", dps, name=name, meta=meta))
    mach = ctx.spec("mach_operate", type=DpType.ENUM)
    if mach:
        position = ctx.spec("position", type=DpType.INTEGER)
        dps = {"control": mach.dp_id}
        meta = {"options": _enum(mach), "invert": True, "open_cmd": "FZ", "close_cmd": "ZZ", "stop_cmd": "STOP"}
        if position:
            dps["position"] = position.dp_id
            dps["position_state"] = position.dp_id
            meta["position"] = meta["position_state"] = _int_meta(position, default_max=100)
        ctx.add(EntityDef(Platform.COVER, "cover", dps, meta=meta))
    # Kogan-style blinds: switch_1 + percent_control
    if cat == "cl":
        sw = ctx.spec("switch_1", type=DpType.BOOLEAN)
        pos = ctx.spec("percent_control", type=DpType.INTEGER)
        if sw and pos:
            ctx.add(
                EntityDef(
                    Platform.COVER,
                    "blind",
                    {"switch": sw.dp_id, "position": pos.dp_id, "position_state": pos.dp_id},
                    device_class="blind",
                    meta={
                        "bool_control": True,
                        "invert": True,
                        "position": _int_meta(pos, default_max=100),
                        "position_state": _int_meta(pos, default_max=100),
                    },
                )
            )
    # Garage door openers: switch_n + doorcontact_state_n
    if cat == "ckmkzq":
        for suffix in ("_1", "_2", "_3"):
            sw = ctx.spec(f"switch{suffix}", type=DpType.BOOLEAN)
            contact = ctx.spec("doorcontact_state" if suffix == "_1" else f"doorcontact_state{suffix}")
            if sw and contact:
                name = None if suffix == "_1" else f"Door {suffix[1:]}"
                ctx.add(
                    EntityDef(
                        Platform.COVER,
                        f"garage{suffix}",
                        {"switch": sw.dp_id, "contact": contact.dp_id},
                        name=name,
                        device_class="garage",
                        meta={"garage": True},
                    )
                )


# ---------------------------------------------------------------------- climate


_HVAC_MODE_MAP = {
    "auto": "heat_cool",
    "cold": "cool",
    "cool": "cool",
    "freeze": "cool",
    "hot": "heat",
    "heat": "heat",
    "wind": "fan_only",
    "fan": "fan_only",
    "wet": "dry",
    "dry": "dry",
    "off": "off",
    "manual": "heat_cool",
}


def _build_climate(ctx: _Ctx) -> None:
    temp_set = ctx.spec("temp_set", type=DpType.INTEGER)
    temp_set_f = ctx.spec("temp_set_f", type=DpType.INTEGER)
    if temp_set is None and temp_set_f is None:
        return
    if ctx.category not in CLIMATE_CATEGORIES and ctx.spec("temp_current", "upper_temp") is None:
        return
    # Thermostats: a program/auto mode is an automatic schedule, not heat+cool.
    mode_map = dict(_HVAC_MODE_MAP)
    if ctx.category in ("wk", "wkf", "qn", "rs", "dbl", "mjj"):
        mode_map["auto"] = "auto"
        mode_map["manual"] = "heat"
        mode_map["program"] = "auto"
    switch = ctx.spec("switch", "power_switch", type=DpType.BOOLEAN)
    current = ctx.spec("temp_current", "upper_temp", type=DpType.INTEGER)
    current_f = ctx.spec("temp_current_f", "upper_temp_f", type=DpType.INTEGER)
    mode = ctx.spec("mode", type=DpType.ENUM)
    fan = ctx.spec("fan_speed_enum", "level", "windspeed", type=DpType.ENUM)
    humidity = ctx.spec("humidity_current", type=DpType.INTEGER)
    humidity_set = ctx.spec("humidity_set", type=DpType.INTEGER)
    swing = ctx.spec("swing", "shake", type=DpType.BOOLEAN)
    swing_h = ctx.spec("switch_horizontal", type=DpType.BOOLEAN)
    swing_v = ctx.spec("switch_vertical", type=DpType.BOOLEAN)
    primary = temp_set or temp_set_f
    assert primary is not None
    dps: dict[str, int] = {"temp_set": primary.dp_id}
    meta: dict[str, Any] = {"temp_set": _int_meta(primary, default_max=35 if temp_set else 95)}
    unit = "°F" if temp_set is None else "°C"
    if temp_set is not None and temp_set_f is not None:
        dps["temp_set_f"] = temp_set_f.dp_id
        meta["temp_set_f"] = _int_meta(temp_set_f, default_max=95)
    if switch:
        dps["switch"] = switch.dp_id
    cur = current if unit == "°C" or current_f is None else current_f
    if cur:
        dps["temp_current"] = cur.dp_id
        meta["temp_current"] = _int_meta(cur, default_max=100)
    if mode:
        options = _enum(mode)
        hvac = {o: mode_map.get(o.lower()) for o in options}
        # Two Tuya modes mapping to one HVAC mode become presets instead.
        seen: dict[str, str] = {}
        for opt, h in list(hvac.items()):
            if h is None or h == "off":
                continue
            if h in seen:
                hvac[opt] = None
                hvac[seen[h]] = None
            else:
                seen[h] = opt
        known = {o: h for o, h in hvac.items() if h}
        presets = [o for o, h in hvac.items() if h is None]
        if known:
            dps["mode"] = mode.dp_id
            meta["hvac_modes"] = known
        if presets:
            dps["preset"] = mode.dp_id
            meta["presets"] = presets
    if fan:
        dps["fan"] = fan.dp_id
        meta["fan_modes"] = _enum(fan)
    if humidity:
        dps["humidity"] = humidity.dp_id
        meta["humidity"] = _int_meta(humidity, default_max=100)
    if humidity_set:
        dps["humidity_set"] = humidity_set.dp_id
        meta["humidity_set"] = _int_meta(humidity_set, default_max=100)
    if swing:
        dps["swing"] = swing.dp_id
    if swing_h:
        dps["swing_h"] = swing_h.dp_id
    if swing_v:
        dps["swing_v"] = swing_v.dp_id
    declared = str(primary.values.get("unit") or (cur.values.get("unit") if cur else "") or "").lower()
    if declared in ("°f", "f", "fahrenheit", "℉"):
        unit = "°F"
    elif declared in ("°c", "c", "celsius", "℃"):
        unit = "°C"
    meta["unit"] = unit
    meta["switch_only_hvac"] = SWITCH_ONLY_HVAC.get(ctx.category, "heat")
    ctx.add(EntityDef(Platform.CLIMATE, "climate", dps, meta=meta))


# ---------------------------------------------------------------------- fans


def _build_fans(ctx: _Ctx) -> None:
    if ctx.category not in FAN_CATEGORIES:
        return
    switch = ctx.spec("switch_fan", "fan_switch", "switch", type=DpType.BOOLEAN)
    speed = ctx.spec("fan_speed_percent", "fan_speed", "speed", "fan_speed_enum")
    osc = ctx.spec("switch_horizontal", "switch_vertical", "oscillate", type=DpType.BOOLEAN)
    direction = ctx.spec("fan_direction", type=DpType.ENUM)
    if switch is None and speed is None and osc is None and direction is None:
        return
    mode = ctx.spec("fan_mode", "mode", type=DpType.ENUM)
    dps: dict[str, int] = {}
    meta: dict[str, Any] = {}
    if switch:
        dps["switch"] = switch.dp_id
    if speed:
        dps["speed"] = speed.dp_id
        if speed.type == DpType.ENUM:
            meta["speed_options"] = _enum(speed)
        else:
            meta["speed"] = _int_meta(speed, default_max=100, default_min=1)
    if direction:
        dps["direction"] = direction.dp_id
    if osc:
        dps["oscillate"] = osc.dp_id
    if mode:
        dps["preset"] = mode.dp_id
        meta["presets"] = _enum(mode)
    ctx.add(EntityDef(Platform.FAN, "fan", dps, meta=meta))


# ---------------------------------------------------------------------- humidifiers


def _build_humidifiers(ctx: _Ctx) -> None:
    kind = HUMIDIFIER_CATEGORIES.get(ctx.category)
    if kind is None:
        return
    switch = ctx.spec("switch", "switch_spray", type=DpType.BOOLEAN)
    if kind == "humidifier":
        current = ctx.spec("humidity_current", type=DpType.INTEGER)
        target = ctx.spec("humidity_set", type=DpType.INTEGER)
    else:
        current = ctx.spec("humidity_indoor", type=DpType.INTEGER)
        target = ctx.spec("dehumidify_set_value", type=DpType.INTEGER)
    if switch is None and current is None and target is None:
        return
    mode = ctx.spec("mode", type=DpType.ENUM)
    dps: dict[str, int] = {}
    meta: dict[str, Any] = {"device_class": kind}
    if switch:
        dps["switch"] = switch.dp_id
    if current:
        dps["current"] = current.dp_id
        meta["current"] = _int_meta(current, default_max=100)
    if target:
        dps["target"] = target.dp_id
        meta["target"] = _int_meta(target, default_max=100)
    if mode:
        dps["mode"] = mode.dp_id
        meta["modes"] = _enum(mode)
    ctx.add(EntityDef(Platform.HUMIDIFIER, "humidifier", dps, device_class=kind, meta=meta))


# ---------------------------------------------------------------------- vacuums


def _build_vacuums(ctx: _Ctx) -> None:
    if ctx.category not in VACUUM_CATEGORIES:
        return
    power_go = ctx.spec("power_go", type=DpType.BOOLEAN)
    status = ctx.spec("status", type=DpType.ENUM)
    if power_go is None and status is None:
        return
    dps: dict[str, int] = {}
    meta: dict[str, Any] = {}
    if power_go:
        dps["power_go"] = power_go.dp_id
    if status:
        dps["status"] = status.dp_id
    pause = ctx.spec("pause", type=DpType.BOOLEAN)
    if pause:
        dps["pause"] = pause.dp_id
    charge = ctx.spec("switch_charge", type=DpType.BOOLEAN)
    if charge:
        dps["switch_charge"] = charge.dp_id
    mode = ctx.spec("mode", type=DpType.ENUM)
    if mode:
        dps["mode"] = mode.dp_id
        meta["modes"] = _enum(mode)
        ctx.claim(mode)
    seek = ctx.spec("seek", type=DpType.BOOLEAN)
    if seek:
        dps["seek"] = seek.dp_id
    suction = ctx.spec("suction", type=DpType.ENUM)
    if suction:
        dps["fan_speed"] = suction.dp_id
        meta["fan_speeds"] = _enum(suction)
    ctx.add(EntityDef(Platform.VACUUM, "vacuum", dps, meta=meta))
    # `mode` stays available as a select too (zone/spot modes are useful).
    if mode:
        ctx.claimed.discard(mode.dp_id)


# ---------------------------------------------------------------------- alarm panels


def _build_alarms(ctx: _Ctx) -> None:
    if ctx.category not in ALARM_CATEGORIES:
        return
    master = ctx.spec("master_mode", type=DpType.ENUM)
    if master is None:
        return
    dps: dict[str, int] = {"mode": master.dp_id}
    meta: dict[str, Any] = {"options": _enum(master)}
    state = ctx.spec("master_state", type=DpType.ENUM)
    if state:
        dps["state"] = state.dp_id
    msg = ctx.spec("alarm_msg")
    if msg:
        dps["message"] = msg.dp_id
    ctx.add(EntityDef(Platform.ALARM_CONTROL_PANEL, "alarm", dps, name="Alarm", meta=meta))
    if state:
        ctx.claimed.discard(state.dp_id)  # also useful as a binary sensor


# ---------------------------------------------------------------------- events


def _build_events(ctx: _Ctx) -> None:
    if ctx.category in SCENE_SWITCH_CATEGORIES:
        for i in range(1, 10):
            spec = ctx.spec(f"switch_mode{i}", type=DpType.ENUM) or ctx.spec(f"switch{i}_value", type=DpType.ENUM)
            if spec is None:
                continue
            ctx.add(
                EntityDef(
                    Platform.EVENT,
                    f"button_{i}",
                    {"event": spec.dp_id},
                    name=f"Button {i}",
                    device_class="button",
                    meta={"event_types": _enum(spec) or ["click", "double_click", "press"]},
                )
            )
    if ctx.category in CAMERA_CATEGORIES:
        for code, name, kind in (
            ("alarm_message", "Doorbell message", "b64_string"),
            ("doorbell_pic", "Doorbell picture", "b64_raw"),
        ):
            spec = ctx.spec(code)
            if spec:
                ctx.add(
                    EntityDef(
                        Platform.EVENT,
                        code,
                        {"event": spec.dp_id},
                        name=name,
                        device_class="doorbell",
                        meta={"event_types": ["triggered"], "decode": kind},
                    )
                )


# ---------------------------------------------------------------------- simple codes


def _build_by_code(ctx: _Ctx) -> None:
    cat = ctx.category
    for code, spec in ctx.by_code.items():
        if spec.dp_id in ctx.claimed or code in C.IGNORED_CODES:
            continue
        base, _idx = C.split_channel(code)
        label = C.pretty(code)
        # ---- valves (irrigation controllers)
        if cat in C.VALVE_CATEGORIES and spec.type == DpType.BOOLEAN and (code == "switch" or base == "switch"):
            ctx.add(
                EntityDef(
                    Platform.VALVE,
                    code,
                    {"switch": spec.dp_id},
                    name=None
                    if code == "switch" or (code == "switch_1" and "switch_2" not in ctx.by_code)
                    else f"Zone {code.split('_')[-1]}",
                    device_class="water",
                )
            )
            continue
        # ---- sirens
        if code in C.SIREN_CODES and spec.type == DpType.BOOLEAN:
            ctx.add(EntityDef(Platform.SIREN, code, {"switch": spec.dp_id}, name=None if cat == "sgbj" else "Siren"))
            continue
        # ---- buttons (write-only actions)
        button_rule = C.BUTTONS.get(code) or (
            C.BUTTONS["manual_clean"] if code == "switch_usb6" and cat == "hxd" else None
        )
        if button_rule and spec.type == DpType.BOOLEAN and not spec.read_only:
            ctx.add(
                EntityDef(
                    Platform.BUTTON,
                    code,
                    {"press": spec.dp_id},
                    name="Snooze" if code == "switch_usb6" else (button_rule.name or label),
                    device_class=button_rule.device_class,
                    entity_category=button_rule.entity_category,
                    icon=button_rule.icon,
                    meta={"enabled_default": button_rule.enabled_default},
                )
            )
            continue
        # ---- binary sensors
        if code in C.BINARY_SENSORS and spec.type in (DpType.BOOLEAN, DpType.ENUM, DpType.BITMAP, DpType.INTEGER):
            rule = C.BINARY_SENSORS[code]
            ctx.add(
                EntityDef(
                    Platform.BINARY_SENSOR,
                    code,
                    {"state": spec.dp_id},
                    name=rule.name or label,
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
            if code == "bright_value" and cat in ILLUMINANCE_CATEGORIES:
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
            if sensor_rule and sensor_rule.default_scale is not None and not spec.values.get("scale"):
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
                    meta={
                        "scale": scale,
                        "type": "Integer",
                        "enabled_default": sensor_rule.enabled_default if sensor_rule else True,
                    },
                )
            )
            continue
        # ---- enums
        if spec.type == DpType.ENUM:
            options = _enum(spec)
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
    _build_humidifiers(ctx)
    _build_fans(ctx)
    _build_vacuums(ctx)
    _build_alarms(ctx)
    _build_events(ctx)
    _build_by_code(ctx)
    ctx.defs.append(
        EntityDef(
            Platform.SENSOR,
            "connection",
            {},
            name="Connection",
            entity_category=EntityCategory.DIAGNOSTIC,
            icon="mdi:lan-connect",
            meta={"type": "connection", "options": ["local", "cloud", "offline"]},
        )
    )
    assign_translation_keys(ctx.defs)
    _LOGGER.debug(
        "%s (%s): %d entities from %d DPs", device.name, device.category, len(ctx.defs), len(device.dp_schema)
    )
    return ctx.defs


# ---------------------------------------------------------------------- translations

_NUMBERED_KEYS = {
    (Platform.LIGHT, "light"): "light_n",
    (Platform.COVER, "cover"): "curtain_n",
    (Platform.COVER, "garage"): "door_n",
    (Platform.EVENT, "button"): "button_n",
    (Platform.VALVE, "switch"): "zone_n",
}
# Only codes with an English string in strings.json may become translation keys;
# anything else keeps its generated English name.
_KNOWN_KEYS: dict[Platform, frozenset[str]] = {
    Platform.SWITCH: frozenset(C.SWITCHES),
    Platform.BINARY_SENSOR: frozenset(C.BINARY_SENSORS),
    Platform.NUMBER: frozenset(C.NUMBERS),
    Platform.SELECT: frozenset(C.SELECTS),
    Platform.SENSOR: frozenset(C.SENSORS) | {"bright_value", "connection"},
    Platform.BUTTON: frozenset(C.BUTTONS),
    Platform.LIGHT: frozenset({"floodlight", "indicator", "night_light", "backlight"}),
    Platform.COVER: frozenset({"blind"}),
    Platform.ALARM_CONTROL_PANEL: frozenset({"alarm"}),
}
_FIXED_KEYS = {
    (Platform.EVENT, "alarm_message"): "doorbell_message",
    (Platform.EVENT, "doorbell_pic"): "doorbell_picture",
    (Platform.BUTTON, "switch_usb6"): "snooze",
    (Platform.SIREN, "alarm_switch"): "siren",
    (Platform.SIREN, "siren_switch"): "siren",
}


def assign_translation_keys(defs: list[EntityDef]) -> None:
    """Give every named entity a translation key so the UI can localise it.

    The English fallback stays in ``name``; translations live in ``strings.json`` under
    ``entity.<platform>.<key>.name``. Numbered entities (second light, zone 3, …) share one
    key with an ``n`` placeholder.
    """
    for d in defs:
        if d.name is None or d.translation_key:
            continue
        if (d.platform, d.key) in _FIXED_KEYS:
            d.translation_key = _FIXED_KEYS[(d.platform, d.key)]
            continue
        base, idx = C.split_channel(d.key)
        numbered = _NUMBERED_KEYS.get((d.platform, base))
        if numbered and idx is not None:
            d.translation_key = numbered
            d.meta["translation_placeholders"] = {"n": str(idx)}
            continue
        if d.key in _KNOWN_KEYS.get(d.platform, frozenset()):
            d.translation_key = d.key
