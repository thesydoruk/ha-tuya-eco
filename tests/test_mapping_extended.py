"""Mapping tests for the platforms added after the first release."""

from __future__ import annotations

from homeassistant.const import Platform

from custom_components.tuya_eco.mapping import build_entities
from custom_components.tuya_eco.providers.base import CloudDevice, DpSpec, DpType


def _dev(category: str, specs: list[tuple[int, str, DpType, dict, bool]]) -> CloudDevice:
    schema = {dp: DpSpec(dp_id=dp, code=code, type=t, values=v, read_only=ro) for dp, code, t, v, ro in specs}
    return CloudDevice(id="dev1", name="Dev", local_key="k", category=category, dp_schema=schema)


def _by_platform(defs):
    out: dict[Platform, list] = {}
    for d in defs:
        out.setdefault(d.platform, []).append(d)
    return out


def test_humidifier() -> None:
    device = _dev(
        "jsq",
        [
            (1, "switch", DpType.BOOLEAN, {}, False),
            (2, "humidity_set", DpType.INTEGER, {"min": 30, "max": 80, "scale": 0, "step": 5, "unit": "%"}, False),
            (3, "humidity_current", DpType.INTEGER, {"min": 0, "max": 100}, True),
            (4, "mode", DpType.ENUM, {"range": ["auto", "sleep", "manual"]}, False),
            (5, "switch_sound", DpType.BOOLEAN, {}, False),
        ],
    )
    defs = _by_platform(build_entities(device))
    hum = defs[Platform.HUMIDIFIER][0]
    assert hum.dps == {"switch": 1, "current": 3, "target": 2, "mode": 4}
    assert hum.meta["device_class"] == "humidifier"
    assert hum.meta["target"]["max"] == 80
    assert [d.key for d in defs[Platform.SWITCH]] == ["switch_sound"]


def test_dehumidifier_and_fan_share_cs() -> None:
    device = _dev(
        "cs",
        [
            (1, "switch", DpType.BOOLEAN, {}, False),
            (2, "dehumidify_set_value", DpType.INTEGER, {"min": 40, "max": 70}, False),
            (3, "humidity_indoor", DpType.INTEGER, {"min": 0, "max": 100}, True),
            (4, "fan_speed_enum", DpType.ENUM, {"range": ["low", "high"]}, False),
            (5, "fault", DpType.BITMAP, {"label": ["tankfull", "defrost"]}, True),
        ],
    )
    defs = _by_platform(build_entities(device))
    assert defs[Platform.HUMIDIFIER][0].meta["device_class"] == "dehumidifier"
    fan = defs[Platform.FAN][0]
    assert fan.dps == {"speed": 4}  # switch was claimed by the humidifier
    assert defs[Platform.BINARY_SENSOR][0].device_class == "problem"


def test_vacuum() -> None:
    device = _dev(
        "sd",
        [
            (1, "power_go", DpType.BOOLEAN, {}, False),
            (2, "pause", DpType.BOOLEAN, {}, False),
            (3, "switch_charge", DpType.BOOLEAN, {}, False),
            (4, "mode", DpType.ENUM, {"range": ["smart", "chargego", "zone"]}, False),
            (5, "status", DpType.ENUM, {"range": ["standby", "cleaning", "charging"]}, True),
            (6, "suction", DpType.ENUM, {"range": ["gentle", "normal", "strong"]}, False),
            (7, "electricity_left", DpType.INTEGER, {"min": 0, "max": 100, "unit": "%"}, True),
            (8, "reset_filter", DpType.BOOLEAN, {}, False),
            (9, "seek", DpType.BOOLEAN, {}, False),
        ],
    )
    defs = _by_platform(build_entities(device))
    vac = defs[Platform.VACUUM][0]
    assert vac.dps == {"power_go": 1, "status": 5, "pause": 2, "switch_charge": 3, "mode": 4, "seek": 9, "fan_speed": 6}
    assert vac.meta["fan_speeds"] == ["gentle", "normal", "strong"]
    assert [d.key for d in defs[Platform.SELECT]] == ["mode"]  # still exposed for zone cleaning
    assert [d.key for d in defs[Platform.BUTTON]] == ["reset_filter"]
    assert defs[Platform.SENSOR][0].device_class == "battery"
    assert Platform.SWITCH not in defs


