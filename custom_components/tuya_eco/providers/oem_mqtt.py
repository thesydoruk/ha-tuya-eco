"""Push and command channel of the Tuya mobile-app MQTT broker (OEM apps).

The apps built on the Tuya SDK keep an MQTT session with ``mobileMqttsUrl`` (e.g.
``m1.tuyaeu.com:8883``, TLS). Verified live with a Ledvance SMART+ account:

* credentials: username = session id (``sid``), password = ``md5(ecode)[8:24]``;
* devices report on ``smart/mb/in/{devId}`` and take commands on ``smart/mb/out/{devId}``
  (one exact topic per device, wildcards are refused);
* frames are encrypted with the device's ``localKey``:

  - ``2.2``: ``"2.2" | crc32(frame[7:]) | seq | origin | AES-ECB(json)``  (big-endian ints)
  - ``2.3``: ``"2.3" | seq | origin | 0x00`` (12-byte header = GCM AAD) ``| nonce(12) | ciphertext | tag(16)``

* decrypted messages: ``protocol 4`` = DP report (``data.dps``), ``protocol 1`` with
  ``data.online`` = online/offline event; commands are ``{"data":{"dps":…},"protocol":5,"t":<s>}``.

The channel is a *backup*: the hub uses it only while a device is unreachable on the LAN.
paho-mqtt runs its network loop in its own thread; every callback is handed to the event loop.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable, Iterable
import hashlib
import json
import logging
import os
import struct
import threading
import time
from typing import TYPE_CHECKING, Any
import zlib

from ..local.wire import WireError, aes_ecb_decrypt, aes_ecb_encrypt, aes_gcm_decrypt, aes_gcm_encrypt

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

TOPIC_IN = "smart/mb/in/"
TOPIC_OUT = "smart/mb/out/"
WILL_TOPIC = "tuya/smart/will"
KEEPALIVE = 60
PUBACK_TIMEOUT = 5.0
_CLIENT_ID_SALT = "sdkfasodifca"

# CONNACK return codes that mean "these credentials are not accepted".
AUTH_REFUSED = frozenset({4, 5})

MessageCallback = Callable[[str, bytes], None]
ConnectionCallback = Callable[[bool, int], None]


# ---------------------------------------------------------------------- credentials


def _md5hex(data: str) -> str:
    return hashlib.md5(data.encode()).hexdigest()  # noqa: S324 - protocol requirement


def mqtt_password(ecode: str) -> str:
    """Password of the mobile MQTT session."""
    return _md5hex(ecode)[8:24]


def mqtt_client_id(package: str, phone_id: str, uid: str) -> str:
    """Client id in the SDK's format; `phone_id` must be unique per installation."""
    return f"{package}_mb_{phone_id}_{_md5hex(uid + _CLIENT_ID_SALT)}_DEFAULT"


def mqtt_host_for(api_endpoint: str) -> str:
    """Broker host when the login result did not name one: ``a1.tuyaeu.com`` → ``m1.tuyaeu.com``."""
    host = api_endpoint.split("://", 1)[-1].split("/", 1)[0]
    return "m1." + host.split(".", 1)[1] if "." in host else host


# ---------------------------------------------------------------------- frames


def decode_frame(payload: bytes, local_key: str) -> tuple[str, dict[str, Any]] | None:
    """Decrypt a frame from ``smart/mb/in/…``: (version, message), or ``None`` if it is not readable."""
    key = local_key.encode()
    head = payload[:3]
    try:
        if head == b"2.2":
            if len(payload) < 31:
                return None
            (crc,) = struct.unpack(">I", payload[3:7])
            if crc != zlib.crc32(payload[7:]) & 0xFFFFFFFF:
                return None
            plain = aes_ecb_decrypt(key, payload[15:])
        elif head == b"2.3":
            if len(payload) < 12 + 12 + 16:
                return None
            header, nonce = payload[:12], payload[12:24]
            plain = aes_gcm_decrypt(key, nonce, payload[24:-16], payload[-16:], header)
        elif head == b"2.1":
            plain = aes_ecb_decrypt(key, base64.b64decode(payload[19:]))
        elif payload[:1] == b"{":
            message = json.loads(payload)
            data = message.get("data") if isinstance(message, dict) else None
            if isinstance(data, str):  # pv 2.0: AES-ECB of the data, hex encoded
                message["data"] = json.loads(aes_ecb_decrypt(key, bytes.fromhex(data)))
            return ("json", message) if isinstance(message, dict) else None
        else:
            return None
        message = json.loads(plain)
    except (WireError, ValueError, TypeError):
        return None
    if not isinstance(message, dict):
        return None
    return head.decode(), message


def command_payload(dps: dict[str, Any], now: float | None = None) -> bytes:
    """Plaintext of a DP command."""
    body = {"data": {"dps": dps}, "protocol": 5, "t": int(time.time() if now is None else now)}
    return json.dumps(body, separators=(",", ":")).encode()


def encode_command(
    version: str,
    local_key: str,
    dps: dict[str, Any],
    seq: int,
    origin: int,
    *,
    now: float | None = None,
    nonce: bytes | None = None,
) -> bytes:
    """Frame for ``smart/mb/out/{devId}`` in the version the device itself uses."""
    key = local_key.encode()
    plain = command_payload(dps, now)
    so = struct.pack(">II", seq & 0xFFFFFFFF, origin & 0xFFFFFFFF)
    if version == "2.3":
        header = b"2.3" + so + b"\x00"
        iv = nonce if nonce is not None else os.urandom(12)
        return header + iv + aes_gcm_encrypt(key, iv, plain, header)
    if version == "2.2":
        body = so + aes_ecb_encrypt(key, plain)
        return b"2.2" + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF) + body
    raise ValueError(f"unsupported frame version {version!r}")


