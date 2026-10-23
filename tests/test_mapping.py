"""Tests of the DP schema → entity mapping."""

from __future__ import annotations

from homeassistant.const import Platform

from custom_components.tuya_eco.mapping import build_entities, synthesize_schema
from custom_components.tuya_eco.providers.base import CloudDevice, DpSpec, DpType


def _dev(category: str, specs: list[tuple[int, str, DpType, dict, bool]], product_id: str = "") -> CloudDevice:
    schema = {dp: DpSpec(dp_id=dp, code=code, type=t, values=v, read_only=ro) for dp, code, t, v, ro in specs}
    return CloudDevice(id="dev1", name="Dev", local_key="k", category=category, product_id=product_id, dp_schema=schema)


def _by_platform(defs):
    out: dict[Platform, list] = {}
    for d in defs:
        out.setdefault(d.platform, []).append(d)
    return out


def test_energy_plug() -> None:
    device = _dev(
        "cz",
        [
            (1, "switch_1", DpType.BOOLEAN, {}, False),
            (9, "countdown_1", DpType.INTEGER, {"min": 0, "max": 86400, "scale": 0, "step": 1, "unit": "s"}, False),
            (17, "add_ele", DpType.INTEGER, {"min": 0, "max": 50000, "scale": 3, "step": 100, "unit": "kwh"}, True),
            (18, "cur_current", DpType.INTEGER, {"min": 0, "max": 30000, "scale": 0, "step": 1, "unit": "mA"}, True),
            (19, "cur_power", DpType.INTEGER, {"min": 0, "max": 50000, "scale": 1, "step": 1, "unit": "W"}, True),
            (20, "cur_voltage", DpType.INTEGER, {"min": 0, "max": 5000, "scale": 1, "step": 1, "unit": "V"}, True),
            (38, "relay_status", DpType.ENUM, {"range": ["power_off", "power_on", "last"]}, False),
            (41, "child_lock", DpType.BOOLEAN, {}, False),
        ],
    )
    defs = _by_platform(build_entities(device))
    switches = {d.key: d for d in defs[Platform.SWITCH]}
    assert switches["switch_1"].name is None  # main switch takes the device name
    assert switches["switch_1"].device_class == "outlet"
    assert switches["child_lock"].entity_category == "config"
    sensors = {d.key: d for d in defs[Platform.SENSOR]}
    assert sensors["cur_power"].device_class == "power"
    assert sensors["cur_power"].meta["scale"] == 1
    assert sensors["add_ele"].state_class == "total_increasing"
    assert sensors["add_ele"].unit == "kWh"
    numbers = {d.key: d for d in defs[Platform.NUMBER]}
    assert numbers["countdown_1"].meta["max"] == 86400
    selects = {d.key: d for d in defs[Platform.SELECT]}
    assert selects["relay_status"].meta["options"] == ["power_off", "power_on", "last"]


def test_rgbcw_light() -> None:
    device = _dev(
        "dj",
        [
            (20, "switch_led", DpType.BOOLEAN, {}, False),
            (21, "work_mode", DpType.ENUM, {"range": ["white", "colour", "scene", "music"]}, False),
            (22, "bright_value_v2", DpType.INTEGER, {"min": 10, "max": 1000, "scale": 0, "step": 1}, False),
            (23, "temp_value_v2", DpType.INTEGER, {"min": 0, "max": 1000, "scale": 0, "step": 1}, False),
            (24, "colour_data_v2", DpType.JSON, {}, False),
            (25, "scene_data_v2", DpType.JSON, {}, False),
            (26, "countdown_1", DpType.INTEGER, {"min": 0, "max": 86400}, False),
        ],
    )
    defs = _by_platform(build_entities(device))
    light = defs[Platform.LIGHT][0]
    assert light.dps == {"switch": 20, "brightness": 22, "color_temp": 23, "color": 24, "mode": 21}
    assert light.meta["color"]["v2"] is True
    assert light.meta["brightness"]["max"] == 1000
    assert Platform.SWITCH not in defs  # switch_led was claimed by the light
    assert Platform.SELECT not in defs  # work_mode claimed, scene ignored
    assert [d.key for d in defs[Platform.NUMBER]] == ["countdown_1"]


