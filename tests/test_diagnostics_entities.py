"""Device diagnostics on entities: connection attributes, fault bits, power-on behaviour."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from homeassistant.const import Platform

from custom_components.tuya_eco.binary_sensor import EcoBinarySensor
from custom_components.tuya_eco.mapping import PowerMemory, build_entities
from custom_components.tuya_eco.mapping.codes import clean_unit
from custom_components.tuya_eco.providers.base import CloudDevice, DpSpec, DpType
from custom_components.tuya_eco.select import EcoSelect
from custom_components.tuya_eco.sensor import EcoConnectionSensor


def _dev(category: str, specs: list[tuple[int, str, DpType, dict, bool]], product_id: str = "") -> CloudDevice:
    schema = {dp: DpSpec(dp_id=dp, code=code, type=t, values=v, read_only=ro) for dp, code, t, v, ro in specs}
    return CloudDevice(id="dev1", name="Dev", local_key="k", category=category, product_id=product_id, dp_schema=schema)


def _keys(defs) -> dict[tuple[Platform, str], Any]:
    return {(d.platform, d.key): d for d in defs}


class _FakeDevice:
    def __init__(self, dps: dict[str, Any]) -> None:
        self.id = "dev1"
        self.name = "Dev"
        self.dps = dps
        self.written: list[dict] = []
        self.info = SimpleNamespace(name="Dev", product_name="", product_id="", category="")
        self.available = True
        self.transport = "local"
        self.host = "192.168.1.20"
        self.version = 3.4
        self.last_error = None
        self.last_update = 1_700_000_000.0

    def get_dp(self, dp_id: int) -> Any:
        return self.dps.get(str(dp_id))

    async def async_set_dps(self, dps: dict) -> None:
        self.written.append(dps)


def _entity(cls, definition, dps: dict[str, Any]):
    device = _FakeDevice(dps)
    hub = SimpleNamespace(provider=SimpleNamespace(name="Ledvance"), cloud_fallback=False)
    return cls(hub, device, definition), device


def _energy_plug() -> CloudDevice:
    return _dev(
        "cz",
        [
            (1, "switch_1", DpType.BOOLEAN, {}, False),
            (19, "cur_power", DpType.INTEGER, {"min": 0, "max": 80000, "scale": 1, "step": 1, "unit": "W"}, True),
            (26, "fault", DpType.BITMAP, {"label": ["ov_cr", "ov_vol", "ov_pwr", "ls_cr", "ls_vol", "ls_pow"]}, True),
            (42, "cycle_time", DpType.STRING, {}, False),
        ],
    )


# ---------------------------------------------------------------------- connection sensor


def test_connection_sensor_knows_which_dps_are_mapped() -> None:
    conn = _keys(build_entities(_energy_plug()))[(Platform.SENSOR, "connection")]
    assert conn.meta["mapped_dps"] == [1, 19, 26]  # cycle_time (42) is ignored on purpose
    assert conn.meta["codes"]["42"] == "cycle_time"


def test_connection_sensor_attributes_list_link_and_unmapped_dps() -> None:
    conn = _keys(build_entities(_energy_plug()))[(Platform.SENSOR, "connection")]
    entity, _ = _entity(
        EcoConnectionSensor, conn, {"1": True, "19": 120, "26": 0, "42": "", "101": 7, "102": "x" * 300}
    )
    attrs = entity.extra_state_attributes
    assert attrs["host"] == "192.168.1.20"
    assert attrs["protocol_version"] == 3.4
    assert attrs["last_error"] is None
    assert attrs["last_update"].startswith("2023-11-14")
    assert list(attrs["unmapped_dps"]) == ["cycle_time", "dp_101", "dp_102"]
    assert attrs["unmapped_dps"]["dp_101"] == 7
    assert len(attrs["unmapped_dps"]["dp_102"]) == 255
    assert entity.native_value == "local"


# ---------------------------------------------------------------------- fault bits


def test_fault_binary_sensor_lists_active_bits() -> None:
    fault = _keys(build_entities(_energy_plug()))[(Platform.BINARY_SENSOR, "fault")]
    assert fault.meta["labels"][1] == "ov_vol"
    entity, device = _entity(EcoBinarySensor, fault, {"26": 0})
    assert entity.is_on is False
    assert entity.extra_state_attributes == {"active": []}
    device.dps["26"] = 0b100010
    assert entity.is_on is True
    assert entity.extra_state_attributes == {"active": ["ov_vol", "ls_pow"]}


def test_plain_binary_sensor_has_no_attributes() -> None:
    device = _dev("mcs", [(1, "doorcontact_state", DpType.BOOLEAN, {}, True)])
    door = _keys(build_entities(device))[(Platform.BINARY_SENSOR, "doorcontact_state")]
    entity, _ = _entity(EcoBinarySensor, door, {"1": True})
    assert entity.extra_state_attributes is None


# ---------------------------------------------------------------------- units


def test_bogus_units_are_dropped_and_aliases_normalised() -> None:
    assert clean_unit("x") is None
    assert clean_unit("") is None
    assert clean_unit(None) is None
    assert clean_unit("lux") == "lx"
    assert clean_unit("℃") == "°C"
    assert clean_unit("cm") == "cm"
    device = _dev(
        "hps",
        [
            (2, "sensitivity", DpType.INTEGER, {"min": 0, "max": 10, "scale": 0, "step": 1, "unit": "x"}, False),
            (5, "mystery_level", DpType.INTEGER, {"min": 0, "max": 100, "scale": 0, "step": 1, "unit": "lux"}, True),
        ],
    )
    defs = _keys(build_entities(device))
    assert defs[(Platform.NUMBER, "sensitivity")].unit is None
    assert defs[(Platform.SENSOR, "mystery_level")].unit == "lx"


# ---------------------------------------------------------------------- power-on behaviour of lights


def _bulb_with_power_memory() -> CloudDevice:
    return _dev(
        "dj",
        [
            (20, "switch_led", DpType.BOOLEAN, {}, False),
            (21, "work_mode", DpType.ENUM, {"range": ["white", "colour", "scene", "music"]}, False),
            (22, "bright_value", DpType.INTEGER, {"min": 10, "max": 1000, "scale": 0, "step": 1}, False),
            (23, "temp_value", DpType.INTEGER, {"min": 0, "max": 1000, "scale": 0, "step": 1}, False),
            (24, "colour_data", DpType.STRING, {"maxlen": 255}, False),
            (33, "power_memory", DpType.RAW, {}, False),
            (34, "do_not_disturb", DpType.BOOLEAN, {}, False),
        ],
    )


def test_power_memory_codec_roundtrip() -> None:
    memory = PowerMemory.decode("AAEACAO2A+gD6AML")  # as reported by a Ledvance bulb
    assert memory == PowerMemory(mode=1, hue=8, saturation=950, value=1000, brightness=1000, color_temp=779)
    assert memory.encode() == "AAEACAO2A+gD6AML"
    assert PowerMemory.decode("AAEAAAAAAAAAAAAA").mode == 1
    assert PowerMemory.decode("not base64!") is None
    assert PowerMemory.decode("") is None
    assert PowerMemory.decode(5) is None
    assert PowerMemory.decode("AA==") is None  # one byte: no mode
    assert PowerMemory(mode=2, hue=70000).encode() == PowerMemory(mode=2, hue=0xFFFF).encode()


def test_bulb_gets_power_on_behavior_select() -> None:
    defs = _keys(build_entities(_bulb_with_power_memory()))
    select = defs[(Platform.SELECT, "power_memory")]
    assert select.dps == {"value": 33, "brightness": 22, "color_temp": 23, "color": 24, "mode": 21}
    assert select.meta["options"] == ["default", "memory", "custom"]
    assert select.translation_key == "power_memory"
    assert select.entity_category == "config"
    # the raw DP is claimed: no stray entity for it, and the connection sensor sees it as mapped
    assert 33 in defs[(Platform.SENSOR, "connection")].meta["mapped_dps"]
    assert (Platform.SWITCH, "do_not_disturb") in defs


async def test_power_on_behavior_select_reads_mode_and_writes_payload() -> None:
    select = _keys(build_entities(_bulb_with_power_memory()))[(Platform.SELECT, "power_memory")]
    entity, device = _entity(EcoSelect, select, {"33": "AAEACAO2A+gD6AML", "22": 500, "23": 300, "24": "005a03e801f4"})
    assert entity.current_option == "memory"
    device.dps["33"] = "garbage"
    assert entity.current_option is None
    device.dps["33"] = "AAEACAO2A+gD6AML"
    await entity.async_select_option("default")
    # only the mode byte changes, the stored custom state is kept
    assert device.written == [{33: "AAAACAO2A+gD6AML"}]
    await entity.async_select_option("custom")
    assert PowerMemory.decode(device.written[1][33]) == PowerMemory(
        mode=2, hue=90, saturation=1000, value=500, brightness=500, color_temp=300
    )


async def test_power_on_behavior_custom_without_light_state() -> None:
    select = _keys(build_entities(_bulb_with_power_memory()))[(Platform.SELECT, "power_memory")]
    entity, device = _entity(EcoSelect, select, {})
    assert entity.current_option is None
    await entity.async_select_option("custom")
    assert PowerMemory.decode(device.written[0][33]) == PowerMemory(mode=2)
