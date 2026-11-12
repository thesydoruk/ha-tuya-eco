"""Probe the Tuya mobile-app MQTT push channel for an OEM account (Ledvance SMART+, Sylvania).

Research tool, not part of the integration. It answers two open questions before the
push channel is built into ``TuyaOemProvider``:

1. Which MQTT credential scheme the broker accepts for this app identity.
2. What the device messages look like, the online/offline event in particular.

What it does:

* logs in through the mobile API (the same code path as the integration);
* lists the devices with their local keys;
* tries the known credential schemes against ``mobileMqttsUrl`` until one is accepted;
* subscribes to ``smart/mb/in/{devId}`` for every device and gateway, plus the user topics;
* decrypts what arrives (pv 2.1 / 2.2 / 2.3 / JSON) with the device's local key and logs it.

While it listens, switch a bulb in the SMART+ app, change its brightness, then cut its
power for a minute and restore it. That shows the DP reports and the online/offline events.

Secrets (password, sid, ecode, local keys, MQTT username/password) are never printed or
logged. The log goes to ``.scratch/oem_push_probe.log`` (git-ignored).

Run from the repository root::

    .venv/Scripts/python scripts/probe_oem_push.py --region EU --minutes 5

Or inside the Home Assistant container, with the account of the installed integration::

    python3 /tmp/probe_oem_push.py --ha-config /config --log /tmp/oem_push_probe.log

The e-mail is asked for interactively and the password through ``getpass`` (or set
``TUYA_EMAIL`` / ``TUYA_PASSWORD``). Logging in here may show "signed in on another
device" in the SMART+ app, just like adding the integration does; the integration
re-logs in by itself if its session is replaced.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter
from dataclasses import dataclass, field
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import ssl
import struct
import sys
import threading
import time
from typing import Any
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOG_PATH = ROOT / ".scratch" / "oem_push_probe.log"
CLIENT_ID_SALT = "sdkfasodifca"
DEFAULT_PACKAGES = ("com.ledvance.smartplus",)

# ---------------------------------------------------------------------------- logging


class Log:
    """Print to stdout and append to the log file."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self("=" * 72)
        self(f"probe started {time.strftime('%Y-%m-%d %H:%M:%S')}")

    def __call__(self, msg: str = "") -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        with self._lock:
            print(line, flush=True)
            self._fh.write(line + "\n")
            self._fh.flush()


def shape(value: Any) -> str:
    """Describe a secret without revealing it."""
    if value is None:
        return "None"
    if isinstance(value, str):
        return f"str[{len(value)}]"
    return type(value).__name__


def short_id(value: str) -> str:
    """Identifiers are not secrets, but keep the log tidy and less personal."""
    return value if len(value) <= 8 else f"{value[:4]}…{value[-4:]}"


# ---------------------------------------------------------------------------- credentials


def md5hex(data: str) -> str:
    return hashlib.md5(data.encode()).hexdigest()  # noqa: S324 - protocol requirement


@dataclass
class Session:
    """What the MQTT credentials are derived from."""

    sid: str
    ecode: str
    uid: str
    partner: str
    app_key: str
    signing_key: str
    host: str
    port: int


@dataclass
class Variant:
    """One credential scheme to try."""

    name: str
    client_id: str
    username: str
    password: str
    will: bool = True


def client_id_for(package: str, phone_id: str, uid: str) -> str:
    """``{package}_mb_{phoneId}_{md5(uid + salt)}_DEFAULT`` (OEM SDK)."""
    return f"{package}_mb_{phone_id}_{md5hex(uid + CLIENT_ID_SALT)}_DEFAULT"


def stock_password(ecode: str) -> str:
    """Stock Smart Life scheme: ``md5(ecode)[8:24]``."""
    return md5hex(ecode)[8:24]


def oem_password(signing_key: str, ecode: str) -> str:
    """OEM scheme: ``md5(md5(G) + ecode)[8:24]``, G = the request-signing key."""
    return md5hex(md5hex(signing_key) + ecode)[8:24]


def oem_username(s: Session, ch_key: str) -> str:
    """OEM scheme: ``{partner}_v1_{appKey}_{chKey}_mb_{sid}{tail}``."""
    tail = md5hex(md5hex(s.app_key) + s.ecode)[-16:]
    return f"{s.partner}_v1_{s.app_key}_{ch_key}_mb_{s.sid}{tail}"