def test_two_channel_dimmer() -> None:
    device = _dev(
        "tgkg",
        [
            (1, "switch_led_1", DpType.BOOLEAN, {}, False),
            (2, "bright_value_1", DpType.INTEGER, {"min": 10, "max": 1000}, False),
            (3, "brightness_min_1", DpType.INTEGER, {"min": 10, "max": 1000}, False),
            (7, "switch_led_2", DpType.BOOLEAN, {}, False),
            (8, "bright_value_2", DpType.INTEGER, {"min": 10, "max": 1000}, False),
        ],
    )
    defs = _by_platform(build_entities(device))
    lights = {d.key: d for d in defs[Platform.LIGHT]}
    assert set(lights) == {"light_1", "light_2"}
    assert lights["light_1"].dps["brightness_min"] == 3
    assert lights["light_2"].name == "Light 2"


def test_curtain_and_climate() -> None:
    curtain = _dev(
        "cl",
        [
            (1, "control", DpType.ENUM, {"range": ["open", "stop", "close"]}, False),
            (2, "percent_control", DpType.INTEGER, {"min": 0, "max": 100}, False),
            (3, "percent_state", DpType.INTEGER, {"min": 0, "max": 100}, True),
        ],
    )
    cover = build_entities(curtain)[0]
    assert cover.platform == Platform.COVER
    assert cover.dps == {"control": 1, "position": 2, "position_state": 3}

    thermostat = _dev(
        "wk",
        [
            (1, "switch", DpType.BOOLEAN, {}, False),
            (2, "temp_set", DpType.INTEGER, {"min": 50, "max": 300, "scale": 1, "step": 5, "unit": "℃"}, False),
            (3, "temp_current", DpType.INTEGER, {"min": -200, "max": 600, "scale": 1, "step": 1}, True),
            (4, "mode", DpType.ENUM, {"range": ["auto", "manual"]}, False),
            (6, "child_lock", DpType.BOOLEAN, {}, False),
        ],
    )
    defs = _by_platform(build_entities(thermostat))
    climate = defs[Platform.CLIMATE][0]
    assert climate.dps == {"temp_set": 2, "switch": 1, "temp_current": 3, "mode": 4}
    assert climate.meta["hvac_modes"] == {"auto": "auto", "manual": "heat"}
    assert climate.meta["unit"] == "°C"
    assert [d.key for d in defs[Platform.SWITCH]] == ["child_lock"]


def test_sensor_device_enum_and_bool() -> None:
    device = _dev(
        "mcs",
        [
            (1, "doorcontact_state", DpType.BOOLEAN, {}, True),
            (2, "battery_percentage", DpType.INTEGER, {"min": 0, "max": 100, "unit": "%"}, True),
            (3, "pir", DpType.ENUM, {"range": ["pir", "none"]}, True),
            (4, "status", DpType.ENUM, {"range": ["ok", "bad"]}, True),
        ],
    )
    defs = _by_platform(build_entities(device))
    binary = {d.key: d for d in defs[Platform.BINARY_SENSOR]}
    assert binary["doorcontact_state"].device_class == "door"
    assert binary["pir"].meta["on_values"] == ["pir"]
    sensors = {d.key: d for d in defs[Platform.SENSOR]}
    assert sensors["battery_percentage"].device_class == "battery"
    assert sensors["status"].meta["options"] == ["ok", "bad"]


def test_synthesized_schema_for_manual_devices() -> None:
    schema = synthesize_schema({"1": True, "2": 500, "101": "abc"})
    assert schema[1].type == DpType.BOOLEAN and not schema[1].read_only
    assert schema[2].type == DpType.INTEGER and schema[2].values["max"] == 1000
    assert schema[101].type == DpType.STRING
    device = CloudDevice(id="m", name="Manual", local_key="k", dp_schema=schema)
    defs = _by_platform(build_entities(device))
    assert defs[Platform.SWITCH][0].dps == {"switch": 1}
    assert Platform.SENSOR in defs


def test_entity_def_roundtrip() -> None:
    device = _dev("cz", [(1, "switch", DpType.BOOLEAN, {}, False)])
    original = build_entities(device)[0]
    restored = type(original).from_dict(original.as_dict())
    assert restored == original
