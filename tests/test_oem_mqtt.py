"""OEM-app MQTT channel: credentials, frame codec, channel bookkeeping and provider wiring."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import zlib

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import pytest

from custom_components.tuya_eco.providers.base import CloudDevice, DpSpec, DpType, PushListener
from custom_components.tuya_eco.providers.oem_mqtt import (
    OemMqttChannel,
    command_payload,
    decode_frame,
    encode_command,
    mqtt_client_id,
    mqtt_host_for,
    mqtt_password,
)
from custom_components.tuya_eco.providers.oem_profiles import LEDVANCE
from custom_components.tuya_eco.providers.tuya_oem import TuyaMobileApi, TuyaOemProvider

KEY = "0123456789abcdef"  # fake local key
BULB = "bf00000000000000bulb"
GATEWAY = "bf0000000000000000gw"
CHILD = "a4c138000000child"


def _ecb(data: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    encryptor = Cipher(algorithms.AES(KEY.encode()), modes.ECB()).encryptor()
    return encryptor.update(padder.update(data) + padder.finalize()) + encryptor.finalize()


def _frame_22(message: dict[str, Any], seq: int = 7, origin: int = 1) -> bytes:
    """Independent 2.2 encoder: ``"2.2" | crc32(rest) | seq | origin | AES-ECB``."""
    body = struct.pack(">II", seq, origin) + _ecb(json.dumps(message).encode())
    return b"2.2" + struct.pack(">I", zlib.crc32(body)) + body


def _frame_23(message: dict[str, Any], seq: int = 7, origin: int = 1, nonce: bytes = b"\x05" * 12) -> bytes:
    header = b"2.3" + struct.pack(">II", seq, origin) + b"\x00"
    return header + nonce + AESGCM(KEY.encode()).encrypt(nonce, json.dumps(message).encode(), header)


REPORT = {"protocol": 4, "t": 1794437788, "data": {"dps": {"20": False}}}


@pytest.fixture(autouse=True)
def _no_http_session(monkeypatch) -> None:
    """The provider's API client gets a dummy session; nothing here talks to the network."""
    monkeypatch.setattr(
        "custom_components.tuya_eco.providers.tuya_oem.async_get_clientsession", lambda _hass: MagicMock()
    )


# ---------------------------------------------------------------------- credentials


def test_credentials() -> None:
    assert mqtt_password("ecode-fake-value") == hashlib.md5(b"ecode-fake-value").hexdigest()[8:24]
    salt = hashlib.md5(b"uid-fake" + b"sdkfasodifca").hexdigest()
    assert mqtt_client_id("com.example.app", "phone1", "uid-fake") == f"com.example.app_mb_phone1_{salt}_DEFAULT"
    assert mqtt_host_for("https://a1.tuyaeu.com/api.json") == "m1.tuyaeu.com"
    assert mqtt_host_for("https://a1.tuyaus.com/api.json") == "m1.tuyaus.com"


# ---------------------------------------------------------------------- frames


def test_decode_22_23_and_json() -> None:
    assert decode_frame(_frame_22(REPORT), KEY) == ("2.2", REPORT)
    assert decode_frame(_frame_23(REPORT), KEY) == ("2.3", REPORT)
    pv20 = {"pv": "2.0", "protocol": 4, "data": _ecb(json.dumps({"dps": {"1": True}}).encode()).hex()}
    version, message = decode_frame(json.dumps(pv20).encode(), KEY)  # type: ignore[misc]
    assert version == "json"
    assert message["data"] == {"dps": {"1": True}}


def test_decode_2_1() -> None:
    frame = b"2.1" + b"0" * 16 + base64.b64encode(_ecb(json.dumps(REPORT).encode()))
    assert decode_frame(frame, KEY) == ("2.1", REPORT)


def test_decode_rejects_bad_crc_wrong_key_and_garbage() -> None:
    tampered = bytearray(_frame_22(REPORT))
    tampered[-1] ^= 0x01
    assert decode_frame(bytes(tampered), KEY) is None
    assert decode_frame(_frame_22(REPORT), "fedcba9876543210") is None
    assert decode_frame(_frame_23(REPORT), "fedcba9876543210") is None
    assert decode_frame(b"3.3garbage", KEY) is None
    assert decode_frame(b"2.2", KEY) is None