def ch_key_for(app_key: str, message: str) -> str:
    """``hmac_sha256(appKey, package + "_" + certSHA256)[8:16]``."""
    return hmac.new(app_key.encode(), message.encode(), hashlib.sha256).hexdigest()[8:16]


def build_variants(s: Session, packages: list[str], cert: str | None, profile_phone_id: str) -> list[Variant]:
    """Credential schemes in the order worth trying."""
    phone_id = uuid.uuid4().hex[:24]
    variants: list[Variant] = []
    for package in packages:
        cid = client_id_for(package, phone_id, s.uid)
        variants.append(Variant(f"stock sid/md5(ecode) cid={package}", cid, s.sid, stock_password(s.ecode)))
        variants.append(Variant(f"sid + oem password cid={package}", cid, s.sid, oem_password(s.signing_key, s.ecode)))
        ch_inputs = [f"{package}_{cert}"] if cert else []
        ch_inputs += ["A", f"{package}_A"]
        for ch_in in ch_inputs:
            label = "cert" if cert and ch_in.endswith(cert) else ch_in.replace(package, "pkg")
            variants.append(
                Variant(
                    f"oem chKey({label}) cid={package}",
                    cid,
                    oem_username(s, ch_key_for(s.app_key, ch_in)),
                    oem_password(s.signing_key, s.ecode),
                )
            )
    # The integration's fixed phone id, in case the broker ties the client id to the API login.
    cid = client_id_for(packages[0], profile_phone_id, s.uid)
    variants.append(Variant("stock, profile phone id", cid, s.sid, stock_password(s.ecode)))
    # The will message is reportedly required for DP routing; try one without it last.
    variants.append(
        Variant(
            "stock, no will", client_id_for(packages[0], phone_id, s.uid), s.sid, stock_password(s.ecode), will=False
        )
    )
    return variants


# ---------------------------------------------------------------------------- decoding


def _aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives import padding  # noqa: PLC0415
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: PLC0415

    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()  # noqa: S305 - protocol
    raw = decryptor.update(data) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(raw) + unpadder.finalize()


def _aes_gcm_decrypt(key: bytes, nonce: bytes, data: bytes, aad: bytes | None) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

    return AESGCM(key).decrypt(nonce, data, aad)


