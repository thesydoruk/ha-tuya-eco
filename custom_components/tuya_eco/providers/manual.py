"""Manual provider: the user already knows the device id and local key.

Useful for keys read directly in a vendor app (newer Ledvance SMART+ versions
show the local key in the app) or taken from another tool. The DP schema is
detected from the device itself (first status poll) and mapped heuristically.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .base import BaseProvider, CloudDevice, LoginKind, LoginStep

CONF_DEVICES = "devices"
CONF_DEVICE_ID = "device_id"
CONF_LOCAL_KEY = "local_key"
CONF_NAME = "name"
CONF_HOST = "host"
CONF_PROTOCOL_VERSION = "protocol_version"


class ManualProvider(BaseProvider):
    """A static list of devices entered by hand."""

    key = "manual"
    name = "Manual (device id + local key)"
    login_kind = LoginKind.CREDENTIALS
    login_fields = {
        CONF_NAME: "text",
        CONF_DEVICE_ID: "text",
        CONF_LOCAL_KEY: "password",
        CONF_HOST: "text?",
        CONF_PROTOCOL_VERSION: "select:auto,3.1,3.2,3.3,3.4,3.5",
    }

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        super().__init__(hass)
        self._devices: list[dict[str, Any]] = []

    async def async_start_login(self, user_input: dict[str, Any]) -> LoginStep:
        """Add one device."""
        self.add_device(user_input)
        return LoginStep(kind=LoginKind.CREDENTIALS)

    def add_device(self, user_input: dict[str, Any]) -> None:
        """Add or replace a device definition."""
        dev_id = str(user_input[CONF_DEVICE_ID]).strip()
        entry = {
            CONF_DEVICE_ID: dev_id,
            CONF_LOCAL_KEY: str(user_input[CONF_LOCAL_KEY]).strip(),
            CONF_NAME: str(user_input.get(CONF_NAME) or dev_id).strip(),
            CONF_HOST: str(user_input.get(CONF_HOST) or "").strip() or None,
            CONF_PROTOCOL_VERSION: str(user_input.get(CONF_PROTOCOL_VERSION) or "auto"),
        }
        self._devices = [d for d in self._devices if d[CONF_DEVICE_ID] != dev_id] + [entry]

    def remove_device(self, device_id: str) -> None:
        """Remove a device definition."""
        self._devices = [d for d in self._devices if d[CONF_DEVICE_ID] != device_id]

    async def async_finish_login(self, state: dict[str, Any]) -> None:
        """Nothing to validate online."""

    def to_entry_data(self) -> dict[str, Any]:
        """Serialise."""
        return {CONF_DEVICES: list(self._devices)}

    @classmethod
    def from_entry_data(cls, hass: HomeAssistant, data: dict[str, Any]) -> ManualProvider:
        """Restore."""
        provider = cls(hass)
        provider._devices = [dict(d) for d in data.get(CONF_DEVICES, [])]
        return provider

    @property
    def account_id(self) -> str:
        """Unique id: one entry per manually added device."""
        first = self._devices[0][CONF_DEVICE_ID] if self._devices else "empty"
        return f"manual:{first}"

    async def async_fetch_devices(self) -> list[CloudDevice]:
        """Return the configured devices."""
        result = []
        for dev in self._devices:
            version = dev.get(CONF_PROTOCOL_VERSION)
            result.append(
                CloudDevice(
                    id=dev[CONF_DEVICE_ID],
                    name=dev[CONF_NAME],
                    local_key=dev[CONF_LOCAL_KEY],
                    ip=dev.get(CONF_HOST),
                    protocol_version=None if version in (None, "auto") else str(version),
                )
            )
        return result