def test_encode_22_matches_independent_encoder() -> None:
    plain = command_payload({"20": True}, now=1794437788)
    assert plain == b'{"data":{"dps":{"20":true}},"protocol":5,"t":1794437788}'
    frame = encode_command("2.2", KEY, {"20": True}, 2, 123456, now=1794437788)
    body = struct.pack(">II", 2, 123456) + _ecb(plain)
    assert frame == b"2.2" + struct.pack(">I", zlib.crc32(body)) + body
    assert decode_frame(frame, KEY) == ("2.2", json.loads(plain))


def test_encode_23_header_is_the_aad() -> None:
    nonce = bytes(range(12))
    frame = encode_command("2.3", KEY, {"20": False}, 3, 99, now=1794437788, nonce=nonce)
    header = b"2.3" + struct.pack(">II", 3, 99) + b"\x00"
    assert frame[:12] == header
    assert frame[12:24] == nonce
    assert AESGCM(KEY.encode()).decrypt(nonce, frame[24:], header) == command_payload({"20": False}, 1794437788)


def test_encode_rejects_unknown_version() -> None:
    with pytest.raises(ValueError):
        encode_command("2.1", KEY, {"20": True}, 2, 1)


# ---------------------------------------------------------------------- channel


class FakeClient:
    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.published: list[tuple[str, bytes]] = []
        self.credentials: tuple[str, str] | None = None

    def subscribe(self, topic: str, qos: int) -> None:
        self.subscribed.append(topic)

    def unsubscribe(self, topic: str) -> None:
        self.unsubscribed.append(topic)

    def username_pw_set(self, username: str, password: str) -> None:
        self.credentials = (username, password)

    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> Any:
        self.published.append((topic, payload))
        return SimpleNamespace(rc=0, mid=len(self.published))


def _channel(**kwargs: Any) -> tuple[OemMqttChannel, FakeClient]:
    hass = SimpleNamespace(loop=asyncio.get_running_loop())
    channel = OemMqttChannel(
        hass,  # type: ignore[arg-type]
        client_id="cid",
        on_message=kwargs.get("on_message", lambda *_: None),
        on_connection=kwargs.get("on_connection", lambda *_: None),
    )
    client = FakeClient()
    channel._client = client
    return channel, client


async def test_channel_subscribes_on_connect_and_diffs_later() -> None:
    events: list[tuple[bool, int]] = []
    channel, client = _channel(on_connection=lambda c, rc: events.append((c, rc)))
    channel.set_devices({"a", "b"})
    assert client.subscribed == []  # not connected yet
    channel._paho_connect(client, None, None, 0)  # called from paho's thread in real life
    await asyncio.sleep(0)
    assert sorted(client.subscribed) == ["smart/mb/in/a", "smart/mb/in/b"]
    assert channel.connected
    assert events == [(True, 0)]
    channel.set_devices({"b", "c"})
    assert client.subscribed[-1] == "smart/mb/in/c"
    assert client.unsubscribed == ["smart/mb/in/a"]


async def test_channel_routes_messages_and_credentials() -> None:
    received: list[tuple[str, bytes]] = []
    channel, client = _channel(on_message=lambda dev, payload: received.append((dev, payload)))
    channel._paho_message(None, None, SimpleNamespace(topic="smart/mb/in/dev1", payload=b"2.2xyz"))
    channel._paho_message(None, None, SimpleNamespace(topic="other/topic", payload=b"x"))
    await asyncio.sleep(0)
    assert received == [("dev1", b"2.2xyz")]
    channel.set_credentials("sid-2", "pw-2")
    assert client.credentials == ("sid-2", "pw-2")


async def test_publish_waits_for_puback() -> None:
    channel, client = _channel()
    channel._connected = True
    task = asyncio.create_task(channel.async_publish("dev1", b"frame"))
    await asyncio.sleep(0)
    assert client.published == [("smart/mb/out/dev1", b"frame")]
    channel._paho_publish(None, None, 1)
    assert await task is True


async def test_publish_times_out_or_fails_on_disconnect() -> None:
    channel, _client = _channel()
    assert await channel.async_publish("dev1", b"frame") is False  # not connected
    channel._connected = True
    assert await channel.async_publish("dev1", b"frame", timeout=0.05) is False
    task = asyncio.create_task(channel.async_publish("dev1", b"frame"))
    await asyncio.sleep(0)
    channel._connection_changed(False, 1)
    assert await task is False


# ---------------------------------------------------------------------- provider