def _as_json(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def decode_frame(payload: bytes, local_key: str | None) -> tuple[str, Any]:
    """Return (format, decoded object or error text)."""
    key = local_key.encode() if local_key else None
    head = payload[:3]
    try:
        if payload[:1] == b"{":
            obj = _as_json(payload)
            data = obj.get("data") if isinstance(obj, dict) else None
            if key and isinstance(data, str):
                for label, blob in (("hex", _try(bytes.fromhex, data)), ("b64", _try(base64.b64decode, data))):
                    if blob:
                        plain = _try(lambda b=blob: _as_json(_aes_ecb_decrypt(key, b)))
                        if plain is not None:
                            obj = dict(obj, data=plain)
                            return f"json(pv{obj.get('pv')}, data {label}+aes)", obj
            return "json", obj
        if key is None:
            return f"{head!r} (no local key)", payload[:48].hex()
        if head == b"2.2":
            return "2.2", _as_json(_aes_ecb_decrypt(key, payload[15:]))
        if head == b"2.1":
            return "2.1", _as_json(_aes_ecb_decrypt(key, base64.b64decode(payload[19:])))
        if head == b"2.3":
            errors = []
            for hdr_len in (12, 15):
                for aad in (payload[:hdr_len], payload[3:hdr_len], None):
                    try:
                        nonce = payload[hdr_len : hdr_len + 12]
                        plain = _aes_gcm_decrypt(key, nonce, payload[hdr_len + 12 :], aad)
                        return f"2.3(hdr={hdr_len})", _as_json(plain)
                    except Exception as err:  # noqa: BLE001 - probing layouts
                        errors.append(type(err).__name__)
            return "2.3 (undecoded)", f"{payload[:40].hex()} errors={sorted(set(errors))}"
        return f"unknown {head!r}", payload[:48].hex()
    except Exception as err:  # noqa: BLE001 - research tool, report everything
        return f"{head!r} decode failed: {type(err).__name__}: {err}", payload[:48].hex()


def frame_header(payload: bytes) -> str:
    """Sequence / origin of a binary frame, and the CRC check for 2.2."""
    head = payload[:3]
    if head == b"2.2" and len(payload) >= 15:
        crc, s, o = struct.unpack(">III", payload[3:15])
        ok = crc == (zlib.crc32(payload[7:]) & 0xFFFFFFFF)
        return f"s={s} o={o} crc={'ok' if ok else 'BAD'}"
    if head == b"2.3" and len(payload) >= 12:
        s, o = struct.unpack(">II", payload[3:11])
        return f"s={s} o={o}"
    return ""


# ---------------------------------------------------------------------------- encoding


def _aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives import padding  # noqa: PLC0415
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: PLC0415

    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()  # noqa: S305 - protocol
    return encryptor.update(padded) + encryptor.finalize()


def command_json(dps: dict[str, Any], now: float | None = None) -> bytes:
    """Plaintext of a DP command: ``{"data":{"dps":…},"protocol":5,"t":<s>}``."""
    body = {"data": {"dps": dps}, "protocol": 5, "t": int(now if now is not None else time.time())}
    return json.dumps(body, separators=(",", ":")).encode()


def encode_command(pv: str, local_key: str, plain: bytes, seq: int, origin: int, nonce: bytes | None = None) -> bytes:
    """Outbound frame for ``smart/mb/out/{devId}``; the version follows the device's cloud pv."""
    key = local_key.encode()
    so = struct.pack(">II", seq, origin)
    if _pv_float(pv) >= 2.3:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

        header = b"2.3" + so + b"\x00"
        nonce = nonce if nonce is not None else os.urandom(12)
        return header + nonce + AESGCM(key).encrypt(nonce, plain, header)
    if _pv_float(pv) >= 2.2:
        body = so + _aes_ecb_encrypt(key, plain)
        return b"2.2" + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF) + body
    raise ValueError(f"pv {pv!r} not supported by the probe")


def _pv_float(pv: str) -> float:
    try:
        return float(pv)
    except (TypeError, ValueError):
        return 0.0


def _try(func: Any, *args: Any) -> Any:
    try:
        return func(*args)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------- cloud


@dataclass
class DeviceInfo:
    dev_id: str
    name: str
    local_key: str
    parents: set[str] = field(default_factory=set)
    online: Any = None
    pv: str = ""
    dps: dict[str, Any] = field(default_factory=dict)