# ---------------------------------------------------------------------- channel


class OemMqttChannel:
    """One MQTT session: subscriptions, inbound messages and PUBACK-confirmed publishes.

    All public methods and both callbacks run in the event loop.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        client_id: str,
        on_message: MessageCallback,
        on_connection: ConnectionCallback,
    ) -> None:
        """Initialise (no I/O)."""
        self.hass = hass
        self.client_id = client_id
        self._on_message = on_message
        self._on_connection = on_connection
        self._client: Any = None
        self._username = ""
        self._password = ""
        self._topics: set[str] = set()
        self._topics_lock = threading.Lock()
        self._connected = False
        self._pending: dict[int, asyncio.Future[None]] = {}

    @property
    def connected(self) -> bool:
        """True while the broker session is up."""
        return self._connected

    def set_credentials(self, username: str, password: str) -> None:
        """New session id: used from the next (re)connect on."""
        self._username, self._password = username, password
        if self._client is not None:
            self._client.username_pw_set(username, password)

    def set_devices(self, device_ids: Iterable[str]) -> None:
        """Follow these devices: subscribe the new ones, drop the others."""
        wanted = {TOPIC_IN + dev_id for dev_id in device_ids if dev_id}
        with self._topics_lock:
            added, removed = wanted - self._topics, self._topics - wanted
            self._topics = wanted
        if self._client is None or not self._connected:
            return  # (re)connect subscribes the whole set
        for topic in sorted(added):
            self._client.subscribe(topic, qos=1)
        for topic in sorted(removed):
            self._client.unsubscribe(topic)

    async def async_start(self, host: str, port: int) -> None:
        """Connect in the background; paho keeps reconnecting with backoff."""
        if self._client is not None:
            return
        self._client = await self.hass.async_add_executor_job(self._build_client)
        self._client.connect_async(host, port, keepalive=KEEPALIVE)
        self._client.loop_start()

    async def async_stop(self) -> None:
        """Disconnect and stop the network thread."""
        client, self._client = self._client, None
        self._connected = False
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("channel stopped"))
        self._pending.clear()
        if client is not None:
            await self.hass.async_add_executor_job(self._shutdown, client)

    async def async_publish(self, device_id: str, frame: bytes, timeout: float = PUBACK_TIMEOUT) -> bool:
        """Publish a command; True once the broker acknowledged it (PUBACK)."""
        if self._client is None or not self._connected:
            return False
        info = self._client.publish(TOPIC_OUT + device_id, frame, qos=1, retain=False)
        if info.rc != 0:
            return False
        # PUBACK reaches the loop through call_soon_threadsafe, i.e. after this registration.
        future: asyncio.Future[None] = self.hass.loop.create_future()
        self._pending[info.mid] = future
        try:
            async with asyncio.timeout(timeout):
                await future
        except (TimeoutError, ConnectionError):
            return False
        finally:
            self._pending.pop(info.mid, None)
        return True

    # ------------------------------------------------------------------ paho (worker threads)

    def _build_client(self) -> Any:
        from homeassistant.util.ssl import client_context  # noqa: PLC0415
        import paho.mqtt.client as mqtt  # noqa: PLC0415

        kwargs: dict[str, Any] = {}
        if hasattr(mqtt, "CallbackAPIVersion"):  # paho ≥ 2
            kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION1
        client = mqtt.Client(client_id=self.client_id, clean_session=True, protocol=mqtt.MQTTv311, **kwargs)
        client.username_pw_set(self._username, self._password)
        client.tls_set_context(client_context())
        client.reconnect_delay_set(min_delay=5, max_delay=300)
        # The official app sets this will; without it the broker reportedly does not route DP reports.
        will = {"clientId": self.client_id, "deviceType": "ANDROID", "message": "", "userName": self._username}
        client.will_set(WILL_TOPIC, json.dumps(will, separators=(",", ":")), qos=1, retain=False)
        client.on_connect = self._paho_connect
        client.on_disconnect = self._paho_disconnect
        client.on_message = self._paho_message
        client.on_publish = self._paho_publish
        return client

    @staticmethod
    def _shutdown(client: Any) -> None:
        client.disconnect()
        client.loop_stop()

    def _paho_connect(self, client: Any, _userdata: Any, _flags: Any, rc: int) -> None:
        if rc == 0:
            with self._topics_lock:
                topics = sorted(self._topics)
            for topic in topics:
                client.subscribe(topic, qos=1)
        self.hass.loop.call_soon_threadsafe(self._connection_changed, rc == 0, rc)

    def _paho_disconnect(self, _client: Any, _userdata: Any, rc: int) -> None:
        self.hass.loop.call_soon_threadsafe(self._connection_changed, False, rc)

    def _paho_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        self.hass.loop.call_soon_threadsafe(self._message, str(message.topic), bytes(message.payload))

    def _paho_publish(self, _client: Any, _userdata: Any, mid: int) -> None:
        self.hass.loop.call_soon_threadsafe(self._puback, mid)

    # ------------------------------------------------------------------ event loop side

    def _connection_changed(self, connected: bool, rc: int) -> None:
        if self._client is None:
            return  # stopped meanwhile
        was = self._connected
        self._connected = connected
        if not connected:
            for future in self._pending.values():  # QoS 1 is not resent across a clean session
                if not future.done():
                    future.set_exception(ConnectionError("disconnected"))
        if connected != was or rc:
            self._on_connection(connected, rc)

    def _message(self, topic: str, payload: bytes) -> None:
        if self._client is None or not topic.startswith(TOPIC_IN):
            return
        self._on_message(topic[len(TOPIC_IN) :], payload)

    def _puback(self, mid: int) -> None:
        future = self._pending.get(mid)
        if future is not None and not future.done():
            future.set_result(None)