class Recorder(PushListener):
    def __init__(self) -> None:
        self.reports: list[tuple[str, dict[str, Any]]] = []
        self.online: list[tuple[str, bool]] = []

    def push_device_report(self, device_id: str, values: dict[str, Any]) -> None:
        self.reports.append((device_id, values))

    def push_device_online(self, device_id: str, online: bool) -> None:
        self.online.append((device_id, online))


SCHEMA = {
    20: DpSpec(20, "switch_led", DpType.BOOLEAN),
    22: DpSpec(22, "bright_value_v2", DpType.INTEGER, {"min": 10, "max": 1000}),
}


def _provider() -> tuple[TuyaOemProvider, Recorder]:
    hass = MagicMock()
    hass.loop = asyncio.get_running_loop()
    provider = TuyaOemProvider(hass)
    provider.profile = LEDVANCE
    provider.update_known_devices(
        [
            CloudDevice(id=BULB, name="Bulb", local_key=KEY, dp_schema=dict(SCHEMA)),
            CloudDevice(id=GATEWAY, name="Gateway", local_key=KEY),
            CloudDevice(
                id=CHILD,
                name="Child",
                local_key="",
                gateway_id=GATEWAY,
                node_id="0x1234",
                dp_schema={1: DpSpec(1, "switch_1", DpType.BOOLEAN)},
            ),
        ]
    )
    recorder = Recorder()
    provider._push_listener = recorder
    return provider, recorder


async def test_provider_maps_reports_and_online_events() -> None:
    provider, rec = _provider()
    provider._on_mqtt_message(BULB, _frame_23({"protocol": 4, "data": {"dps": {"20": True, "22": 500, "99": 1}}}))
    provider._on_mqtt_message(BULB, _frame_23({"protocol": 4, "data": {"dps": {"20": False}}}))
    offline = {"protocol": 1, "type": "update", "data": {"devId": BULB, "online": False, "gwId": BULB}}
    provider._on_mqtt_message(BULB, _frame_23(offline))
    assert rec.reports == [(BULB, {"switch_led": True, "bright_value_v2": 500}), (BULB, {"switch_led": False})]
    assert rec.online == [(BULB, True), (BULB, False)]  # a report means online; repeats are not re-sent
    assert provider._frame_version[BULB] == "2.3"


async def test_provider_routes_sub_device_reports_by_cid() -> None:
    provider, rec = _provider()
    provider._on_mqtt_message(GATEWAY, _frame_22({"protocol": 4, "data": {"cid": "0x1234", "dps": {"1": True}}}))
    provider._on_mqtt_message(GATEWAY, _frame_22({"protocol": 4, "data": {"cid": "0xffff", "dps": {"1": True}}}))
    assert rec.reports == [(CHILD, {"switch_1": True})]


async def test_provider_ignores_unknown_topics_and_unreadable_frames() -> None:
    provider, rec = _provider()
    provider._on_mqtt_message("unknown-device", _frame_22(REPORT))
    provider._on_mqtt_message(BULB, b"2.2" + b"\x00" * 40)
    assert rec.reports == [] and rec.online == []


async def test_channel_loss_marks_devices_cloud_offline() -> None:
    provider, rec = _provider()
    provider._on_mqtt_message(BULB, _frame_22(REPORT))
    provider._on_mqtt_connection(False, 1)
    assert rec.online == [(BULB, True), (BULB, False)]


async def test_silent_devices_are_checked_over_http_after_connect(monkeypatch) -> None:
    """A device unreachable on the LAN does not report on MQTT; its online state comes from device.get."""
    real_sleep = asyncio.sleep  # the module-level patch below replaces asyncio.sleep everywhere
    monkeypatch.setattr("custom_components.tuya_eco.providers.tuya_oem._SILENT_CHECK_DELAY", 0.01)
    monkeypatch.setattr("custom_components.tuya_eco.providers.tuya_oem.asyncio.sleep", AsyncMock())
    provider, rec = _provider()
    provider.hass.async_create_task = lambda coro: asyncio.get_running_loop().create_task(coro)
    provider._channel = MagicMock(connected=True)
    api = provider._get_api()
    online = {GATEWAY: False, CHILD: True}
    api.device_get = AsyncMock(side_effect=lambda dev_id: {"isOnline": online[dev_id]})  # type: ignore[method-assign]

    provider._on_mqtt_message(BULB, _frame_22(REPORT))  # the bulb reports: not asked about
    provider._on_mqtt_connection(True, 0)
    await real_sleep(0.05)
    await provider._silent_task  # type: ignore[misc]

    assert sorted(call.args[0] for call in api.device_get.await_args_list) == sorted([GATEWAY, CHILD])
    assert rec.online == [(BULB, True), (GATEWAY, False), (CHILD, True)]


