"""Tests of product profiles (replace / extend) and the v1 light scene select."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from homeassistant.const import Platform
import pytest

from custom_components.tuya_eco.button import EcoButton
from custom_components.tuya_eco.mapping import build_entities, profiles as P
from custom_components.tuya_eco.mapping.codes import LIGHT_SCENES_V1
from custom_components.tuya_eco.providers.base import CloudDevice, DpSpec, DpType
from custom_components.tuya_eco.select import EcoSelect
from custom_components.tuya_eco.switch import EcoSwitch

_INT10 = {"min": 0, "max": 10, "scale": 0, "step": 1}


def _dev(category: str, specs: list[tuple[int, str, DpType, dict, bool]], product_id: str = "") -> CloudDevice:
    schema = {dp: DpSpec(dp_id=dp, code=code, type=t, values=v, read_only=ro) for dp, code, t, v, ro in specs}
    return CloudDevice(id="dev1", name="Dev", local_key="k", category=category, product_id=product_id, dp_schema=schema)


def _keys(defs) -> dict[tuple[Platform, str], Any]:
    return {(d.platform, d.key): d for d in defs}


def _ledvance_v1_bulb() -> CloudDevice:
    return _dev(
        "dj",
        [
            (20, "switch_led", DpType.BOOLEAN, {}, False),
            (21, "work_mode", DpType.ENUM, {"range": ["white", "colour", "scene", "music"]}, False),
            (22, "bright_value", DpType.INTEGER, {"min": 10, "max": 1000, "scale": 0, "step": 1}, False),
            (23, "temp_value", DpType.INTEGER, {"min": 0, "max": 1000, "scale": 0, "step": 1}, False),
            (24, "colour_data", DpType.STRING, {"maxlen": 255}, False),
            (25, "scene_data", DpType.STRING, {"maxlen": 255}, False),
            (26, "countdown", DpType.INTEGER, {"min": 0, "max": 86400, "scale": 0, "step": 1, "unit": "s"}, False),
            (101, "remote_switch", DpType.BOOLEAN, {}, False),
        ],
    )


def test_v1_bulb_gets_scene_select_and_remote_switch() -> None:
    defs = _keys(build_entities(_ledvance_v1_bulb()))
    scene = defs[(Platform.SELECT, "scene")]
    assert scene.dps == {"value": 25, "mode": 21}
    assert scene.translation_key == "scene"
    assert list(scene.meta["scenes"]) == list(LIGHT_SCENES_V1)
    remote = defs[(Platform.SWITCH, "remote_switch")]
    assert remote.dps == {"switch": 101}
    assert remote.entity_category == "config"
    assert remote.translation_key == "remote_switch"


def test_v2_strip_has_no_scene_select() -> None:
    device = _dev(
        "dj",
        [
            (20, "switch_led", DpType.BOOLEAN, {}, False),
            (21, "work_mode", DpType.ENUM, {"range": ["white", "colour", "scene", "music"]}, False),
            (25, "scene_data_v2", DpType.JSON, {}, False),
        ],
    )
    assert (Platform.SELECT, "scene") not in _keys(build_entities(device))


def test_scene_numbers_are_unique() -> None:
    assert len({data[:2] for data in LIGHT_SCENES_V1.values()}) == len(LIGHT_SCENES_V1)


# ---------------------------------------------------------------------- profiles


def _pc_cloud_device() -> CloudDevice:
    """The PC power card as the Smart Life cloud describes it."""
    return _dev(
        "cz",
        [
            (1, "switch_1", DpType.BOOLEAN, {}, False),
            (7, "switch_usb1", DpType.BOOLEAN, {}, False),
            (8, "switch_usb2", DpType.BOOLEAN, {}, False),
            (38, "relay_status", DpType.ENUM, {"range": ["power_off", "power_on"]}, False),
            (40, "child_lock", DpType.BOOLEAN, {}, False),
        ],
        product_id="rtbhfbuii82scjrp",
    )


def test_extend_profile_merges_schema_and_adds_entities() -> None:
    P._load_all.cache_clear()
    defs = _keys(build_entities(_pc_cloud_device()))
    assert (Platform.SWITCH, "switch_usb1") not in defs
    assert (Platform.SWITCH, "switch_usb2") not in defs
    assert defs[(Platform.SWITCH, "switch_1")].name is None  # main switch keeps the device name
    assert defs[(Platform.SWITCH, "rf_pairing")].dps == {"switch": 7}
    assert defs[(Platform.SWITCH, "buzzer")].dps == {"switch": 8}
    assert defs[(Platform.SELECT, "relay_status")].meta["options"] == ["off", "on"]
    assert defs[(Platform.BUTTON, "reset")].meta["press_value"] == "Reset"
    assert defs[(Platform.BUTTON, "force_reset")].meta["press_value"] == "forceReset"
    remote = defs[(Platform.SWITCH, "rf_remote_control")]
    assert remote.dps == {"switch": 102}
    assert remote.meta == {"on_value": "on", "off_value": "off"}
    assert (Platform.SENSOR, "connection") in defs
    # every DP is used by at most one generated entity (the two buttons share 101 on purpose)
    used = [dp for d in defs.values() if d.platform != Platform.BUTTON for dp in d.dps.values()]
    assert len(used) == len(set(used))


def test_presence_sensor_profile_adds_vendor_dps() -> None:
    P._load_all.cache_clear()
    device = _dev(
        "hps",
        [
            (1, "presence_state", DpType.ENUM, {"range": ["none", "presence"]}, True),
            (2, "sensitivity", DpType.INTEGER, _INT10, False),
            (3, "near_detection", DpType.INTEGER, {"min": 0, "max": 1000, "scale": 0, "step": 10, "unit": "cm"}, False),
            (4, "far_detection", DpType.INTEGER, {"min": 0, "max": 600, "scale": 0, "step": 10, "unit": "cm"}, False),
            (101, "illuminance_value", DpType.INTEGER, {"min": 0, "max": 10000, "scale": 0, "step": 1}, True),
        ],
        product_id="uykwakpl6rbxvjdd",
    )
    defs = _keys(build_entities(device))
    assert defs[(Platform.BINARY_SENSOR, "presence_state")].device_class == "occupancy"
    # the cloud's generic names for DPs 2-4 are replaced by what they mean on this device
    for old_key in ("sensitivity", "near_detection", "far_detection"):
        assert (Platform.NUMBER, old_key) not in defs
    assert defs[(Platform.NUMBER, "move_sensitivity")].dps == {"value": 2}
    assert defs[(Platform.NUMBER, "move_sensitivity")].unit is None  # the cloud says "x"
    assert defs[(Platform.NUMBER, "move_distance")].dps == {"value": 3}
    assert defs[(Platform.NUMBER, "move_distance")].meta["max"] == 1000
    assert defs[(Platform.NUMBER, "static_distance")].dps == {"value": 4}
    assert defs[(Platform.NUMBER, "static_distance")].unit == "cm"
    distance = defs[(Platform.SENSOR, "target_dis_closest")]
    assert distance.dps == {"value": 122}
    assert distance.device_class == "distance"
    assert distance.unit == "cm"
    assert distance.translation_key == "target_dis_closest"
    movement = defs[(Platform.SENSOR, "movement_state")]
    assert movement.dps == {"value": 116}
    assert "breathe" in movement.meta["options"]
    assert defs[(Platform.LIGHT, "indicator")].dps == {"switch": 103}
    assert defs[(Platform.BUTTON, "factory_reset")].meta["enabled_default"] is False
    assert defs[(Platform.NUMBER, "none_delay_time")].unit == "s"
    assert defs[(Platform.NUMBER, "small_move_distance")].unit == "cm"
    assert defs[(Platform.SELECT, "alarm_volume")].meta["options"] == ["high", "middle", "low", "mute"]
    for key in ("adaptive_test", "small_move_self_test", "breath_self_test", "breath_false_detection"):
        assert defs[(Platform.SWITCH, key)].entity_category == "config"
    # every new entity is translatable
    for d in defs.values():
        assert d.name is None or d.translation_key, d.key


def test_replace_profile_still_replaces(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = P.parse_profile(
        {"entities": [{"platform": "switch", "key": "main", "dps": {"switch": 1}, "device_class": "outlet"}]}
    )
    monkeypatch.setattr(P, "device_profile", lambda device: profile)
    defs = build_entities(_pc_cloud_device())
    assert [(d.platform, d.key) for d in defs] == [(Platform.SWITCH, "main")]


def test_profile_entity_overrides_generated_key(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = P.parse_profile(
        {
            "mode": "extend",
            "entities": [{"platform": "switch", "key": "child_lock", "dps": {"switch": 40}, "icon": "mdi:lock"}],
        }
    )
    monkeypatch.setattr(P, "device_profile", lambda device: profile)
    defs = [d for d in build_entities(_pc_cloud_device()) if d.key == "child_lock"]
    assert len(defs) == 1
    assert defs[0].icon == "mdi:lock"


def test_bad_profile_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        P.parse_profile({"mode": "merge"})


# ---------------------------------------------------------------------- platform behaviour


class _FakeDevice:
    def __init__(self, dps: dict[int, Any]) -> None:
        self.id = "dev1"
        self.name = "Dev"
        self.dps = dps
        self.written: list[dict] = []
        self.info = SimpleNamespace(name="Dev", product_name="", product_id="", category="")
        self.available = True

    def get_dp(self, dp_id: int) -> Any:
        return self.dps.get(dp_id)

    async def async_set_dps(self, dps: dict) -> None:
        self.written.append(dps)


def _entity(cls, definition, dps: dict[int, Any]):
    device = _FakeDevice(dps)
    hub = SimpleNamespace(provider=SimpleNamespace(name="Ledvance"), cloud_fallback=False)
    return cls(hub, device, definition), device


async def test_scene_select_reads_by_scene_number_and_writes_mode() -> None:
    definition = _keys(build_entities(_ledvance_v1_bulb()))[(Platform.SELECT, "scene")]
    entity, device = _entity(EcoSelect, definition, {21: "white", 25: "000e0d0000000000000000c80000"})
    assert entity.current_option is None  # not in scene mode
    device.dps[21] = "scene"
    assert entity.current_option == "night"  # same scene number, other speed
    device.dps[25] = "ff0e0d"
    assert entity.current_option is None
    await entity.async_select_option("reading")
    assert device.written == [{21: "scene", 25: LIGHT_SCENES_V1["reading"]}]


async def test_string_switch_and_valued_button() -> None:
    P._load_all.cache_clear()
    defs = _keys(build_entities(_pc_cloud_device()))
    switch, device = _entity(EcoSwitch, defs[(Platform.SWITCH, "rf_remote_control")], {102: "off"})
    assert switch.is_on is False
    device.dps[102] = "on"
    assert switch.is_on is True
    await switch.async_turn_off()
    assert device.written == [{102: "off"}]
    button, device = _entity(EcoButton, defs[(Platform.BUTTON, "force_reset")], {})
    await button.async_press()
    assert device.written == [{101: "forceReset"}]