async def cloud_login(
    profile_key: str, region: str, email: str, password: str, log: Log
) -> tuple[Session, dict[str, DeviceInfo], str]:
    import aiohttp  # noqa: PLC0415

    from custom_components.tuya_eco.providers.oem_profiles import PROFILES  # noqa: PLC0415
    from custom_components.tuya_eco.providers.tuya_oem import REGIONS, TuyaMobileApi, _textbook_rsa_encrypt  # noqa: PLC0415

    profile = PROFILES[profile_key]
    endpoint, country = REGIONS[region]
    async with aiohttp.ClientSession() as http:
        api = TuyaMobileApi(http, profile, endpoint, country, email, password)
        token = await api._request(  # noqa: SLF001 - research tool needs the raw login result
            "tuya.m.user.email.token.create",
            {"countryCode": country, "email": email},
            requires_sid=False,
            allow_relogin=False,
        )
        pw_md5 = hashlib.md5(password.encode()).hexdigest().encode()  # noqa: S324
        encrypted = _textbook_rsa_encrypt(int(token["publicKey"]), int(token["exponent"]), pw_md5)
        info = await api._request(  # noqa: SLF001
            "tuya.m.user.email.password.login",
            {
                "countryCode": str(country),
                "email": email,
                "ifencrypt": 1,
                "options": '{"group": 1}',
                "passwd": "0" * 64 + encrypted,
                "token": token["token"],
            },
            requires_sid=False,
            allow_relogin=False,
        )
        api.sid = info["sid"]

        log("login result fields:")
        secret_keys = {
            "sid",
            "ecode",
            "refreshToken",
            "sessionId",
            "email",
            "mobile",
            "username",
            "nickname",
            "headPic",
        }
        for k in sorted(info):
            v = info[k]
            if k == "domain" and isinstance(v, dict):
                log("  domain:")
                for dk in sorted(v):
                    log(f"    {dk} = {v[dk]}")
            elif k in secret_keys:
                log(f"  {k}: {shape(v)}")
            elif k in ("uid", "partnerIdentity"):
                log(f"  {k}: {short_id(str(v))} ({shape(v)})")
            elif isinstance(v, (dict, list)):
                log(f"  {k}: {type(v).__name__}[{len(v)}]")
            else:
                log(f"  {k}: {str(v)[:60]}")

        domain = info.get("domain") or {}
        host = domain.get("mobileMqttsUrl") or f"m1.tuya{region.lower()}.com"
        port = int(domain.get("mqttsPort") or domain.get("mobileMqttsPort") or 8883)
        session = Session(
            sid=info["sid"],
            ecode=str(info.get("ecode") or ""),
            uid=str(info.get("uid") or ""),
            partner=str(info.get("partnerIdentity") or ""),
            app_key=profile.client_id,
            signing_key=profile.secret,
            host=host,
            port=port,
        )
        if not session.ecode:
            log("WARNING: no ecode in the login result; the MQTT passwords below will be wrong")

        devices: dict[str, DeviceInfo] = {}
        for group in await api.groups():
            gid = str(group.get("groupId"))
            for raw in await api.devices(gid):
                dev_id = str(raw.get("devId") or "")
                if not dev_id:
                    continue
                d = DeviceInfo(
                    dev_id,
                    str(raw.get("name") or dev_id),
                    str(raw.get("localKey") or ""),
                    online=raw.get("isOnline"),
                    pv=str(raw.get("pv") or ""),
                    dps=dict(raw.get("dps") or {}),
                )
                for parent_key in ("parentId", "meshId", "gwId"):
                    p = raw.get(parent_key)
                    if p and p != dev_id:
                        d.parents.add(str(p))
                devices[dev_id] = d
        return session, devices, profile.device_id


# ---------------------------------------------------------------------------- mqtt