async def test_silent_check_stops_on_api_errors_and_when_disconnected(monkeypatch) -> None:
    from custom_components.tuya_eco.providers.base import ProviderError

    monkeypatch.setattr("custom_components.tuya_eco.providers.tuya_oem.asyncio.sleep", AsyncMock())
    provider, rec = _provider()
    api = provider._get_api()
    api.device_get = AsyncMock(side_effect=ProviderError("rate limited"))  # type: ignore[method-assign]
    provider._channel = MagicMock(connected=True)
    await provider._async_check_silent_devices()
    assert api.device_get.await_count == 1 and rec.online == []

    provider._channel = MagicMock(connected=False)
    await provider._async_check_silent_devices()
    assert api.device_get.await_count == 1


async def test_stop_cancels_a_pending_silent_check() -> None:
    provider, _rec = _provider()
    provider._on_mqtt_connection(True, 0)
    assert provider._silent_check is not None
    await provider.async_stop_push()
    assert provider._silent_check is None


async def test_refused_login_renews_the_session_once(monkeypatch) -> None:
    # A host that booted 30 s ago: time.monotonic() is still below the rate-limit interval.
    import time as real_time  # noqa: PLC0415

    monkeypatch.setattr(
        "custom_components.tuya_eco.providers.tuya_oem.time",
        SimpleNamespace(monotonic=lambda: 30.0, time=real_time.time),
    )
    provider, _rec = _provider()
    api = provider._get_api()
    api.login = AsyncMock()  # type: ignore[method-assign]
    provider.hass.async_create_task = lambda coro: asyncio.get_running_loop().create_task(coro)
    provider._on_mqtt_connection(False, 5)
    await asyncio.sleep(0)
    provider._on_mqtt_connection(False, 5)  # rate limited
    await asyncio.sleep(0)
    assert api.login.await_count == 1


async def test_commands_prefer_mqtt_and_fall_back_to_http() -> None:
    provider, _rec = _provider()
    api = provider._get_api()
    api.publish_dps = AsyncMock()  # type: ignore[method-assign]
    channel = MagicMock(connected=True)
    channel.async_publish = AsyncMock(return_value=True)
    provider._channel = channel

    # Frame version not learned yet → HTTP.
    assert await provider.async_send_cloud_command(BULB, [{"code": "switch_led", "value": True}])
    assert api.publish_dps.await_count == 1
    channel.async_publish.assert_not_awaited()

    provider._on_mqtt_message(BULB, _frame_22(REPORT))  # learns "2.2"
    assert await provider.async_send_cloud_command(BULB, [{"code": "switch_led", "value": True}])
    assert api.publish_dps.await_count == 1
    dev_id, frame = channel.async_publish.await_args.args
    assert dev_id == BULB
    version, message = decode_frame(frame, KEY)  # type: ignore[misc]
    assert version == "2.2"
    assert message["protocol"] == 5 and message["data"] == {"dps": {"20": True}}

    channel.async_publish.return_value = False  # no PUBACK → HTTP
    assert await provider.async_send_cloud_command(BULB, [{"code": "switch_led", "value": False}])
    assert api.publish_dps.await_count == 2


async def test_sequence_numbers_increase() -> None:
    provider, _rec = _provider()
    provider._channel = MagicMock(connected=True, async_publish=AsyncMock(return_value=True))
    provider._on_mqtt_message(BULB, _frame_22(REPORT))
    for value in (True, False):
        await provider.async_send_cloud_command(BULB, [{"code": "switch_led", "value": value}])
    frames = [call.args[1] for call in provider._channel.async_publish.await_args_list]
    seqs = [struct.unpack(">I", f[7:11])[0] for f in frames]
    assert seqs[1] == seqs[0] + 1


# ---------------------------------------------------------------------- login / start


def _login_result(**extra: Any) -> dict[str, Any]:
    return {
        "sid": "sid-fake",
        "refreshToken": "rt-fake",
        "ecode": "ecode-fake",
        "uid": "uid-fake",
        "domain": {"mobileMqttsUrl": "m1.example.com", "mqttsPort": 8883},
        **extra,
    }


