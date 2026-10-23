"""Minimal asynchronous client for the Tuya IoT Platform OpenAPI.

Only what the IoT-platform provider needs: token handling, the signed request
scheme (the post-2021 "v2" signature), the associated-users device list and the
device specifications (DP schema).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

import aiohttp

from .base import ProviderAuthError, ProviderError

_LOGGER = logging.getLogger(__name__)

ENDPOINTS: dict[str, str] = {
    "cn": "openapi.tuyacn.com",
    "us": "openapi.tuyaus.com",
    "us-e": "openapi-ueaz.tuyaus.com",
    "eu": "openapi.tuyaeu.com",
    "eu-w": "openapi-weaz.tuyaeu.com",
    "in": "openapi.tuyain.com",
    "sg": "openapi-sg.iotbing.com",
}

_AUTH_ERROR_CODES = {1004, 1010, 1011, 1013, 1106, 1100, 28841002, 28841105}


class TuyaOpenApi:
    """Signed requests against ``https://openapi.tuya{region}.com``."""

    def __init__(self, session: aiohttp.ClientSession, region: str, client_id: str, secret: str) -> None:
        """Initialise."""
        self._session = session
        self._host = ENDPOINTS.get(region, ENDPOINTS["eu"])
        self._client_id = client_id
        self._secret = secret
        self._token: str | None = None
        self._token_expires = 0.0
        self._time_offset = 0.0

    # ------------------------------------------------------------------ signing

    def _sign(self, method: str, path_with_query: str, body: str, t: str, token: str | None) -> str:
        string_to_sign = "\n".join([method, hashlib.sha256(body.encode()).hexdigest(), "", path_with_query])
        payload = self._client_id + (token or "") + t + string_to_sign
        return hmac.new(self._secret.encode(), payload.encode(), hashlib.sha256).hexdigest().upper()

    async def _request(self, method: str, path: str, query: dict[str, str] | None = None, *, auth: bool = True) -> Any:
        if auth:
            await self._ensure_token()
        q = "&".join(f"{k}={query[k]}" for k in sorted(query)) if query else ""
        path_with_query = f"{path}?{q}" if q else path
        t = str(int((time.time() + self._time_offset) * 1000))
        token = self._token if auth else None
        headers = {
            "client_id": self._client_id,
            "sign": self._sign(method, path_with_query, "", t, token),
            "t": t,
            "sign_method": "HMAC-SHA256",
        }
        if token:
            headers["access_token"] = token
        url = f"https://{self._host}{path_with_query}"
        try:
            async with self._session.request(
                method, url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise ProviderError(f"cloud unreachable: {err}") from err
        except TimeoutError as err:
            raise ProviderError("cloud request timed out") from err
        if not isinstance(data, dict):
            raise ProviderError(f"unexpected response {data!r}")
        if "t" in data and auth is False:
            self._time_offset = data["t"] / 1000 - time.time()
        if not data.get("success"):
            code = data.get("code")
            msg = data.get("msg", "unknown error")
            if code in _AUTH_ERROR_CODES or "sign" in str(msg).lower() or "token" in str(msg).lower():
                self._token = None
                raise ProviderAuthError(f"{msg} ({code})")
            raise ProviderError(f"{msg} ({code})")
        return data.get("result")

    async def _ensure_token(self) -> None:
        if self._token and time.time() < self._token_expires - 60:
            return
        result = await self._request("GET", "/v1.0/token", {"grant_type": "1"}, auth=False)
        if not isinstance(result, dict) or "access_token" not in result:
            raise ProviderAuthError("token request returned no access token")
        self._token = result["access_token"]
        self._token_expires = time.time() + float(result.get("expire_time", 7200))

    # ------------------------------------------------------------------ data

    async def list_devices(self) -> list[dict[str, Any]]:
        """All devices of the users linked to the project (includes `local_key`)."""
        devices: list[dict[str, Any]] = []
        last_row_key: str | None = None
        for _ in range(50):  # hard cap on pages
            query = {"size": "50"}
            if last_row_key:
                query["last_row_key"] = last_row_key
            result = await self._request("GET", "/v1.0/iot-01/associated-users/devices", query)
            if not isinstance(result, dict):
                break
            devices.extend(result.get("devices") or [])
            if not result.get("has_more") or not result.get("last_row_key"):
                break
            last_row_key = str(result["last_row_key"])
        return devices

    async def specifications(self, device_id: str) -> dict[str, Any]:
        """`functions` / `status` lists with code, type, values (and dp_id when the API provides it)."""
        try:
            result = await self._request("GET", f"/v1.1/devices/{device_id}/specifications")
        except ProviderAuthError:
            raise
        except ProviderError as err:
            if "2009" in str(err) or "not support" in str(err):
                return {}
            raise
        return result if isinstance(result, dict) else {}
