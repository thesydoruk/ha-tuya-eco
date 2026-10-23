"""Tuya IoT Platform provider (cloud project access id / secret).

Works for any account linked to the project ("Link Tuya App Account" in the
IoT platform), i.e. Smart Life, Tuya Smart and OEM apps that can scan the
platform's QR code. Uses the integration's own OpenAPI client.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .base import BaseProvider, CloudDevice, DpSpec, LoginKind, LoginStep
from .smartlife import parse_dp_type, parse_values
from .tuya_openapi import ENDPOINTS, TuyaOpenApi

_LOGGER = logging.getLogger(__name__)

CONF_API_REGION = "api_region"
CONF_API_KEY = "api_key"
CONF_API_SECRET = "api_secret"

API_REGIONS = tuple(ENDPOINTS)


def build_schema(spec: dict[str, Any]) -> dict[int, DpSpec]:
    """Merge `status` and `functions` of a specifications result into DpSpecs.

    Entries without a `dp_id` cannot be used locally and are skipped.
    """
    schema: dict[int, DpSpec] = {}
    writable = {str(f.get("code")) for f in spec.get("functions") or [] if isinstance(f, dict)}
    for group in (spec.get("status") or [], spec.get("functions") or []):
        for item in group:
            if not isinstance(item, dict) or "code" not in item or item.get("dp_id") is None:
                continue
            try:
                dp_id = int(item["dp_id"])
            except (TypeError, ValueError):
                continue
            if dp_id in schema:
                continue
            code = str(item["code"])
            schema[dp_id] = DpSpec(
                dp_id=dp_id,
                code=code,
                type=parse_dp_type(item.get("type")),
                values=parse_values(item.get("values")),
                read_only=code not in writable,
            )
    return schema


class TuyaIotProvider(BaseProvider):
    """Tuya IoT Platform cloud project."""

    key = "tuya_iot"
    name = "Tuya IoT Platform (cloud project keys)"
    login_kind = LoginKind.API_KEYS
    login_fields = {
        CONF_API_REGION: "select:" + ",".join(API_REGIONS),
        CONF_API_KEY: "text",
        CONF_API_SECRET: "password",
    }

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        super().__init__(hass)
        self._region = "eu"
        self._api_key = ""
        self._api_secret = ""
        self._api: TuyaOpenApi | None = None
        self._schemas: dict[str, dict[int, DpSpec]] = {}  # by product id

    def _get_api(self) -> TuyaOpenApi:
        if self._api is None:
            self._api = TuyaOpenApi(async_get_clientsession(self.hass), self._region, self._api_key, self._api_secret)
        return self._api

    async def async_start_login(self, user_input: dict[str, Any]) -> LoginStep:
        """Store keys."""
        self._region = str(user_input.get(CONF_API_REGION, "eu"))
        self._api_key = str(user_input[CONF_API_KEY]).strip()
        self._api_secret = str(user_input[CONF_API_SECRET]).strip()
        self._api = None
        return LoginStep(kind=LoginKind.API_KEYS)

    async def async_finish_login(self, state: dict[str, Any]) -> None:
        """Validate the keys by listing devices."""
        await self.async_fetch_devices()

    def to_entry_data(self) -> dict[str, Any]:
        """Serialise."""
        return {CONF_API_REGION: self._region, CONF_API_KEY: self._api_key, CONF_API_SECRET: self._api_secret}

    @classmethod
    def from_entry_data(cls, hass: HomeAssistant, data: dict[str, Any]) -> TuyaIotProvider:
        """Restore."""
        provider = cls(hass)
        provider._region = data.get(CONF_API_REGION, "eu")
        provider._api_key = data[CONF_API_KEY]
        provider._api_secret = data[CONF_API_SECRET]
        return provider

    @property
    def account_id(self) -> str:
        """Unique id."""
        return f"tuya_iot:{self._api_key}"

    async def async_fetch_devices(self) -> list[CloudDevice]:
        """List devices with keys and DP schema."""
        api = self._get_api()
        devices: list[CloudDevice] = []
        for raw in await api.list_devices():
            if not isinstance(raw, dict) or "id" not in raw:
                continue
            product_id = str(raw.get("product_id") or "")
            schema = self._schemas.get(product_id)
            if schema is None:
                try:
                    schema = build_schema(await api.specifications(str(raw["id"])))
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("%s: specifications unavailable: %s", raw["id"], err)
                    schema = {}
                if product_id and schema:
                    self._schemas[product_id] = schema
            devices.append(
                CloudDevice(
                    id=str(raw["id"]),
                    name=str(raw.get("name") or raw["id"]),
                    local_key=str(raw.get("local_key") or ""),
                    category=str(raw.get("category") or ""),
                    product_id=product_id,
                    product_name=str(raw.get("product_name") or ""),
                    model=str(raw.get("model") or ""),
                    icon=str(raw.get("icon") or ""),
                    ip=str(raw["ip"]) if raw.get("ip") else None,
                    online=raw.get("online"),
                    uuid=str(raw.get("uuid") or ""),
                    node_id=str(raw.get("node_id") or ""),
                    gateway_id=str(raw.get("gateway_id") or raw.get("owner_id_gateway") or ""),
                    dp_schema=dict(schema),
                    status={
                        s["code"]: s.get("value")
                        for s in raw.get("status") or []
                        if isinstance(s, dict) and "code" in s
                    },
                )
            )
        return devices