class Probe:
    """paho wrapper: connect with one variant, subscribe, log messages."""

    def __init__(self, session: Session, devices: dict[str, DeviceInfo], log: Log) -> None:
        self.s = session
        self.devices = devices
        self.log = log
        self.client: Any = None
        self.connected = threading.Event()
        self.rc: int | None = None
        self.sub_topics: dict[int, str] = {}
        self.stats: Counter[str] = Counter()
        self.seen = 0
        # Outbound: one random origin per session, sequence counting up from 2 (as the app does).
        self.origin = int.from_bytes(os.urandom(4), "big") & 0x7FFFFFFF
        self.seq = 1
        self.pending: dict[str, tuple[float, dict[str, Any]]] = {}  # devId -> (sent at, dps)
        self.last_dps: dict[str, dict[str, Any]] = {}
        self.in_version: dict[str, str] = {}  # devId -> frame version the device sends ("2.2"/"2.3")
        self.watch_out: set[str] = set()  # devIds whose command topic is watched too
        self.publish_done: dict[int, float] = {}

    def _new_client(self, variant: Variant, verify_tls: bool) -> Any:
        import paho.mqtt.client as mqtt  # noqa: PLC0415

        kwargs: dict[str, Any] = {}
        if hasattr(mqtt, "CallbackAPIVersion"):
            kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION1
        client = mqtt.Client(client_id=variant.client_id, clean_session=True, protocol=mqtt.MQTTv311, **kwargs)
        client.username_pw_set(variant.username, variant.password)
        ctx = ssl.create_default_context()
        if not verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        client.tls_set_context(ctx)
        if variant.will:
            will = {"clientId": variant.client_id, "deviceType": "ANDROID", "message": "", "userName": variant.username}
            client.will_set("tuya/smart/will", json.dumps(will, separators=(",", ":")), qos=1, retain=False)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_subscribe = self._on_subscribe
        client.on_message = self._on_message
        client.on_publish = self._on_publish
        return client

    def _on_publish(self, _c: Any, _u: Any, mid: int) -> None:
        self.publish_done[mid] = time.monotonic()

    def send(self, dev: DeviceInfo, dps: dict[str, Any]) -> None:
        """Publish a DP command to ``smart/mb/out/{devId}`` and remember it for the echo check."""
        self.seq += 1
        pv = dev.pv if _pv_float(dev.pv) >= 2.2 else self.in_version.get(dev.dev_id, "")
        frame = encode_command(pv, dev.local_key, command_json(dps), self.seq, self.origin)
        sent = time.monotonic()
        info = self.client.publish(f"smart/mb/out/{dev.dev_id}", frame, qos=1, retain=False)
        self.pending[dev.dev_id] = (sent, dps)
        self.log(
            f"SEND smart/mb/out/<{dev.name}> pv={dev.pv} frame={frame[:3].decode()} len={len(frame)} "
            f"s={self.seq} dps={json.dumps(dps)} mid={info.mid}"
        )
        deadline = sent + 5
        while info.mid not in self.publish_done and time.monotonic() < deadline:
            time.sleep(0.02)
        if info.mid in self.publish_done:
            self.log(f"  PUBACK after {(self.publish_done[info.mid] - sent) * 1000:.0f} ms")
        else:
            self.log("  no PUBACK within 5 s")

    def _on_connect(self, _c: Any, _u: Any, _flags: Any, rc: int) -> None:
        self.rc = rc
        self.connected.set()

    def _on_disconnect(self, _c: Any, _u: Any, rc: int) -> None:
        if self.seen or rc:
            self.log(f"disconnected rc={rc}")

    def _on_subscribe(self, _c: Any, _u: Any, mid: int, granted: Any) -> None:
        topic = self.sub_topics.get(mid, "?")
        self.log(f"  SUBACK {self._label(topic)}: {list(granted)}{'  (refused)' if 128 in list(granted) else ''}")

    def _label(self, topic: str) -> str:
        out = topic
        for dev_id, d in self.devices.items():
            if dev_id in topic:
                out = topic.replace(dev_id, f"<{d.name}>")
        if self.s.uid:
            out = out.replace(self.s.uid, "<uid>")
        if self.s.partner:
            out = out.replace(self.s.partner, "<partner>")
        return out

    def _on_message(self, _c: Any, _u: Any, msg: Any) -> None:
        self.seen += 1
        topic = msg.topic
        dev_id = topic.rsplit("/", 1)[-1]
        dev = self.devices.get(dev_id)
        payload = bytes(msg.payload)
        if payload[:3] in (b"2.2", b"2.3"):
            self.in_version[dev_id] = payload[:3].decode()
        fmt, obj = decode_frame(payload, dev.local_key if dev else None)
        proto = obj.get("protocol") if isinstance(obj, dict) else None
        self.stats[f"{fmt} protocol={proto}"] += 1
        self.log(f"MSG {self._label(topic)} [{fmt}] len={len(payload)} protocol={proto} {frame_header(payload)}")
        text = json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
        self.log(f"    {self._label(text)[:1500]}")
        dps = (obj.get("data") or {}).get("dps") if isinstance(obj, dict) and proto == 4 else None
        if isinstance(dps, dict):
            self.last_dps.setdefault(dev_id, {}).update(dps)
            pending = self.pending.get(dev_id)
            if pending and any(str(k) in dps for k in pending[1]):
                sent_at, wanted = pending
                match = all(dps.get(str(k)) == v for k, v in wanted.items() if str(k) in dps)
                self.log(
                    f"  ECHO after {(time.monotonic() - sent_at) * 1000:.0f} ms, "
                    f"{'matches' if match else 'DIFFERS from'} the command"
                )
                del self.pending[dev_id]

    def try_variant(self, variant: Variant) -> bool:
        for verify in (True, False):
            self.connected.clear()
            self.rc = None
            client = self._new_client(variant, verify)
            try:
                client.connect(self.s.host, self.s.port, keepalive=60)
            except ssl.SSLError as err:
                if verify:
                    self.log(f"  TLS verification failed ({err.reason}); retrying without verification")
                    continue
                self.log(f"  TLS error: {err}")
                return False
            except OSError as err:
                self.log(f"  connect error: {err}")
                return False
            client.loop_start()
            self.connected.wait(15)
            if self.rc == 0:
                self.client = client
                self.log(f"  CONNACK rc=0 (accepted), TLS verified={verify}")
                return True
            client.disconnect()
            client.loop_stop()
            self.log(f"  CONNACK rc={self.rc} ({_rc_text(self.rc)})")
            return False
        return False

    def subscribe_all(self) -> None:
        topics: list[str] = []
        for dev_id, d in self.devices.items():
            topics.append(f"smart/mb/in/{dev_id}")
            topics.extend(f"smart/mb/in/{p}" for p in d.parents)
            if dev_id in self.watch_out:  # commands other clients send to this device
                topics.append(f"smart/mb/out/{dev_id}")
        if self.s.uid:
            topics.append(f"smart/mb/{self.s.uid}")
            if self.s.partner:
                topics.append(f"{self.s.partner}/mb/{self.s.uid}")
        for topic in dict.fromkeys(topics):
            _res, mid = self.client.subscribe(topic, qos=1)
            self.sub_topics[mid] = topic
            time.sleep(0.05)


