"""Tuya "mobile app" (OEM) API provider.

White-label apps built on the Tuya SDK (Ledvance SMART+, Sylvania Smart WiFi, …)
cannot scan the QR codes of the IoT platform or the device-sharing service. Their
backend is still Tuya, reachable through the mobile-app API at
``a1.tuya{region}.com/api.json``. Logging in with the app's own identifiers and the
user's e-mail/password returns every device with its ``localKey`` and DP schema.

An *app profile* holds the identifiers of one such app. Profiles are registered in
`oem_profiles.py`; a generic variant lets the user paste them manually.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any
import uuid

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .base import (
    BaseProvider,
    CloudDevice,
    DpSpec,
    DpType,
    Home,
    LoginKind,
    LoginStep,
    ProviderAuthError,
    ProviderError,
    PushListener,
)
from .oem_mqtt import (
    AUTH_REFUSED,
    OemMqttChannel,
    decode_frame,
    encode_command,
    mqtt_client_id,
    mqtt_host_for,
    mqtt_password,
)

_LOGGER = logging.getLogger(__name__)

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_REGION = "region"
CONF_COUNTRY_CODE = "country_code"
CONF_SID = "sid"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_CLIENT_ID = "client_id"
CONF_SECRET = "secret"
CONF_ECODE = "ecode"
CONF_UID = "uid"
CONF_MQTT_HOST = "mqtt_host"
CONF_MQTT_PORT = "mqtt_port"
CONF_MQTT_ID = "mqtt_id"

# A refused MQTT login triggers a new API session at most this often.
_MQTT_RELOGIN_INTERVAL = 600.0
# Devices push to the cloud only when their state changes or the LAN side polls them, so a
# device that is unreachable on the LAN stays silent on MQTT. This long after the channel
# (re)connects, the ones that have not reported are asked about through the HTTP API once.
_SILENT_CHECK_DELAY = 90.0
_SILENT_CHECK_MAX = 30

REGIONS: dict[str, tuple[str, int]] = {
    "EU": ("https://a1.tuyaeu.com/api.json", 49),
    "US": ("https://a1.tuyaus.com/api.json", 1),
    "CN": ("https://a1.tuyacn.com/api.json", 86),
    "IN": ("https://a1.tuyain.com/api.json", 91),
}

_SIGN_KEYS = frozenset(
    {
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
    }
)
_API_VERSIONS = {
    "tuya.m.device.sub.list": "1.1",
    "tuya.m.location.room.list": "2.0",
    "tuya.m.infrared.keydata.get": "2.0",
}
_MIN_REFRESH_INTERVAL = 300.0


@dataclass(slots=True)
class OemAppProfile:
    """Identifiers of one white-label Tuya app."""

    key: str
    name: str
    client_id: str
    secret: str
    user_agent: str = "TY-UA=APP/Android/1.1.6/SDK/null"
    app_version: str = "1.1.6"
    device_id: str = "5fe5abb36728cce7b9cd2185625edccbd6d9bd787e40"
    regions: tuple[str, ...] = ("EU", "US", "CN", "IN")
    default_region: str = "EU"
    # Android package of the app: the first part of the MQTT client id.
    package: str = "com.tuya.smart"


class OemApiError(ProviderError):
    """API returned an error."""

    def __init__(self, code: str, message: str) -> None:
        """Store the error code."""
        super().__init__(f"{code}: {message}")
        self.code = code


class OemRateLimited(OemApiError):
    """HTTP 429 / too frequent."""


class OemSessionInvalid(OemApiError):
    """The session id expired."""


def _mobile_hash(data: str) -> str:
    digest = hashlib.md5(data.encode()).hexdigest()  # noqa: S324 - protocol requirement
    return digest[8:16] + digest[0:8] + digest[24:32] + digest[16:24]


def _textbook_rsa_encrypt(n: int, e: int, message: bytes) -> str:
    """Tuya uses raw (unpadded) RSA for the password field."""
    cipher = pow(int.from_bytes(message, "big"), e, n)
    length = max(1, (cipher.bit_length() + 7) // 8)
    return cipher.to_bytes(length, "big").hex()


@dataclass
class TuyaMobileApi:
    """Minimal asynchronous client for the Tuya mobile-app API."""

    session: aiohttp.ClientSession
    profile: OemAppProfile
    endpoint: str
    country_code: int
    email: str
    password: str
    sid: str | None = None
    refresh_token: str = ""
    # Push channel credentials from the login result (see `oem_mqtt.py`). `ecode` belongs
    # to the user and outlives sessions; the silent refresh may not return it.
    ecode: str = ""
    uid: str = ""
    mqtt_host: str = ""
    mqtt_port: int = 8883
    # Called after every successful login (new sid): persist it, re-key the push channel.
    on_session: Callable[[], None] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _sign(self, params: dict[str, str], post_data: str | None) -> str:
        data = dict(params)
        if post_data is not None:
            data["postData"] = post_data
        parts: list[str] = []
        for key in sorted(data):
            value = data[key]
            if key not in _SIGN_KEYS or not str(value):
                continue
            if key == "postData":
                parts.append(f"{key}={_mobile_hash(value)}")
            else:
                parts.append(f"{key}={value}")
        return hmac.new(self.profile.secret.encode(), "||".join(parts).encode(), hashlib.sha256).hexdigest()

    async def _request(
        self,
        action: str,
        post_data: dict[str, Any] | None = None,
        *,
        gid: str | None = None,
        requires_sid: bool = True,
        allow_relogin: bool = True,
    ) -> Any:
        params = {
            "a": action,
            "appVersion": self.profile.app_version,
            "appRnVersion": "5.14",
            "channel": "oem",
            "deviceId": self.profile.device_id,
            "platform": "Linux",
            "requestId": str(uuid.uuid4()),
            "lang": "en",
            "clientId": self.profile.client_id,
            "osSystem": "9",
            "os": "Android",
            "timeZoneId": "America/Sao_Paulo",
            "ttid": f"sdk_tuya@{self.profile.client_id}",
            "et": "0.0.1",
            "v": _API_VERSIONS.get(action, "1.0"),
            "sdkVersion": "3.10.0",
            "time": str(int(time.time())),
        }
        if gid:
            params["gid"] = gid
        if requires_sid:
            if not self.sid:
                raise OemSessionInvalid("USER_SESSION_INVALID", "not logged in")
            params["sid"] = self.sid
        body = json.dumps(post_data, separators=(",", ":")) if post_data is not None else None
        params["sign"] = self._sign(params, body)
        headers = {"User-Agent": self.profile.user_agent}
        form = {"postData": body} if body is not None else None

        for attempt in range(1, 4):
            try:
                async with self.session.post(
                    self.endpoint, params=params, data=form, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    if resp.status == 429:
                        raise OemRateLimited("HTTP_429", "rate limited")
                    payload = await resp.json(content_type=None)
            except aiohttp.ClientError as err:
                raise ProviderError(f"cloud unreachable: {err}") from err
            except TimeoutError as err:
                raise ProviderError("cloud request timed out") from err
            try:
                return self._handle(payload)
            except OemRateLimited:
                if attempt == 3:
                    raise
                await asyncio.sleep(10.0 * attempt)
                params["time"] = str(int(time.time()))
                params["requestId"] = str(uuid.uuid4())
                params["sign"] = self._sign(params, body)
            except OemSessionInvalid:
                if not allow_relogin or not requires_sid:
                    raise
                await self.login()
                return await self._request(action, post_data, gid=gid, requires_sid=requires_sid, allow_relogin=False)
        raise ProviderError("unreachable")

    @staticmethod
    def _handle(payload: Any) -> Any:
        if not isinstance(payload, dict):
            raise ProviderError(f"unexpected response {payload!r}")
        if payload.get("success"):
            return payload.get("result")
        code = str(payload.get("errorCode", ""))
        msg = str(payload.get("errorMsg", ""))
        if code == "USER_SESSION_INVALID":
            raise OemSessionInvalid(code, msg)
        if code in ("USER_PASSWD_WRONG", "USER_NOT_EXIST", "USER_EMAIL_NOT_EXIST", "USER_EMAIL_ILLEGAL"):
            raise ProviderAuthError(f"{code}: {msg}")
        if code == "REQUEST_TOO_FREQUENTLY_PLEASE_TRY_AGAIN_LATER":
            raise OemRateLimited(code, msg)
        raise OemApiError(code, msg)

    # ------------------------------------------------------------------ auth

    async def login(self, *, full: bool = False) -> None:
        """Obtain a session: silent refresh first, then e-mail/password (`full` skips the refresh)."""
        async with self._lock:
            if self.refresh_token and not full:
                try:
                    result = await self._request(
                        "tuya.m.user.session.update",
                        {"refreshToken": self.refresh_token},
                        requires_sid=False,
                        allow_relogin=False,
                    )
                    if isinstance(result, dict) and result.get("sid"):
                        self.sid = result["sid"]
                        self.refresh_token = result.get("refreshToken", self.refresh_token)
                        self._take_session_info(result)
                        return
                except ProviderAuthError:
                    raise
                except ProviderError as err:
                    _LOGGER.debug("silent session refresh failed (%s); full login", err)
                    self.refresh_token = ""
            token_info = await self._request(
                "tuya.m.user.email.token.create",
                {"countryCode": self.country_code, "email": self.email},
                requires_sid=False,
                allow_relogin=False,
            )
            if not isinstance(token_info, dict):
                raise ProviderError("token.create returned no data")
            pw_md5 = hashlib.md5(self.password.encode()).hexdigest().encode()  # noqa: S324
            encrypted = _textbook_rsa_encrypt(int(token_info["publicKey"]), int(token_info["exponent"]), pw_md5)
            login_info = await self._request(
                "tuya.m.user.email.password.login",
                {
                    "countryCode": str(self.country_code),
                    "email": self.email,
                    "ifencrypt": 1,
                    "options": '{"group": 1}',
                    "passwd": "0" * 64 + encrypted,
                    "token": token_info["token"],
                },
                requires_sid=False,
                allow_relogin=False,
            )
            if not isinstance(login_info, dict) or not login_info.get("sid"):
                raise ProviderAuthError("login returned no session")
            self.sid = login_info["sid"]
            self.refresh_token = login_info.get("refreshToken", "")
            self._take_session_info(login_info)

    def _take_session_info(self, info: dict[str, Any]) -> None:
        """Keep the push credentials a login result carries, then tell the owner."""
        if info.get("ecode"):
            self.ecode = str(info["ecode"])
        if info.get("uid"):
            self.uid = str(info["uid"])
        domain = info.get("domain")
        if isinstance(domain, dict) and domain.get("mobileMqttsUrl"):
            self.mqtt_host = str(domain["mobileMqttsUrl"])
            try:
                self.mqtt_port = int(domain.get("mqttsPort") or 8883)
            except (TypeError, ValueError):
                self.mqtt_port = 8883
        if self.on_session is not None:
            self.on_session()

    # ------------------------------------------------------------------ data

    async def groups(self) -> list[dict[str, Any]]:
        """Homes ("locations") of the account."""
        result = await self._request("tuya.m.location.list")
        return list(result or [])

    async def devices(self, group_id: str) -> list[dict[str, Any]]:
        """Raw device list of one home (includes localKey and schema)."""
        result = await self._request("tuya.m.my.group.device.list", gid=group_id)
        return list(result or [])

    async def device_get(self, device_id: str) -> dict[str, Any]:
        """One device."""
        result = await self._request("tuya.m.device.get", {"devId": device_id})
        return dict(result or {})

    async def product_info(self, product_id: str) -> dict[str, Any]:
        """Product card: ``name`` is the model shown by the app (e.g. ``LDVWIFIB40E14RGBW``)."""
        result = await self._request("tuya.m.product.info.get", {"productId": product_id})
        return dict(result or {})

    async def publish_dps(self, device_id: str, gateway_id: str, dps: dict[str, Any]) -> None:
        """Cloud command (fallback)."""
        await self._request(
            "tuya.m.device.dp.publish",
            {"devId": device_id, "gwId": gateway_id or device_id, "dps": json.dumps(dps)},
        )


# ---------------------------------------------------------------------- schema

_PROPERTY_TYPES = {
    "bool": DpType.BOOLEAN,
    "value": DpType.INTEGER,
    "enum": DpType.ENUM,
    "string": DpType.STRING,
    "bitmap": DpType.BITMAP,
    "raw": DpType.RAW,
    "json": DpType.JSON,
}


def parse_mobile_schema(raw_schema: Any) -> dict[int, DpSpec]:
    """Convert the mobile API `schema` JSON into DpSpec objects."""
    if isinstance(raw_schema, str):
        try:
            raw_schema = json.loads(raw_schema)
        except ValueError:
            return {}
    if not isinstance(raw_schema, list):
        return {}
    result: dict[int, DpSpec] = {}
    for item in raw_schema:
        if not isinstance(item, dict) or "id" not in item or "code" not in item:
            continue
        prop = item.get("property") or {}
        if item.get("type") == "raw" or prop.get("type") == "raw":
            dp_type = DpType.RAW
        else:
            dp_type = _PROPERTY_TYPES.get(str(prop.get("type", "")).lower(), DpType.RAW)
        values: dict[str, Any] = {}
        if dp_type == DpType.INTEGER:
            values = {
                "min": prop.get("min", 0),
                "max": prop.get("max", 0),
                "scale": prop.get("scale", 0),
                "step": prop.get("step", 1),
                "unit": prop.get("unit", ""),
            }
        elif dp_type == DpType.ENUM:
            values = {"range": list(prop.get("range", []))}
        elif dp_type == DpType.BITMAP:
            values = {"label": list(prop.get("label", [])), "maxlen": prop.get("maxlen")}
        elif dp_type == DpType.STRING:
            values = {"maxlen": prop.get("maxlen")}
        mode = str(item.get("mode", "rw")).lower()
        dp_id = int(item["id"])
        result[dp_id] = DpSpec(
            dp_id=dp_id,
            code=str(item["code"]),
            type=dp_type,
            values=values,
            read_only=mode == "ro",
        )
    return result


def convert_mobile_device(raw: dict[str, Any], home_id: str = "", home_name: str = "") -> CloudDevice:
    """Convert a raw mobile-API device dict to CloudDevice."""
    schema = parse_mobile_schema(raw.get("schema"))
    code_by_dp = {dp: spec.code for dp, spec in schema.items()}
    status: dict[str, Any] = {}
    for dp, value in (raw.get("dps") or {}).items():
        try:
            code = code_by_dp.get(int(dp))
        except (TypeError, ValueError):
            continue
        if code:
            status[code] = value
    dev_id = str(raw["devId"])
    gw_id = str(raw.get("gwId") or dev_id)
    version = raw.get("pv")
    return CloudDevice(
        id=dev_id,
        name=str(raw.get("name") or dev_id),
        local_key=str(raw.get("localKey") or ""),
        category=str(raw.get("category") or ""),
        product_id=str(raw.get("productId") or ""),
        product_name=str(raw.get("productName") or raw.get("product_name") or ""),
        model=str(raw.get("model") or ""),
        icon=str(raw.get("iconUrl") or raw.get("icon") or ""),
        ip=None,  # `localIp` is the WAN address; LAN discovery is authoritative
        mac=str(raw.get("mac") or ""),
        online=raw.get("isOnline"),
        uuid=str(raw.get("uuid") or ""),
        node_id=str(raw.get("nodeId") or "") if gw_id != dev_id else "",
        gateway_id=gw_id if gw_id != dev_id else "",
        home_id=home_id,
        home_name=home_name,
        dp_schema=schema,
        status=status,
        protocol_version=str(version) if version else None,
    )


# ---------------------------------------------------------------------- provider


class TuyaOemProvider(BaseProvider):
    """Generic OEM-app provider: the user supplies the app identifiers."""

    key = "tuya_oem"
    name = "Other Tuya OEM app (app id + secret)"
    login_kind = LoginKind.CREDENTIALS
    supports_homes = True
    # The app's MQTT session (`oem_mqtt.py`) carries DP reports, online/offline events and
    # commands: devices unreachable on the LAN stay usable through it.
    supports_push = True
    profile: OemAppProfile | None = None  # fixed profile in subclasses
    login_fields = {
        CONF_EMAIL: "text",
        CONF_PASSWORD: "password",
        CONF_REGION: "select:EU,US,CN,IN",
        CONF_CLIENT_ID: "text",
        CONF_SECRET: "password",
    }

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        super().__init__(hass)
        self._api: TuyaMobileApi | None = None
        self._email = ""
        self._password = ""
        self._region = "EU"
        self._country_code: int | None = None
        self._sid: str | None = None
        self._refresh_token = ""
        self._custom_profile: OemAppProfile | None = None
        self._last_fetch = 0.0
        self._cache: list[CloudDevice] = []
        # DP schema per product id: the device list has no schema, `tuya.m.device.get`
        # does, and all devices of one product share it.
        self._schema_by_product: dict[str, dict[int, DpSpec]] = {}
        self._version_by_product: dict[str, str | None] = {}
        self._meta_by_product: dict[str, dict[str, str]] = {}
        # Persist the session after a re-login (the hub sets this).
        self.on_token_update: Callable[[dict[str, Any]], None] | None = None
        # Push channel: credentials survive restarts, the rest is runtime state.
        self._ecode = ""
        self._uid = ""
        self._mqtt_host = ""
        self._mqtt_port = 8883
        self._mqtt_id = ""  # per-installation part of the client id
        self._known: dict[str, CloudDevice] = {}
        self._push_listener: PushListener | None = None
        self._channel: OemMqttChannel | None = None
        self._frame_version: dict[str, str] = {}  # devId → "2.2" / "2.3", learned from its reports
        self._cloud_online: dict[str, bool] = {}
        self._seq = 1
        self._origin = int.from_bytes(os.urandom(4), "big") & 0x7FFFFFFF
        self._last_mqtt_relogin = 0.0
        self._relogin_task: asyncio.Task[None] | None = None
        self._push_lock = asyncio.Lock()
        self._silent_check: asyncio.TimerHandle | None = None
        self._silent_task: asyncio.Task[None] | None = None

    def _resolve_profile(self) -> OemAppProfile:
        if self.profile is not None:
            return self.profile
        if self._custom_profile is None:
            raise ProviderError("OEM app profile missing")
        return self._custom_profile

    def _get_api(self) -> TuyaMobileApi:
        if self._api is None:
            endpoint, default_cc = REGIONS.get(self._region, REGIONS["EU"])
            self._api = TuyaMobileApi(
                session=async_get_clientsession(self.hass),
                profile=self._resolve_profile(),
                endpoint=endpoint,
                country_code=self._country_code or default_cc,
                email=self._email,
                password=self._password,
                sid=self._sid,
                refresh_token=self._refresh_token,
                ecode=self._ecode,
                uid=self._uid,
                mqtt_host=self._mqtt_host,
                mqtt_port=self._mqtt_port,
                on_session=self._session_changed,
            )
        return self._api

    def _session_changed(self) -> None:
        """New API session: re-key the push channel and persist."""
        api = self._api
        if api is None:
            return
        self._ecode, self._uid = api.ecode, api.uid
        self._mqtt_host, self._mqtt_port = api.mqtt_host, api.mqtt_port
        if self._channel is not None and api.sid and api.ecode:
            self._channel.set_credentials(api.sid, mqtt_password(api.ecode))
        if self.on_token_update is not None:
            self.on_token_update({})

    async def async_start_login(self, user_input: dict[str, Any]) -> LoginStep:
        """Store credentials; the actual login happens in finish."""
        self._email = str(user_input[CONF_EMAIL]).strip()
        self._password = str(user_input[CONF_PASSWORD])
        self._region = str(user_input.get(CONF_REGION, "EU"))
        cc = user_input.get(CONF_COUNTRY_CODE)
        self._country_code = int(cc) if cc else None
        if self.profile is None:
            self._custom_profile = OemAppProfile(
                key="custom",
                name="Custom",
                client_id=str(user_input[CONF_CLIENT_ID]).strip(),
                secret=str(user_input[CONF_SECRET]).strip(),
            )
        self._api = None
        return LoginStep(kind=LoginKind.CREDENTIALS)

    async def async_finish_login(self, state: dict[str, Any]) -> None:
        """Log in with e-mail / password."""
        api = self._get_api()
        await api.login()
        self._sid = api.sid
        self._refresh_token = api.refresh_token

    def to_entry_data(self) -> dict[str, Any]:
        """Serialise (credentials are needed for silent re-login)."""
        data = {
            CONF_EMAIL: self._email,
            CONF_PASSWORD: self._password,
            CONF_REGION: self._region,
            CONF_COUNTRY_CODE: self._country_code,
            CONF_SID: self._api.sid if self._api else self._sid,
            CONF_REFRESH_TOKEN: self._api.refresh_token if self._api else self._refresh_token,
            CONF_ECODE: self._api.ecode if self._api else self._ecode,
            CONF_UID: self._api.uid if self._api else self._uid,
            CONF_MQTT_HOST: self._api.mqtt_host if self._api else self._mqtt_host,
            CONF_MQTT_PORT: self._api.mqtt_port if self._api else self._mqtt_port,
            CONF_MQTT_ID: self._mqtt_id,
        }
        if self.profile is None and self._custom_profile:
            data[CONF_CLIENT_ID] = self._custom_profile.client_id
            data[CONF_SECRET] = self._custom_profile.secret
        return data

    @classmethod
    def from_entry_data(cls, hass: HomeAssistant, data: dict[str, Any]) -> TuyaOemProvider:
        """Restore."""
        provider = cls(hass)
        provider._email = data[CONF_EMAIL]
        provider._password = data[CONF_PASSWORD]
        provider._region = data.get(CONF_REGION, "EU")
        provider._country_code = data.get(CONF_COUNTRY_CODE)
        provider._sid = data.get(CONF_SID)
        provider._refresh_token = data.get(CONF_REFRESH_TOKEN, "")
        provider._ecode = str(data.get(CONF_ECODE) or "")
        provider._uid = str(data.get(CONF_UID) or "")
        provider._mqtt_host = str(data.get(CONF_MQTT_HOST) or "")
        provider._mqtt_port = int(data.get(CONF_MQTT_PORT) or 8883)
        provider._mqtt_id = str(data.get(CONF_MQTT_ID) or "")
        if cls.profile is None:
            provider._custom_profile = OemAppProfile(
                key="custom",
                name="Custom",
                client_id=data.get(CONF_CLIENT_ID, ""),
                secret=data.get(CONF_SECRET, ""),
            )
        return provider

    @property
    def account_id(self) -> str:
        """Unique id."""
        return f"{self.key}:{self._email.lower()}"

    async def async_fetch_devices(self) -> list[CloudDevice]:
        """Return devices of all homes. Throttled to protect against rate limits."""
        now = time.monotonic()
        if self._cache and now - self._last_fetch < _MIN_REFRESH_INTERVAL:
            return list(self._cache)
        api = self._get_api()
        if not api.sid:
            await api.login()
        devices: list[CloudDevice] = []
        for group in await api.groups():
            gid = group.get("groupId")
            if gid is None or not self.home_selected(str(gid)):
                continue
            for raw in await api.devices(str(gid)):
                try:
                    device = convert_mobile_device(raw, str(gid), str(group.get("name") or gid))
                    await self._async_attach_schema(api, device, raw)
                    devices.append(device)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Skipping device %s: %s", raw.get("devId"), err)
        self._cache = devices
        self._last_fetch = now
        self.update_known_devices(devices)
        return list(devices)

    async def _async_attach_schema(self, api: TuyaMobileApi, device: CloudDevice, raw: dict[str, Any]) -> None:
        """Fill the DP schema (and protocol hint) from `tuya.m.device.get`, once per product."""
        if device.dp_schema:
            return
        pid = device.product_id or device.id
        if pid not in self._schema_by_product:
            try:
                detail = await api.device_get(device.id)
            except ProviderError as err:
                _LOGGER.debug("%s: device.get failed: %s", device.id, err)
                return
            schema = parse_mobile_schema(detail.get("schema"))
            if not schema:
                return
            self._schema_by_product[pid] = schema
            self._version_by_product[pid] = str(detail["pv"]) if detail.get("pv") else None
            product_name = str(detail.get("productName") or detail.get("product_name") or "")
            if not product_name and device.product_id and not device.product_name:
                # Neither the device list nor device.get carry the model name; the product card does.
                try:
                    product_name = str((await api.product_info(device.product_id)).get("name") or "")
                except ProviderError as err:
                    _LOGGER.debug("%s: product.info.get failed: %s", device.product_id, err)
            self._meta_by_product[pid] = {
                "product_name": product_name,
                "category": str(detail.get("category") or ""),
            }
            if detail.get("isOnline") is not None:
                device.online = bool(detail["isOnline"])
            await asyncio.sleep(0.3)  # be gentle with the OEM endpoint
        device.dp_schema = dict(self._schema_by_product[pid])
        device.protocol_version = device.protocol_version or self._version_by_product.get(pid)
        meta = self._meta_by_product.get(pid, {})
        if not device.product_name and meta.get("product_name"):
            device.product_name = meta["product_name"]
        if not device.category and meta.get("category"):
            device.category = meta["category"]
        if not device.status:
            code_by_dp = {dp: spec.code for dp, spec in device.dp_schema.items()}
            for dp, value in (raw.get("dps") or {}).items():
                try:
                    code = code_by_dp.get(int(dp))
                except (TypeError, ValueError):
                    continue
                if code:
                    device.status[code] = value

    async def async_list_homes(self) -> list[Home]:
        """Homes ("locations") of the account."""
        api = self._get_api()
        if not api.sid:
            await api.login()
        return [
            Home(id=str(g["groupId"]), name=str(g.get("name") or g["groupId"]))
            for g in await api.groups()
            if g.get("groupId") is not None
        ]

    def update_known_devices(self, devices: list[CloudDevice]) -> None:
        """Devices to follow on the push channel and to address cloud commands to."""
        self._known = {d.id: d for d in devices}
        if self._channel is not None:
            self._channel.set_devices(self._topic_ids())

    async def async_send_cloud_command(self, device_id: str, commands: list[dict[str, Any]]) -> bool:
        """Cloud fallback: MQTT when the device's frame format is known, else the HTTP API."""
        device = self._known.get(device_id)
        if device is None:
            return False
        by_code = device.codes
        dps = {str(by_code[c["code"]].dp_id): c["value"] for c in commands if c.get("code") in by_code}
        if not dps:
            return False
        if await self._async_publish_mqtt(device, dps):
            return True
        try:
            await self._get_api().publish_dps(device_id, device.gateway_id, dps)
        except ProviderError as err:
            _LOGGER.debug("%s: cloud command failed: %s", device_id, err)
            return False
        return True

    async def _async_publish_mqtt(self, device: CloudDevice, dps: dict[str, Any]) -> bool:
        """Command over the MQTT session: ~100 ms to the device and no HTTP rate limit."""
        channel = self._channel
        version = self._frame_version.get(device.id)
        # Sub-devices need a node address in the frame that is not known yet: HTTP handles them.
        if channel is None or not channel.connected or version is None or device.gateway_id or not device.local_key:
            return False
        self._seq += 1
        frame = encode_command(version, device.local_key, dps, self._seq, self._origin)
        if await channel.async_publish(device.id, frame):
            return True
        _LOGGER.debug("%s: MQTT command not acknowledged; using the HTTP API", device.id)
        return False

    # ------------------------------------------------------------------ push (MQTT)

    def _topic_ids(self) -> set[str]:
        ids = set(self._known)
        ids.update(d.gateway_id for d in self._known.values() if d.gateway_id)
        return ids

    async def async_start_push(self, listener: PushListener) -> bool:
        """Open (or re-scope) the app's MQTT session. Idempotent."""
        async with self._push_lock:
            return await self._async_start_push(listener)

    async def _async_start_push(self, listener: PushListener) -> bool:
        self._push_listener = listener
        if self._channel is not None:
            self._channel.set_devices(self._topic_ids())
            return True
        api = self._get_api()
        try:
            if not api.sid:
                await api.login()
            if not api.ecode:
                # Entries created before the push channel lack the ecode; only a full login returns it.
                await api.login(full=True)
        except ProviderError as err:
            _LOGGER.debug("push channel unavailable: %s", err)
            return False
        if not api.ecode or not api.sid:
            _LOGGER.debug("push channel unavailable: the login result has no ecode")
            return False
        if not self._mqtt_id:
            self._mqtt_id = os.urandom(12).hex()
            if self.on_token_update is not None:
                self.on_token_update({})
        profile = self._resolve_profile()
        self._channel = OemMqttChannel(
            self.hass,
            client_id=mqtt_client_id(profile.package, self._mqtt_id, api.uid),
            on_message=self._on_mqtt_message,
            on_connection=self._on_mqtt_connection,
        )
        self._channel.set_credentials(api.sid, mqtt_password(api.ecode))
        self._channel.set_devices(self._topic_ids())
        # A cached "online" may be days old: only what the channel reports counts from now on.
        # LAN-polled devices report within one poll; the silent ones are checked over HTTP.
        for dev_id in list(self._known):
            self._set_cloud_online(dev_id, False)
        await self._channel.async_start(api.mqtt_host or mqtt_host_for(api.endpoint), api.mqtt_port)
        return True

    async def async_stop_push(self) -> None:
        """Close the MQTT session."""
        self._push_listener = None
        if self._silent_check is not None:
            self._silent_check.cancel()
            self._silent_check = None
        for task in (self._relogin_task, self._silent_task):
            if task is not None and not task.done():
                task.cancel()
        channel, self._channel = self._channel, None
        if channel is not None:
            await channel.async_stop()
        self._cloud_online.clear()

    async def async_close(self) -> None:
        """Release resources."""
        await self.async_stop_push()

    def _on_mqtt_connection(self, connected: bool, rc: int) -> None:
        if connected:
            _LOGGER.debug("%s: MQTT push channel connected", self.name)
            if self._silent_check is not None:
                self._silent_check.cancel()
            self._silent_check = self.hass.loop.call_later(_SILENT_CHECK_DELAY, self._start_silent_check)
            return
        if rc in AUTH_REFUSED:
            # The session id the broker saw is no longer valid (re-login elsewhere, expiry).
            now = time.monotonic()
            if now - self._last_mqtt_relogin >= _MQTT_RELOGIN_INTERVAL and (
                self._relogin_task is None or self._relogin_task.done()
            ):
                self._last_mqtt_relogin = now
                self._relogin_task = self.hass.async_create_task(self._async_mqtt_relogin())
            return
        _LOGGER.debug("%s: MQTT push channel lost (rc=%s)", self.name, rc)
        # Without the channel the cloud view of every device is unknown: let the LAN decide alone.
        for dev_id in [d for d, online in self._cloud_online.items() if online]:
            self._set_cloud_online(dev_id, False)

    def _start_silent_check(self) -> None:
        self._silent_check = None
        if self._silent_task is None or self._silent_task.done():
            self._silent_task = self.hass.async_create_task(self._async_check_silent_devices())

    async def _async_check_silent_devices(self) -> None:
        """Ask the HTTP API whether the devices that never reported on MQTT are online.

        Those are the ones the LAN side cannot reach (a reachable device reports its polled
        state to the cloud too). Without this, such a device would stay unavailable until its
        state changes, since the cached "online" is not trusted after a (re)connect.
        """
        channel = self._channel
        if channel is None or not channel.connected:
            return
        silent = [dev_id for dev_id in self._known if self._cloud_online.get(dev_id) is not True]
        api = self._get_api()
        for dev_id in silent[:_SILENT_CHECK_MAX]:
            try:
                detail = await api.device_get(dev_id)
            except ProviderError as err:
                _LOGGER.debug("%s: online check failed (%s); stopping", dev_id, err)
                return
            online = detail.get("isOnline")
            if isinstance(online, bool) and self._cloud_online.get(dev_id) is not True:
                self._set_cloud_online(dev_id, online)
            await asyncio.sleep(0.3)  # be gentle with the OEM endpoint

    async def _async_mqtt_relogin(self) -> None:
        _LOGGER.debug("%s: MQTT login refused; renewing the API session", self.name)
        try:
            await self._get_api().login()  # `on_session` hands the new sid to the channel
        except ProviderError as err:
            _LOGGER.debug("%s: session renewal for MQTT failed: %s", self.name, err)

    def _on_mqtt_message(self, topic_id: str, payload: bytes) -> None:
        source = self._known.get(topic_id)
        if source is None or not source.local_key:
            return
        decoded = decode_frame(payload, source.local_key)
        if decoded is None:
            _LOGGER.debug("%s: unreadable MQTT frame %s…", topic_id, payload[:3])
            return
        version, message = decoded
        if version in ("2.2", "2.3"):
            self._frame_version[topic_id] = version
        data = message.get("data")
        if not isinstance(data, dict):
            return
        target = self._message_target(source, data)
        if target is None:
            return
        protocol = message.get("protocol")
        if protocol == 4 and isinstance(data.get("dps"), dict):
            self._set_cloud_online(target.id, True)  # no explicit "online" event exists: a report is one
            code_by_dp = {str(dp): spec.code for dp, spec in target.dp_schema.items()}
            values = {code_by_dp[str(dp)]: value for dp, value in data["dps"].items() if str(dp) in code_by_dp}
            if values and self._push_listener is not None:
                self._push_listener.push_device_report(target.id, values)
        elif protocol == 1 and isinstance(data.get("online"), bool):
            self._set_cloud_online(target.id, data["online"])

    def _message_target(self, source: CloudDevice, data: dict[str, Any]) -> CloudDevice | None:
        """Device a message is about: the topic's device, or a child named by ``cid`` / ``devId``."""
        cid = data.get("cid")
        if cid:
            return next(
                (d for d in self._known.values() if d.gateway_id == source.id and d.node_id == str(cid)),
                None,
            )
        dev_id = data.get("devId")
        if dev_id and dev_id != source.id:
            return self._known.get(str(dev_id))
        return source

    def _set_cloud_online(self, device_id: str, online: bool) -> None:
        if self._cloud_online.get(device_id) == online:
            return
        self._cloud_online[device_id] = online
        if self._push_listener is not None:
            self._push_listener.push_device_online(device_id, online)