def test_irrigation_valves() -> None:
    device = _dev(
        "sfkzq",
        [
            (1, "switch_1", DpType.BOOLEAN, {}, False),
            (2, "switch_2", DpType.BOOLEAN, {}, False),
            (11, "countdown_1", DpType.INTEGER, {"min": 0, "max": 86400, "unit": "s"}, False),
            (20, "battery_percentage", DpType.INTEGER, {"min": 0, "max": 100}, True),
        ],
    )
    defs = _by_platform(build_entities(device))
    valves = {d.key: d for d in defs[Platform.VALVE]}
    assert set(valves) == {"switch_1", "switch_2"}
    assert valves["switch_2"].name == "Zone 2"
    assert Platform.SWITCH not in defs
    assert defs[Platform.NUMBER][0].device_class == "duration"


def test_siren_and_alarm_panel() -> None:
    siren = _dev(
        "sgbj",
        [
            (1, "alarm_switch", DpType.BOOLEAN, {}, False),
            (2, "alarm_volume", DpType.ENUM, {"range": ["low", "high"]}, False),
        ],
    )
    defs = _by_platform(build_entities(siren))
    assert defs[Platform.SIREN][0].dps == {"switch": 1}
    assert defs[Platform.SELECT][0].key == "alarm_volume"

    panel = _dev(
        "mal",
        [
            (1, "master_mode", DpType.ENUM, {"range": ["disarmed", "arm", "home", "sos"]}, False),
            (2, "master_state", DpType.ENUM, {"range": ["normal", "alarm"]}, True),
            (3, "alarm_msg", DpType.RAW, {}, True),
            (4, "switch_alarm_sound", DpType.BOOLEAN, {}, False),
        ],
    )
    defs = _by_platform(build_entities(panel))
    alarm = defs[Platform.ALARM_CONTROL_PANEL][0]
    assert alarm.dps == {"mode": 1, "state": 2, "message": 3}
    assert defs[Platform.BINARY_SENSOR][0].key == "master_state"


def test_scene_switch_events() -> None:
    device = _dev(
        "wxkg",
        [
            (1, "switch_mode1", DpType.ENUM, {"range": ["click", "double_click", "press"]}, True),
            (2, "switch_mode2", DpType.ENUM, {"range": ["click", "double_click", "press"]}, True),
            (10, "battery_percentage", DpType.INTEGER, {"min": 0, "max": 100}, True),
        ],
    )
    defs = _by_platform(build_entities(device))
    events = {d.key: d for d in defs[Platform.EVENT]}
    assert set(events) == {"button_1", "button_2"}
    assert events["button_1"].meta["event_types"] == ["click", "double_click", "press"]
    assert events["button_1"].device_class == "button"


def test_curtain_switch_with_direction_mode_and_tilt() -> None:
    device = _dev(
        "clkg",
        [
            (1, "control", DpType.ENUM, {"range": ["open", "stop", "close"]}, False),
            (2, "percent_control", DpType.INTEGER, {"min": 0, "max": 100}, False),
            (3, "control_back_mode", DpType.ENUM, {"range": ["forward", "back"]}, False),
            (4, "angle_horizontal", DpType.INTEGER, {"min": 0, "max": 180}, False),
            (5, "switch_backlight", DpType.BOOLEAN, {}, False),
        ],
    )
    defs = _by_platform(build_entities(device))
    cover = defs[Platform.COVER][0]
    assert cover.dps == {"control": 1, "position": 2, "back_mode": 3, "tilt": 4}
    assert defs[Platform.LIGHT][0].key == "backlight"
    assert Platform.SELECT not in defs  # control_back_mode is used by the cover


def test_curtain_robot_mach_operate() -> None:
    device = _dev(
        "jdcljqr",
        [
            (1, "mach_operate", DpType.ENUM, {"range": ["FZ", "ZZ", "STOP"]}, False),
            (2, "position", DpType.INTEGER, {"min": 0, "max": 100}, False),
        ],
    )
    cover = build_entities(device)[0]
    assert cover.platform == Platform.COVER
    assert cover.meta["open_cmd"] == "FZ"


def test_air_conditioner_climate_with_swing_and_duplicate_modes() -> None:
    device = _dev(
        "kt",
        [
            (1, "switch", DpType.BOOLEAN, {}, False),
            (2, "temp_set", DpType.INTEGER, {"min": 16, "max": 30, "scale": 0, "step": 1, "unit": "℃"}, False),
            (3, "temp_current", DpType.INTEGER, {"min": -20, "max": 50}, True),
            (4, "mode", DpType.ENUM, {"range": ["auto", "cold", "hot", "wet", "wind", "freeze"]}, False),
            (5, "fan_speed_enum", DpType.ENUM, {"range": ["auto", "low", "high"]}, False),
            (6, "switch_vertical", DpType.BOOLEAN, {}, False),
            (7, "humidity_set", DpType.INTEGER, {"min": 30, "max": 70}, False),
        ],
    )
    defs = _by_platform(build_entities(device))
    climate = defs[Platform.CLIMATE][0]
    # cold + freeze both map to "cool" → both become presets
    assert climate.meta["hvac_modes"] == {"auto": "heat_cool", "hot": "heat", "wet": "dry", "wind": "fan_only"}
    assert climate.meta["presets"] == ["cold", "freeze"]
    assert climate.dps["swing_v"] == 6 and climate.dps["humidity_set"] == 7
    assert climate.meta["switch_only_hvac"] == "cool"
    assert Platform.SWITCH not in defs and Platform.NUMBER not in defs