async def test_login_keeps_push_credentials_and_reports_the_session() -> None:
    sessions: list[str | None] = []
    api = TuyaMobileApi(
        session=MagicMock(),
        profile=LEDVANCE,
        endpoint="https://a1.tuyaeu.com/api.json",
        country_code=49,
        email="user@example.com",
        password="fake",
    )
    api.on_session = lambda: sessions.append(api.sid)
    responses = [{"publicKey": "65537", "exponent": "3", "token": "t"}, _login_result()]
    api._request = AsyncMock(side_effect=responses)  # type: ignore[method-assign]
    await api.login()
    assert (api.ecode, api.uid, api.mqtt_host, api.mqtt_port) == ("ecode-fake", "uid-fake", "m1.example.com", 8883)
    assert sessions == ["sid-fake"]

    # The silent refresh may come back without the ecode: keep the one we have.
    api._request = AsyncMock(return_value={"sid": "sid-2", "refreshToken": "rt-2"})  # type: ignore[method-assign]
    await api.login()
    assert (api.sid, api.ecode) == ("sid-2", "ecode-fake")
    assert sessions == ["sid-fake", "sid-2"]


async def test_start_push_logs_in_fully_when_the_ecode_is_missing(monkeypatch) -> None:
    provider, rec = _provider()
    persisted: list[dict[str, Any]] = []
    provider.on_token_update = persisted.append
    api = provider._get_api()
    api.sid = "old-sid"

    async def fake_login(*, full: bool = False) -> None:
        assert full
        api.sid, api.ecode, api.uid, api.mqtt_host = "sid-fake", "ecode-fake", "uid-fake", "m1.example.com"
        api._take_session_info({})

    api.login = fake_login  # type: ignore[method-assign]
    started: list[tuple[str, int]] = []

    async def fake_start(self: OemMqttChannel, host: str, port: int) -> None:
        started.append((host, port))

    monkeypatch.setattr(OemMqttChannel, "async_start", fake_start)
    assert await provider.async_start_push(rec)
    assert started == [("m1.example.com", 8883)]
    # Cached "online" flags are not trusted: only the channel's reports bring devices back.
    assert sorted(rec.online) == sorted([(BULB, False), (GATEWAY, False), (CHILD, False)])
    channel = provider._channel
    assert channel is not None
    assert channel._username == "sid-fake" and channel._password == mqtt_password("ecode-fake")
    assert channel.client_id.startswith("com.ledvance.smartplus_mb_")
    assert {t.rsplit("/", 1)[-1] for t in channel._topics} == {BULB, GATEWAY, CHILD}
    data = provider.to_entry_data()
    assert data["ecode"] == "ecode-fake" and data["mqtt_host"] == "m1.example.com" and data["mqtt_id"]
    assert persisted  # mqtt id + new session were handed to the hub for saving

    # A second call only re-scopes the subscriptions.
    assert await provider.async_start_push(rec)
    assert len(started) == 1
    await provider.async_stop_push()
    assert provider._channel is None


async def test_restored_entry_needs_no_new_login(monkeypatch) -> None:
    hass = MagicMock()
    hass.loop = asyncio.get_running_loop()
    data = {
        "email": "user@example.com",
        "password": "fake",
        "region": "EU",
        "sid": "sid-fake",
        "refresh_token": "rt",
        "ecode": "ecode-fake",
        "uid": "uid-fake",
        "mqtt_host": "",
        "mqtt_port": 8883,
        "mqtt_id": "abc",
    }
    provider = type("P", (TuyaOemProvider,), {"profile": LEDVANCE}).from_entry_data(hass, data)
    api = provider._get_api()
    api.login = AsyncMock()  # type: ignore[method-assign]
    started: list[tuple[str, int]] = []

    async def fake_start(self: OemMqttChannel, host: str, port: int) -> None:
        started.append((host, port))

    monkeypatch.setattr(OemMqttChannel, "async_start", fake_start)
    assert await provider.async_start_push(Recorder())
    api.login.assert_not_awaited()
    assert started == [("m1.tuyaeu.com", 8883)]  # derived from the API endpoint
    assert provider._channel is not None and provider._channel.client_id.startswith("com.ledvance.smartplus_mb_abc_")
