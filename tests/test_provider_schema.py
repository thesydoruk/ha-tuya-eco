"""DP schema derivation by the cloud providers when the spec and the DP relation disagree on codes."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.tuya_eco.providers.smartlife import SmartLifeProvider, spec_code_for
from custom_components.tuya_eco.providers.tuya_iot import build_schema


def _fn(code: str, type_: str, values: str = "{}") -> SimpleNamespace:
    return SimpleNamespace(code=code, type=type_, values=values)


def _relation(dp_id: int, code: str, value_type: str, value_desc: str = "{}") -> dict:
    return {"dpId": dp_id, "statusCode": code, "valueType": value_type, "valueDesc": value_desc, "supportLocal": True}


class _Api:
    def __init__(self, relations: list[dict]) -> None:
        self._relations = relations

    def get(self, path: str) -> dict:
        assert path.endswith("/status")
        return {"success": True, "result": {"productKey": "erypqhztvpazpg2m", "dpStatusRelationDTOS": self._relations}}


def test_spec_code_for_prefers_exact_match_then_channel_aliases() -> None:
    functions = {"switch": _fn("switch", "Boolean"), "switch_2": _fn("switch_2", "Boolean")}
    status_range = {"switch": _fn("switch", "Boolean"), "cur_power": _fn("cur_power", "Integer")}
    assert spec_code_for("switch", functions, status_range) == "switch"
    assert spec_code_for("switch_2", functions, status_range) == "switch_2"
    assert spec_code_for("switch_1", functions, status_range) == "switch"  # channel suffix stripped
    assert spec_code_for("cur_power", functions, status_range) == "cur_power"
    assert spec_code_for("switch_led", {"switch_led_1": _fn("switch_led_1", "Boolean")}, {}) == "switch_led_1"
    assert spec_code_for("fault", functions, status_range) is None


def test_smartlife_relay_named_switch_in_spec_but_switch_1_in_relation_is_writable() -> None:
    """WIFI Smart Meter Pro (dlq): function/status_range say `switch`, dpStatusRelationDTOS says `switch_1`."""
    device = SimpleNamespace(
        id="bf000000000000000000ab",
        name="WIFI Smart Meter Pro",
        local_key="0123456789abcdef",
        category="dlq",
        product_id="erypqhztvpazpg2m",
        function={
            "switch": _fn("switch", "Boolean"),
            "countdown_1": _fn("countdown_1", "Integer", '{"min":0,"max":86400,"step":1,"unit":"s"}'),
            "relay_status": _fn("relay_status", "Enum", '{"range":["power_off","power_on","last"]}'),
        },
        status_range={
            "switch": _fn("switch", "Boolean"),
            "countdown_1": _fn("countdown_1", "Integer"),
            "cur_power": _fn("cur_power", "Integer", '{"min":0,"max":260000,"scale":1,"step":1,"unit":"W"}'),
            "relay_status": _fn("relay_status", "Enum"),
        },
        status={"switch": True, "cur_power": 36},
    )
    manager = SimpleNamespace(
        customer_api=_Api(
            [
                _relation(1, "switch_1", "Boolean"),
                _relation(9, "countdown_1", "Integer", '{"min":0,"max":86400,"step":1,"unit":"s"}'),
                _relation(19, "cur_power", "Integer", '{"min":0,"max":260000,"scale":1,"step":1,"unit":"W"}'),
                _relation(38, "relay_status", "Enum", '{"range":["power_off","power_on","last"]}'),
            ]
        )
    )
    provider = SmartLifeProvider(SimpleNamespace())  # type: ignore[arg-type]

    cloud = provider._convert(manager, device)

    assert cloud.dp_schema[1].code == "switch_1"  # the relation code is kept: reports arrive under it
    assert cloud.dp_schema[1].read_only is False
    assert cloud.dp_schema[9].read_only is False
    assert cloud.dp_schema[19].read_only is True
    assert cloud.dp_schema[38].read_only is False
    assert cloud.dp_schema[38].values == {"range": ["power_off", "power_on", "last"]}


def test_smartlife_dp_unknown_to_the_spec_stays_read_only() -> None:
    device = SimpleNamespace(
        id="bf000000000000000000ac",
        name="Dev",
        function={"switch_1": _fn("switch_1", "Boolean")},
        status_range={"switch_1": _fn("switch_1", "Boolean")},
        status={},
    )
    manager = SimpleNamespace(
        customer_api=_Api([_relation(1, "switch_1", "Boolean"), _relation(26, "fault", "Bitmap")])
    )
    cloud = SmartLifeProvider(SimpleNamespace())._convert(manager, device)  # type: ignore[arg-type]
    assert cloud.dp_schema[1].read_only is False
    assert cloud.dp_schema[26].read_only is True


def test_tuya_iot_schema_marks_dp_writable_when_function_uses_another_code() -> None:
    spec = {
        "status": [
            {"code": "switch", "dp_id": 1, "type": "Boolean", "values": "{}"},
            {"code": "cur_power", "dp_id": 19, "type": "Integer", "values": '{"unit":"W","scale":1}'},
        ],
        "functions": [
            {"code": "switch_1", "dp_id": 1, "type": "Boolean", "values": "{}"},
        ],
    }
    schema = build_schema(spec)
    assert schema[1].code == "switch"
    assert schema[1].read_only is False
    assert schema[19].read_only is True
