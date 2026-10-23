"""Tuya IoT Platform provider (cloud project access id / secret, via tinytuya.Cloud).

Works for any account linked to the project ("Link Tuya App Account" in the
IoT platform), i.e. Smart Life, Tuya Smart and OEM apps that can scan the
platform's QR code.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .base import (
    BaseProvider,
    CloudDevice,
    DpSpec,
    LoginKind,
    LoginStep,
    ProviderAuthError,
    ProviderError,
)
from .smartlife import parse_dp_type, parse_values

_LOGGER = logging.getLogger(__name__)

CONF_API_REGION = "api_region"
CONF_API_KEY = "api_key"
CONF_API_SECRET = "api_secret"
CONF_API_DEVICE_ID = "api_device_id"

API_REGIONS = ("eu", "eu-w", "us", "us-e", "cn", "sg", "in")


class TuyaIotProvider(BaseProvider):
    """Tuya IoT Platform cloud project."""

    key = "tuya_iot"
    name = "Tuya IoT Platform (cloud project keys)"
    login_kind = LoginKind.API_KEYS
    login_fields = {
        CONF_API_REGION: "select:" + ",".join(API_REGIONS),
        CONF_API_KEY: "text",
        CONF_API_SECRET: "password",
        CONF_API_DEVICE_ID: "text?",
    }

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        super().__init__(hass)
        self._region = "eu"
        self._api_key = ""
        self._api_secret = ""
        self._device_id = ""
        self._cloud: Any = None

    def _get_cloud(self) -> Any:
        if self._cloud is None:
            import tinytuya  # noqa: PLC0415

            self._cloud = tinytuya.Cloud(
                apiRegion=self._region,
                apiKey=self._api_key,
                apiSecret=self._api_secret,
                apiDeviceID=self._device_id or None,
                initial_token=None,
            )
        return self._cloud

    async def async_start_login(self, user_input: dict[str, Any]) -> LoginStep:
        """Store keys."""
        self._region = str(user_input.get(CONF_API_REGION, "eu"))
        self._api_key = str(user_input[CONF_API_KEY]).strip()
        self._api_secret = str(user_input[CONF_API_SECRET]).strip()
        self._device_id = str(user_input.get(CONF_API_DEVICE_ID) or "").strip()
        self._cloud = None
        return LoginStep(kind=LoginKind.API_KEYS)

    async def async_finish_login(self, state: dict[str, Any]) -> None:
        """Validate the keys by listing devices."""
        await self.async_fetch_devices()

    def to_entry_data(self) -> dict[str, Any]:
        """Serialise."""
        return {
            CONF_API_REGION: self._region,
            CONF_API_KEY: self._api_key,
            CONF_API_SECRET: self._api_secret,
            CONF_API_DEVICE_ID: self._device_id,
        }

    @classmethod
    def from_entry_data(cls, hass: HomeAssistant, data: dict[str, Any]) -> TuyaIotProvider:
        """Restore."""
        provider = cls(hass)
        provider._region = data.get(CONF_API_REGION, "eu")
        provider._api_key = data[CONF_API_KEY]
        provider._api_secret = data[CONF_API_SECRET]
        provider._device_id = data.get(CONF_API_DEVICE_ID, "")
        return provider

    @property
    def account_id(self) -> str:
        """Unique id."""
        return f"tuya_iot:{self._api_key}"

    def _fetch_sync(self) -> list[CloudDevice]:
        cloud = self._get_cloud()
        if getattr(cloud, "error", None):
            err = cloud.error
            raise ProviderAuthError(str(err.get("Payload", err)) if isinstance(err, dict) else str(err))
        result = cloud.getdevices(include_map=True)
        if isinstance(result, dict) and "Error" in result:
            payload = str(result.get("Payload", result["Error"]))
            if "1004" in payload or "sign" in payload.lower() or "1106" in payload or "28841" in payload:
                raise ProviderAuthError(payload)
            raise ProviderError(payload)
        devices: list[CloudDevice] = []
        for raw in result or []:
            if not isinstance(raw, dict) or "id" not in raw:
                continue
            schema: dict[int, DpSpec] = {}
            for dp_key, spec in (raw.get("mapping") or {}).items():
                try:
                    dp_id = int(dp_key)
                except (TypeError, ValueError):
                    continue  # mapping keyed by code: no DP id known
                if not isinstance(spec, dict) or "code" not in spec:
                    continue
                schema[dp_id] = DpSpec(
                    dp_id=dp_id,
                    code=str(spec["code"]),
                    type=parse_dp_type(spec.get("type")),
                    values=parse_values(spec.get("values")),
                    read_only=False,
                )
            devices.append(
                CloudDevice(
                    id=str(raw["id"]),
                    name=str(raw.get("name") or raw["id"]),
                    local_key=str(raw.get("key") or ""),
                    category=str(raw.get("category") or ""),
                    product_id=str(raw.get("product_id") or ""),
                    product_name=str(raw.get("product_name") or ""),
                    model=str(raw.get("model") or ""),
                    icon=str(raw.get("icon") or ""),
                    ip=str(raw["ip"]) if raw.get("ip") else None,
                    online=raw.get("online"),
                    uuid=str(raw.get("uuid") or ""),
                    node_id=str(raw.get("node_id") or ""),
                    gateway_id=str(raw.get("parent") or raw.get("gateway_id") or ""),
                    dp_schema=schema,
                )
            )
        return devices

    async def async_fetch_devices(self) -> list[CloudDevice]:
        """List devices with keys and DP mapping."""
        return await self.hass.async_add_executor_job(self._fetch_sync)
