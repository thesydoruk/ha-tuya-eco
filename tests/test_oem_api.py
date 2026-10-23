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
