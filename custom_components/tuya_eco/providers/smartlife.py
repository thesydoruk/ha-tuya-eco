"""Smart Life / Tuya Smart provider (QR login through tuya-device-sharing-sdk).

This is the same mechanism the core Home Assistant Tuya integration uses. The
"device sharing" API returns the `local_key` of every device plus the
DP-id ⇄ code strategy, which is everything the LAN engine needs.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant

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
    ProviderLoginPending,
    PushListener,
)

_LOGGER = logging.getLogger(__name__)

# Identifiers of the Home Assistant application registered with Tuya. They are
# public (see homeassistant/components/tuya/const.py).
TUYA_CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
TUYA_SCHEMA = "haauthorize"

CONF_USER_CODE = "user_code"
CONF_TERMINAL_ID = "terminal_id"
CONF_ENDPOINT = "endpoint"
CONF_TOKEN_INFO = "token_info"
CONF_USERNAME = "username"

_DPTYPE_ALIASES = {
    "bool": DpType.BOOLEAN,
    "boolean": DpType.BOOLEAN,
    "value": DpType.INTEGER,
    "integer": DpType.INTEGER,
    "enum": DpType.ENUM,
    "string": DpType.STRING,
    "json": DpType.JSON,
    "raw": DpType.RAW,
    "bitmap": DpType.BITMAP,
}


def parse_dp_type(raw: str | None) -> DpType:
    """Map the various spellings Tuya uses for DP types."""
    if not raw:
        return DpType.RAW
    return _DPTYPE_ALIASES.get(str(raw).lower(), DpType.RAW)


def parse_values(raw: Any) -> dict[str, Any]:
    """Parse the `values` JSON string of a DP specification."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class SmartLifeProvider(BaseProvider):
    """Smart Life / Tuya Smart app account."""

    key = "smartlife"
    name = "Smart Life / Tuya Smart (QR code)"
    login_kind = LoginKind.QR_CODE
    login_fields = {CONF_USER_CODE: "text"}
    supports_homes = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        super().__init__(hass)
        self._user_code = ""
        self._terminal_id = ""
        self._endpoint = ""
        self._token_info: dict[str, Any] = {}
        self._username = ""
        self._manager: Any = None
        self._login_control: Any = None
        self.on_token_update: Callable[[dict[str, Any]], None] | None = None
        self._push_listener: PushListener | None = None
        self._sdk_listener: Any = None

    # ------------------------------------------------------------------ login

    async def async_start_login(self, user_input: dict[str, Any]) -> LoginStep:
        """Request a QR token for the given user code."""
        from tuya_sharing import LoginControl  # noqa: PLC0415

        self._user_code = str(user_input[CONF_USER_CODE]).strip()
        self._login_control = LoginControl()
        response = await self.hass.async_add_executor_job(
            self._login_control.qr_code, TUYA_CLIENT_ID, TUYA_SCHEMA, self._user_code
        )
        if not response.get("success"):
            raise ProviderAuthError(f"{response.get('msg', 'unknown error')} ({response.get('code', '?')})")
        token = response["result"]["qrcode"]
        return LoginStep(
            kind=LoginKind.QR_CODE,
            qr_payload=f"tuyaSmart--qrLogin?token={token}",
            state={"token": token},
        )

    async def async_finish_login(self, state: dict[str, Any]) -> None:
        """Poll the login result after the user scanned the QR code."""
        if self._login_control is None:
            from tuya_sharing import LoginControl  # noqa: PLC0415

            self._login_control = LoginControl()
        ok, info = await self.hass.async_add_executor_job(
            self._login_control.login_result, state["token"], TUYA_CLIENT_ID, self._user_code
        )
        if not ok:
            raise ProviderLoginPending(f"{info.get('msg', 'not scanned yet')} ({info.get('code', '?')})")
        self._token_info = {
            "t": info["t"],
            "uid": info["uid"],
            "expire_time": info["expire_time"],
            "access_token": info["access_token"],
            "refresh_token": info["refresh_token"],
        }
        self._terminal_id = info["terminal_id"]
        self._endpoint = info["endpoint"]
        self._username = info.get("username", "")

    # ------------------------------------------------------------------ storage

    def to_entry_data(self) -> dict[str, Any]:
        """Serialise."""
        return {
            CONF_USER_CODE: self._user_code,
            CONF_TERMINAL_ID: self._terminal_id,
            CONF_ENDPOINT: self._endpoint,
            CONF_TOKEN_INFO: self._token_info,
            CONF_USERNAME: self._username,
        }

    @classmethod
    def from_entry_data(cls, hass: HomeAssistant, data: dict[str, Any]) -> SmartLifeProvider:
        """Restore."""
        provider = cls(hass)
        provider._user_code = data[CONF_USER_CODE]
        provider._terminal_id = data[CONF_TERMINAL_ID]
        provider._endpoint = data[CONF_ENDPOINT]
        provider._token_info = dict(data[CONF_TOKEN_INFO])
        provider._username = data.get(CONF_USERNAME, "")
        return provider

    @property
    def account_id(self) -> str:
        """Unique id: the Tuya uid."""
        return f"smartlife:{self._token_info.get('uid', self._user_code)}"

    @property
    def username(self) -> str:
        """Display name."""
        return self._username or self._user_code

    # ------------------------------------------------------------------ manager

    def _token_updated(self, token_info: dict[str, Any]) -> None:
        # Called from the SDK's thread.
        self._token_info = dict(token_info)
        if self.on_token_update:
            self.hass.loop.call_soon_threadsafe(self.on_token_update, self._token_info)

    def _get_manager(self) -> Any:
        if self._manager is None:
            from tuya_sharing import Manager, SharingTokenListener  # noqa: PLC0415

            provider = self

            class _TokenListener(SharingTokenListener):
                def update_token(self, token_info: dict[str, Any]) -> None:
                    provider._token_updated(token_info)

            self._manager = Manager(
                TUYA_CLIENT_ID,
                self._user_code,
                self._terminal_id,
                self._endpoint,
                self._token_info,
                _TokenListener(),
            )
        return self._manager

    def _fetch_devices_sync(self) -> list[CloudDevice]:
        import requests  # noqa: PLC0415

        manager = self._get_manager()
        try:
            homes = manager.home_repository.query_homes()
            manager.user_homes = homes
            manager.device_map.clear()
            for home in homes:
                if not self.home_selected(home.id):
                    continue
                for device in manager.device_repository.query_devices_by_home(home.id):
                    device.home_id = str(home.id)
                    device.home_name = str(home.name)
                    device.set_up = True  # subscribe its MQTT topic
                    manager.device_map[device.id] = device
        except requests.RequestException as err:
            raise ProviderError(f"cloud unreachable: {err}") from err
        except Exception as err:  # noqa: BLE001 - SDK raises plain Exception
            if "sign invalid" in str(err).lower() or "token" in str(err).lower():
                raise ProviderAuthError(str(err)) from err
            raise ProviderError(str(err)) from err

        devices: list[CloudDevice] = []
        for device in manager.device_map.values():
            try:
                devices.append(self._convert(manager, device))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Skipping device %s (%s): %s", device.id, device.name, err)
        return devices

    def _convert(self, manager: Any, device: Any) -> CloudDevice:
        functions = getattr(device, "function", {}) or {}
        status_range = getattr(device, "status_range", {}) or {}
        dp_schema: dict[int, DpSpec] = {}

        # Primary source: DP id ⇄ status code relation (works for every device,
        # even those the SDK marks as not "support_local").
        relations: list[dict[str, Any]] = []
        try:
            response = manager.customer_api.get(f"/v1.0/m/life/devices/{device.id}/status")
            if response.get("success"):
                relations = response.get("result", {}).get("dpStatusRelationDTOS", []) or []
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("%s: strategy lookup failed: %s", device.id, err)
        if not relations:
            for dp_id, strategy in (getattr(device, "local_strategy", {}) or {}).items():
                item = strategy.get("config_item", {})
                relations.append(
                    {
                        "dpId": dp_id,
                        "statusCode": strategy.get("status_code"),
                        "valueType": item.get("valueType"),
                        "valueDesc": item.get("valueDesc"),
                    }
                )

        for rel in relations:
            code = rel.get("statusCode")
            dp_id = rel.get("dpId")
            if code is None or dp_id is None:
                continue
            spec_src = functions.get(code) or status_range.get(code)
            raw_type = rel.get("valueType") or (getattr(spec_src, "type", None) if spec_src else None)
            values = parse_values(rel.get("valueDesc")) or parse_values(
                getattr(spec_src, "values", None) if spec_src else None
            )
            dp_schema[int(dp_id)] = DpSpec(
                dp_id=int(dp_id),
                code=str(code),
                type=parse_dp_type(raw_type),
                values=values,
                read_only=code not in functions,
            )

        return CloudDevice(
            id=device.id,
            name=device.name,
            local_key=getattr(device, "local_key", "") or "",
            category=getattr(device, "category", "") or "",
            product_id=getattr(device, "product_id", "") or "",
            product_name=getattr(device, "product_name", "") or "",
            model=getattr(device, "model", "") or "",
            icon=getattr(device, "icon", "") or "",
            ip=getattr(device, "ip", None) or None,
            online=getattr(device, "online", None),
            uuid=getattr(device, "uuid", "") or "",
            node_id=getattr(device, "node_id", "") or getattr(device, "nodeId", "") or "",
            gateway_id=getattr(device, "gateway_id", "") or getattr(device, "gatewayId", "") or "",
            home_id=getattr(device, "home_id", "") or "",
            home_name=getattr(device, "home_name", "") or "",
            dp_schema=dp_schema,
            status=dict(getattr(device, "status", {}) or {}),
        )

    async def async_fetch_devices(self) -> list[CloudDevice]:
        """Return the devices of the selected homes with local keys."""
        return await self.hass.async_add_executor_job(self._fetch_devices_sync)

    def _list_homes_sync(self) -> list[Home]:
        import requests  # noqa: PLC0415

        try:
            homes = self._get_manager().home_repository.query_homes()
        except requests.RequestException as err:
            raise ProviderError(f"cloud unreachable: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise ProviderError(str(err)) from err
        return [Home(id=str(h.id), name=str(h.name)) for h in homes]

    async def async_list_homes(self) -> list[Home]:
        """Homes of the account."""
        return await self.hass.async_add_executor_job(self._list_homes_sync)

    async def async_send_cloud_command(self, device_id: str, commands: list[dict[str, Any]]) -> bool:
        """Cloud fallback."""
        manager = self._get_manager()
        try:
            await self.hass.async_add_executor_job(manager.send_commands, device_id, commands)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("%s: cloud command failed: %s", device_id, err)
            return False
        return True

    # ------------------------------------------------------------------ push (MQTT)

    def _dispatch(self, func: Callable[..., None], *args: Any) -> None:
        """Hand an SDK thread callback to the event loop."""
        self.hass.loop.call_soon_threadsafe(func, *args)

    def _get_sdk_listener(self) -> Any:
        if self._sdk_listener is None:
            from tuya_sharing import SharingDeviceListener  # noqa: PLC0415

            provider = self

            class _Listener(SharingDeviceListener):
                def update_device(
                    self, device: Any, updated_status_properties: Any = None, dp_timestamps: Any = None
                ) -> None:
                    target = provider._push_listener
                    if target is None:
                        return
                    if updated_status_properties:
                        status = getattr(device, "status", {}) or {}
                        values = {code: status.get(code) for code in updated_status_properties}
                        provider._dispatch(target.push_device_report, device.id, values)
                    else:
                        provider._dispatch(target.push_device_renamed, device.id, str(getattr(device, "name", "")))

                def add_device(self, device: Any) -> None:
                    if provider._push_listener is not None:
                        provider._dispatch(provider._push_listener.push_device_added, device.id)

                def remove_device(self, device_id: str) -> None:
                    if provider._push_listener is not None:
                        provider._dispatch(provider._push_listener.push_device_removed, device_id)

            self._sdk_listener = _Listener()
        return self._sdk_listener

    def _start_push_sync(self) -> bool:
        manager = self._get_manager()
        if not manager.device_map:
            return False
        if self._get_sdk_listener() not in manager.device_listeners:
            manager.add_device_listener(self._get_sdk_listener())
        manager.refresh_mq()
        return manager.mq is not None

    async def async_start_push(self, listener: PushListener) -> bool:
        """(Re)connect the MQTT channel of the device-sharing API."""
        self._push_listener = listener
        try:
            return await self.hass.async_add_executor_job(self._start_push_sync)
        except Exception as err:  # noqa: BLE001 - push is an optimisation, never fatal
            _LOGGER.debug("push channel unavailable: %s", err)
            return False

    def _stop_push_sync(self) -> None:
        manager = self._manager
        if manager is not None and manager.mq is not None:
            manager.mq.stop()
            manager.mq = None

    async def async_stop_push(self) -> None:
        """Disconnect the MQTT channel."""
        self._push_listener = None
        try:
            await self.hass.async_add_executor_job(self._stop_push_sync)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("push channel stop failed: %s", err)

    async def async_close(self) -> None:
        """Release."""
        await self.async_stop_push()
        self._manager = None