def _rc_text(rc: int | None) -> str:
    return {
        None: "no CONNACK within 15 s",
        1: "bad protocol version",
        2: "client id rejected",
        3: "server unavailable",
        4: "bad username or password",
        5: "not authorised",
    }.get(rc, "unknown")


# ---------------------------------------------------------------------------- main


def credentials_from_ha(config_dir: Path, profile_key: str, log: Log) -> tuple[str, str, str]:
    """E-mail, password and region of the tuya_eco entry that uses `profile_key`."""
    store = json.loads((config_dir / ".storage" / "core.config_entries").read_text(encoding="utf-8"))
    entries = [
        e
        for e in store["data"]["entries"]
        if e.get("domain") == "tuya_eco" and e.get("data", {}).get("provider") == profile_key
    ]
    if not entries:
        raise SystemExit(f"no tuya_eco config entry with provider {profile_key!r} in {config_dir}")
    entry = entries[0]
    data = entry["data"]["provider_data"]
    log(f"account from HA config entry {entry.get('title')!r} ({len(entries)} matching)")
    return data["email"], data["password"], data.get("region") or "EU"


def blink(probe: Probe, dev: DeviceInfo, dp: str, hold: float, log: Log) -> None:
    """Flip a boolean DP over MQTT, hold, flip it back; report PUBACK and echo for each step."""
    if _pv_float(dev.pv) < 2.2:
        log(f"<{dev.name}> has no usable cloud pv ({dev.pv!r}); waiting for its first frame to learn the version")
        deadline = time.monotonic() + 45
        while dev.dev_id not in probe.in_version and time.monotonic() < deadline:
            time.sleep(0.5)
        if dev.dev_id not in probe.in_version:
            log("BLINK skipped: the device sent nothing within 45 s")
            return
        log(f"<{dev.name}> sends {probe.in_version[dev.dev_id]} frames; using the same for commands")
    current = probe.last_dps.get(dev.dev_id, {}).get(dp, dev.dps.get(dp))
    if not isinstance(current, bool):
        log(f"BLINK skipped: DP {dp} of <{dev.name}> is {current!r}, not a boolean")
        return
    log(f"BLINK <{dev.name}> DP {dp}: {current} -> {not current} -> {current} (hold {hold:g} s)")
    probe.send(dev, {dp: not current})
    time.sleep(hold)
    if dev.dev_id in probe.pending:
        log("  no echo for the first command yet")
    probe.send(dev, {dp: current})
    time.sleep(6.0)
    if dev.dev_id in probe.pending:
        log("  no echo for the restoring command within 6 s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="ledvance", help="OEM app profile key (default: ledvance)")
    parser.add_argument("--region", default="EU", choices=("EU", "US", "CN", "IN"))
    parser.add_argument("--minutes", type=float, default=5.0, help="how long to listen (default 5)")
    parser.add_argument("--package", action="append", help="app package name(s) for the client id / chKey")
    parser.add_argument("--cert-sha256", help="app signing certificate SHA-256, colon-separated upper case (optional)")
    parser.add_argument("--all-variants", action="store_true", help="test every scheme even after one succeeds")
    parser.add_argument(
        "--ha-config",
        type=Path,
        help="Home Assistant config dir: take the account from the tuya_eco config entry of --profile "
        "and import the integration from <dir>/custom_components",
    )
    parser.add_argument("--log", type=Path, default=LOG_PATH, help=f"log file (default {LOG_PATH})")
    parser.add_argument(
        "--blink",
        metavar="DEVICE",
        help="outbound test: flip a boolean DP of this device over MQTT and flip it back "
        "(device id, id suffix or exact name)",
    )
    parser.add_argument(
        "--watch-out",
        metavar="DEVICE",
        action="append",
        default=[],
        help="also subscribe to smart/mb/out/<id> of this device (id suffix or exact name) to see commands",
    )
    parser.add_argument("--blink-dp", default="20", help="boolean DP to flip (default 20, switch_led)")
    parser.add_argument("--blink-seconds", type=float, default=4.0, help="how long to hold the flipped state")
    args = parser.parse_args()

    log = Log(args.log)
    region = args.region
    if args.ha_config:
        sys.path.insert(0, str(args.ha_config))
        email, password, region = credentials_from_ha(args.ha_config, args.profile, log)
    else:
        email = os.environ.get("TUYA_EMAIL") or input("SMART+ e-mail: ").strip()
        password = os.environ.get("TUYA_PASSWORD") or getpass.getpass("SMART+ password (not echoed): ")

    session, devices, profile_phone_id = asyncio.run(cloud_login(args.profile, region, email, password, log))
    del password
    log(f"broker: {session.host}:{session.port}")
    log(f"devices: {len(devices)}")
    for d in devices.values():
        parents = f" parents={[short_id(p) for p in d.parents]}" if d.parents else ""
        log(
            f"  {d.name}: id={short_id(d.dev_id)} pv={d.pv} key={'yes' if d.local_key else 'NO'} "
            f"online={d.online}{parents}"
        )
    log(
        f"session: sid {shape(session.sid)}, ecode {shape(session.ecode)}, "
        f"uid {shape(session.uid)}, partnerIdentity {shape(session.partner)}"
    )

    packages = args.package or list(DEFAULT_PACKAGES)
    variants = build_variants(session, packages, args.cert_sha256, profile_phone_id)
    probe = Probe(session, devices, log)
    winner: Variant | None = None
    for variant in variants:
        log(f"trying: {variant.name} (will={'yes' if variant.will else 'no'})")
        if probe.try_variant(variant):
            if winner is None:
                winner = variant
            if not args.all_variants:
                break
            probe.client.disconnect()
            probe.client.loop_stop()
            probe.client = None
        time.sleep(1.0)

    if winner is None:
        log("RESULT: no credential scheme was accepted")
        return 1
    if probe.client is None:  # --all-variants: reconnect with the first accepted scheme
        probe.try_variant(winner)
    log(f"RESULT: accepted scheme = {winner.name}")

    target: DeviceInfo | None = None
    if args.blink:
        matches = [d for d in devices.values() if args.blink in (d.dev_id, d.name) or d.dev_id.endswith(args.blink)]
        if len(matches) != 1:
            log(f"--blink {args.blink!r} matches {len(matches)} devices; need exactly one")
            return 2
        target = matches[0]

    for wanted in args.watch_out:
        probe.watch_out.update(
            d.dev_id for d in devices.values() if wanted in (d.dev_id, d.name) or d.dev_id.endswith(wanted)
        )
    probe.subscribe_all()
    deadline = time.monotonic() + args.minutes * 60
    log(f"listening for {args.minutes:g} min: toggle a device in the app, then cut its power for a minute")
    try:
        if target is not None:
            time.sleep(8.0)  # let SUBACKs and the first reports arrive
            blink(probe, target, args.blink_dp, args.blink_seconds, log)
        while time.monotonic() < deadline:
            time.sleep(1.0)
    except KeyboardInterrupt:
        log("stopped by user")
    probe.client.disconnect()
    probe.client.loop_stop()

    log(f"messages received: {probe.seen}")
    for key, count in probe.stats.most_common():
        log(f"  {count:4d}  {key}")
    log(f"log saved to {args.log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
