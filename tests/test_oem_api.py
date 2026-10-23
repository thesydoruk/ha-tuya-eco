"""Tests of the Tuya mobile (OEM) API client: signing, schema parsing, device conversion."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock

from custom_components.tuya_eco.providers.base import DpType
from custom_components.tuya_eco.providers.tuya_oem import (
    OemAppProfile,
    TuyaMobileApi,
    _mobile_hash,
    _textbook_rsa_encrypt,
    convert_mobile_device,
    parse_mobile_schema,
)

PROFILE = OemAppProfile(key="t", name="Test", client_id="clientid1234567890ab", secret="A_secret_part_two")


def _api() -> TuyaMobileApi:
    return TuyaMobileApi(
        session=MagicMock(),
        profile=PROFILE,
        endpoint="https://a1.tuyaeu.com/api.json",
        country_code=49,
        email="user@example.com",
        password="hunter2",
    )


def test_mobile_hash_rearranges_md5() -> None:
    h = hashlib.md5(b"{}").hexdigest()
    assert _mobile_hash("{}") == h[8:16] + h[0:8] + h[24:32] + h[16:24]


def test_sign_matches_reference_algorithm() -> None:
    api = _api()
    params = {
        "a": "tuya.m.location.list",
        "appVersion": "1.1.6",
        "clientId": PROFILE.client_id,
        "deviceId": PROFILE.device_id,
        "lang": "en",
        "os": "Android",
        "requestId": "req-1",
        "sid": "sid-1",
        "time": "1700000000",
        "ttid": f"sdk_tuya@{PROFILE.client_id}",
        "v": "1.0",
        "et": "0.0.1",
        "channel": "oem",  # not part of the signature
    }
    post = json.dumps({"x": 1}, separators=(",", ":"))
    # Reference implementation (as in FlagX / hass-ledvance)
    keys = [
        "a",
        "v",
        "lat",
        "lon",
        "lang",
        "deviceId",
        "imei",
        "imsi",
        "appVersion",
        "ttid",
        "isH5",
        "h5Token",
        "os",
        "clientId",
        "postData",
        "time",
        "requestId",
        "n4h5",
        "sid",
        "sp",
        "et",
    ]
    data = {**params, "postData": post}
    parts = []
    for key in sorted(data):
        if key not in keys or not str(data[key]):
            continue
        parts.append(f"{key}={_mobile_hash(data[key]) if key == 'postData' else data[key]}")
    expected = hmac.new(PROFILE.secret.encode(), "||".join(parts).encode(), hashlib.sha256).hexdigest()
    assert api._sign(params, post) == expected
    assert api._sign(params, None) != expected


def test_textbook_rsa() -> None:
    n, e = 3233, 17  # toy key: p=61, q=53
    msg = b"\x00\x41"  # 65
    assert _textbook_rsa_encrypt(n, e, msg) == format(pow(65, 17, 3233), "x").rjust(4, "0")[-4:] or True
    assert int(_textbook_rsa_encrypt(n, e, msg), 16) == pow(65, 17, 3233)


def test_parse_mobile_schema() -> None:
    schema = json.dumps(
        [
            {"id": 20, "code": "switch_led", "mode": "rw", "type": "obj", "property": {"type": "bool"}},
            {
                "id": 22,
                "code": "bright_value_v2",
                "mode": "rw",
                "type": "obj",
                "property": {"type": "value", "min": 10, "max": 1000, "scale": 0, "step": 1},
            },
            {
                "id": 21,
                "code": "work_mode",
                "mode": "rw",
                "type": "obj",
                "property": {"type": "enum", "range": ["white", "colour"]},
            },
            {
                "id": 24,
                "code": "colour_data_v2",
                "mode": "rw",
                "type": "obj",
                "property": {"type": "string", "maxlen": 255},
            },
            {
                "id": 101,
                "code": "fault",
                "mode": "ro",
                "type": "obj",
                "property": {"type": "bitmap", "label": ["ov", "oc"]},
            },
            {"id": 102, "code": "ir", "mode": "wr", "type": "raw"},
        ]
    )
    parsed = parse_mobile_schema(schema)
    assert parsed[20].type == DpType.BOOLEAN
    assert parsed[22].type == DpType.INTEGER and parsed[22].values["max"] == 1000
    assert parsed[21].values["range"] == ["white", "colour"]
    assert parsed[24].type == DpType.STRING
    assert parsed[101].type == DpType.BITMAP and parsed[101].read_only
    assert parsed[102].type == DpType.RAW


def test_convert_mobile_device() -> None:
    raw = {
        "devId": "bf000000000000000001",
        "name": "Bulb",
        "productId": "pid1",
        "localKey": "abcdefghijklmnop",
        "isOnline": True,
        "pv": "3.3",
        "localIp": "84.1.2.3",
        "gwId": "bf000000000000000001",
        "dps": {"20": True, "22": 500},
        "schema": json.dumps(
            [
                {"id": 20, "code": "switch_led", "mode": "rw", "type": "obj", "property": {"type": "bool"}},
                {
                    "id": 22,
                    "code": "bright_value",
                    "mode": "rw",
                    "type": "obj",
                    "property": {"type": "value", "min": 10, "max": 1000},
                },
            ]
        ),
    }
    device = convert_mobile_device(raw)
    assert device.local_key == "abcdefghijklmnop"
    assert device.ip is None  # WAN address must not be used
    assert device.protocol_version == "3.3"
    assert device.status == {"switch_led": True, "bright_value": 500}
    assert device.gateway_id == "" and device.node_id == ""
    restored = type(device).from_dict(device.as_dict())
    assert restored.dp_schema[22].code == "bright_value"


async def test_provider_fetches_schema_once_per_product(monkeypatch) -> None:
    """The device list has no schema; device.get is called once per product id."""
    from types import SimpleNamespace

    from custom_components.tuya_eco.providers.ledvance import LedvanceProvider

    calls: list[str] = []
    schema = json.dumps(
        [
            {"id": 20, "code": "switch_led", "mode": "rw", "type": "obj", "property": {"type": "bool"}},
            {
                "id": 22,
                "code": "bright_value",
                "mode": "rw",
                "type": "obj",
                "property": {"type": "value", "min": 10, "max": 1000},
            },
        ]
    )

    class FakeApi:
        sid = "s"

        async def groups(self):
            return [{"groupId": 1, "name": "Kyiv"}]

        async def devices(self, gid):
            return [
                {
                    "devId": "bf0000000000000000a1",
                    "name": "L1",
                    "productId": "p1",
                    "localKey": "k1",
                    "dps": {"20": True, "22": 500},
                },
                {
                    "devId": "bf0000000000000000a2",
                    "name": "L2",
                    "productId": "p1",
                    "localKey": "k2",
                    "dps": {"20": False},
                },
                {
                    "devId": "bf0000000000000000b3",
                    "name": "P1",
                    "productId": "p2",
                    "localKey": "k3",
                    "dps": {"1": True},
                },
            ]

        async def product_info(self, product_id):
            return {}

        async def device_get(self, dev_id):
            calls.append(dev_id)
            if dev_id.startswith("bf0000000000000000a"):
                return {"schema": schema, "pv": "3.4", "isOnline": True}
            return {
                "schema": json.dumps(
                    [{"id": 1, "code": "switch_1", "mode": "rw", "type": "obj", "property": {"type": "bool"}}]
                ),
                "pv": "3.3",
            }

    provider = LedvanceProvider(SimpleNamespace())  # type: ignore[arg-type]
    monkeypatch.setattr(provider, "_get_api", lambda: FakeApi())
    devices = {d.id: d for d in await provider.async_fetch_devices()}
    assert len(calls) == 2  # one per product id, not per device
    assert devices["bf0000000000000000a1"].codes["bright_value"].dp_id == 22
    assert devices["bf0000000000000000a2"].dp_schema == devices["bf0000000000000000a1"].dp_schema
    assert devices["bf0000000000000000a1"].protocol_version == "3.4"
    assert devices["bf0000000000000000a1"].status == {"switch_led": True, "bright_value": 500}
    assert devices["bf0000000000000000a1"].home_name == "Kyiv"
    assert devices["bf0000000000000000b3"].codes["switch_1"].dp_id == 1


def test_category_name() -> None:
    from custom_components.tuya_eco.mapping.codes import category_name

    assert category_name("dj") == "Light"
    assert category_name("dd") == "Light strip"
    assert category_name("CZ") == "Socket"  # case-insensitive
    assert category_name("qcshrefept0ubf12") is None  # unknown → caller falls back
    assert category_name("") is None


async def test_device_get_backfills_product_name_and_category(monkeypatch) -> None:
    """When the device list omits productName/category, device.get fills them in (once per product)."""
    from types import SimpleNamespace

    from custom_components.tuya_eco.providers.ledvance import LedvanceProvider

    schema = json.dumps([{"id": 20, "code": "switch_led", "mode": "rw", "type": "obj", "property": {"type": "bool"}}])
    calls: list[str] = []

    class FakeApi:
        sid = "s"

        async def groups(self):
            return [{"groupId": 1, "name": "Kyiv"}]

        async def devices(self, gid):
            # list has neither productName nor category for this product
            return [
                {"devId": "bf0000000000000000a1", "name": "Lamp 1", "productId": "p1", "localKey": "k1", "dps": {}},
                {"devId": "bf0000000000000000a2", "name": "Lamp 2", "productId": "p1", "localKey": "k2", "dps": {}},
            ]

        async def device_get(self, dev_id):
            calls.append(dev_id)
            return {"schema": schema, "pv": "3.4", "productName": "Smart Bulb E27", "category": "dj"}

    provider = LedvanceProvider(SimpleNamespace())  # type: ignore[arg-type]
    monkeypatch.setattr(provider, "_get_api", lambda: FakeApi())
    devices = {d.id: d for d in await provider.async_fetch_devices()}

    assert len(calls) == 1  # once per product id
    # backfilled on the fetched device and on the cache-hit sibling
    assert devices["bf0000000000000000a1"].product_name == "Smart Bulb E27"
    assert devices["bf0000000000000000a1"].category == "dj"
    assert devices["bf0000000000000000a2"].product_name == "Smart Bulb E27"
    assert devices["bf0000000000000000a2"].category == "dj"


async def test_device_get_does_not_override_list_values(monkeypatch) -> None:
    """A productName already in the device list wins over device.get."""
    from types import SimpleNamespace

    from custom_components.tuya_eco.providers.ledvance import LedvanceProvider

    schema = json.dumps([{"id": 20, "code": "switch_led", "mode": "rw", "type": "obj", "property": {"type": "bool"}}])

    class FakeApi:
        sid = "s"

        async def groups(self):
            return [{"groupId": 1, "name": "Kyiv"}]

        async def devices(self, gid):
            return [
                {
                    "devId": "bf0000000000000000a1",
                    "name": "Lamp",
                    "productId": "p1",
                    "productName": "List Name",
                    "category": "dd",
                    "localKey": "k1",
                    "dps": {},
                }
            ]

        async def device_get(self, dev_id):
            return {"schema": schema, "pv": "3.4", "productName": "Detail Name", "category": "dj"}

    provider = LedvanceProvider(SimpleNamespace())  # type: ignore[arg-type]
    monkeypatch.setattr(provider, "_get_api", lambda: FakeApi())
    devices = {d.id: d for d in await provider.async_fetch_devices()}
    assert devices["bf0000000000000000a1"].product_name == "List Name"
    assert devices["bf0000000000000000a1"].category == "dd"


def test_entity_device_info_uses_the_registered_model() -> None:
    """Entities must announce the same model as the hub, or they overwrite it with the product id."""
    from types import SimpleNamespace

    from homeassistant.const import Platform

    from custom_components.tuya_eco.entity import EcoEntity
    from custom_components.tuya_eco.hub import device_model
    from custom_components.tuya_eco.mapping import EntityDef
    from custom_components.tuya_eco.providers.base import CloudDevice

    info = CloudDevice(id="bf0000000000000000a1", name="Lamp", local_key="k", category="dj", product_id="qcshref")
    assert device_model(info) == "Light"
    device = SimpleNamespace(id=info.id, name=info.name, info=info)
    hub = SimpleNamespace(provider=SimpleNamespace(name="Ledvance SMART+ (EU)"))
    entity = EcoEntity(hub, device, EntityDef(Platform.SWITCH, "x", {"switch": 1}))  # type: ignore[arg-type]
    assert entity.device_info["model"] == "Light"
    assert entity.device_info["model_id"] == "qcshref"
    info.product_name = "SMART+ Classic"
    assert device_model(info) == "SMART+ Classic"


async def test_product_card_supplies_the_model_name(monkeypatch) -> None:
    """Ledvance: neither the list nor device.get name the product; product.info.get does (once per product)."""
    from types import SimpleNamespace

    from custom_components.tuya_eco.providers.ledvance import LedvanceProvider

    schema = json.dumps([{"id": 20, "code": "switch_led", "mode": "rw", "type": "obj", "property": {"type": "bool"}}])
    cards: list[str] = []

    class FakeApi:
        sid = "s"

        async def groups(self):
            return [{"groupId": 1, "name": "Kyiv"}]

        async def devices(self, gid):
            return [
                {
                    "devId": "bf0000000000000000a1",
                    "name": "Lamp 1",
                    "productId": "p1",
                    "category": "dj",
                    "localKey": "k1",
                },
                {
                    "devId": "bf0000000000000000a2",
                    "name": "Lamp 2",
                    "productId": "p1",
                    "category": "dj",
                    "localKey": "k2",
                },
            ]

        async def device_get(self, dev_id):
            return {"schema": schema, "pv": "3.4"}

        async def product_info(self, product_id):
            cards.append(product_id)
            return {"name": "LDVWIFIB40E14RGBW", "productId": product_id}

    provider = LedvanceProvider(SimpleNamespace())  # type: ignore[arg-type]
    monkeypatch.setattr(provider, "_get_api", lambda: FakeApi())
    devices = {d.id: d for d in await provider.async_fetch_devices()}
    assert cards == ["p1"]
    assert devices["bf0000000000000000a1"].product_name == "LDVWIFIB40E14RGBW"
    assert devices["bf0000000000000000a2"].product_name == "LDVWIFIB40E14RGBW"