def test_camera_floodlight_and_doorbell() -> None:
    device = _dev(
        "sp",
        [
            (1, "floodlight_switch", DpType.BOOLEAN, {}, False),
            (2, "floodlight_lightness", DpType.INTEGER, {"min": 1, "max": 100}, False),
            (3, "basic_indicator", DpType.BOOLEAN, {}, False),
            (4, "doorbell_pic", DpType.RAW, {}, True),
            (5, "device_restart", DpType.BOOLEAN, {}, False),
            (6, "basic_flip", DpType.BOOLEAN, {}, False),
        ],
    )
    defs = _by_platform(build_entities(device))
    lights = {d.key: d for d in defs[Platform.LIGHT]}
    assert lights["floodlight"].dps == {"switch": 1, "brightness": 2}
    assert lights["indicator"].entity_category == "config"
    assert defs[Platform.EVENT][0].meta["decode"] == "b64_raw"
    assert defs[Platform.BUTTON][0].device_class == "restart"
    assert defs[Platform.SWITCH][0].key == "basic_flip"


def test_illuminance_sensor_vs_light_brightness() -> None:
    sensor = _dev("wsdcg", [(1, "bright_value", DpType.INTEGER, {"min": 0, "max": 10000, "unit": "lux"}, True)])
    assert build_entities(sensor)[0].device_class == "illuminance"
    light = _dev(
        "dj",
        [
            (1, "switch_led", DpType.BOOLEAN, {}, False),
            (2, "bright_value", DpType.INTEGER, {"min": 10, "max": 1000}, False),
        ],
    )
    assert build_entities(light)[0].platform == Platform.LIGHT


def test_translation_keys_and_placeholders() -> None:
    plug = _dev(
        "cz",
        [
            (1, "switch_1", DpType.BOOLEAN, {}, False),
            (2, "switch_2", DpType.BOOLEAN, {}, False),
            (41, "child_lock", DpType.BOOLEAN, {}, False),
            (38, "relay_status", DpType.ENUM, {"range": ["power_off", "power_on"]}, False),
        ],
    )
    by_key = {d.key: d for d in build_entities(plug)}
    assert by_key["child_lock"].translation_key == "child_lock"
    assert by_key["relay_status"].translation_key == "relay_status"
    assert by_key["switch_2"].translation_key == "switch_2"

    dimmer = _dev(
        "tgkg",
        [
            (1, "switch_led_1", DpType.BOOLEAN, {}, False),
            (7, "switch_led_2", DpType.BOOLEAN, {}, False),
        ],
    )
    lights = {d.key: d for d in build_entities(dimmer)}
    assert lights["light_1"].translation_key == "light_n"
    assert lights["light_1"].meta["translation_placeholders"] == {"n": "1"}
    assert lights["light_2"].meta["translation_placeholders"] == {"n": "2"}

    valves = _dev("sfkzq", [(1, "switch_1", DpType.BOOLEAN, {}, False), (2, "switch_2", DpType.BOOLEAN, {}, False)])
    zone2 = next(d for d in build_entities(valves) if d.key == "switch_2")
    assert zone2.translation_key == "zone_n" and zone2.meta["translation_placeholders"] == {"n": "2"}


def test_every_translation_key_has_an_english_string() -> None:
    import json
    from pathlib import Path

    strings = json.loads(
        (Path(__file__).resolve().parents[1] / "custom_components" / "tuya_eco" / "strings.json").read_text(
            encoding="utf-8"
        )
    )["entity"]
    from custom_components.tuya_eco.mapping import codes as C

    for platform, table in (
        ("switch", C.SWITCHES),
        ("binary_sensor", C.BINARY_SENSORS),
        ("number", C.NUMBERS),
        ("select", C.SELECTS),
        ("button", C.BUTTONS),
    ):
        missing = [code for code in table if code not in strings[platform]]
        assert not missing, (platform, missing)
    for key in ("light_n", "floodlight", "indicator", "night_light", "backlight"):
        assert key in strings["light"]
    for key in ("curtain_n", "door_n", "blind"):
        assert key in strings["cover"]
