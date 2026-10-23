"""Provider abstraction: one adapter per Tuya ecosystem app / cloud.

A provider is the only part of the integration that talks to a vendor cloud.
It is responsible for authentication and for returning `CloudDevice` objects
that carry everything the local engine needs: the `local_key` and the DP schema.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class ProviderError(Exception):
    """Base error for provider operations."""


class ProviderAuthError(ProviderError):
    """Authentication is no longer valid; a re-auth flow is required."""


class ProviderLoginPending(ProviderError):
    """Login not finished yet (e.g. QR code not scanned)."""


class DpType(StrEnum):
    """Tuya data-point value types as reported by the cloud schema."""

    BOOLEAN = "Boolean"
    INTEGER = "Integer"
    ENUM = "Enum"
    STRING = "String"
    JSON = "Json"
    RAW = "Raw"
    BITMAP = "Bitmap"


@dataclass(slots=True)
class DpSpec:
    """Specification of one data point (DP) of a device."""

    dp_id: int
    code: str
    type: DpType
    values: dict[str, Any] = field(default_factory=dict)
    read_only: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Serialise for storage in the config entry."""
        return {
            "dp_id": self.dp_id,
            "code": self.code,
            "type": str(self.type),
            "values": self.values,
            "read_only": self.read_only,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DpSpec:
        """Restore from config-entry storage."""
        try:
            dp_type = DpType(data.get("type", "Raw"))
        except ValueError:
            dp_type = DpType.RAW
        return cls(
            dp_id=int(data["dp_id"]),
            code=str(data["code"]),
            type=dp_type,
            values=dict(data.get("values") or {}),
            read_only=bool(data.get("read_only", False)),
        )


@dataclass(slots=True)
class CloudDevice:
    """Provider-agnostic description of a device returned by a cloud."""

    id: str
    name: str
    local_key: str
    category: str = ""
    product_id: str = ""
    product_name: str = ""
    model: str = ""
    icon: str = ""
    ip: str | None = None
    online: bool | None = None
    uuid: str = ""
    node_id: str = ""  # sub-device id for zigbee/ble children behind a gateway
    gateway_id: str = ""
    dp_schema: dict[int, DpSpec] = field(default_factory=dict)
    status: dict[str, Any] = field(default_factory=dict)
    protocol_version: str | None = None  # hint from provider, if known

    @property
    def codes(self) -> dict[str, DpSpec]:
        """DP schema keyed by code."""
        return {spec.code: spec for spec in self.dp_schema.values()}

    def as_dict(self) -> dict[str, Any]:
        """Serialise for storage in the config entry."""
        return {
            "id": self.id,
            "name": self.name,
            "local_key": self.local_key,
            "category": self.category,
            "product_id": self.product_id,
            "product_name": self.product_name,
            "model": self.model,
            "icon": self.icon,
            "ip": self.ip,
            "online": self.online,
            "uuid": self.uuid,
            "node_id": self.node_id,
            "gateway_id": self.gateway_id,
            "dp_schema": [spec.as_dict() for spec in self.dp_schema.values()],
            "status": self.status,
            "protocol_version": self.protocol_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CloudDevice:
        """Restore from config-entry storage."""
        schema = {}
        for raw in data.get("dp_schema") or []:
            spec = DpSpec.from_dict(raw)
            schema[spec.dp_id] = spec
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or data["id"]),
            local_key=str(data.get("local_key") or ""),
            category=str(data.get("category") or ""),
            product_id=str(data.get("product_id") or ""),
            product_name=str(data.get("product_name") or ""),
            model=str(data.get("model") or ""),
            icon=str(data.get("icon") or ""),
            ip=data.get("ip"),
            online=data.get("online"),
            uuid=str(data.get("uuid") or ""),
            node_id=str(data.get("node_id") or ""),
            gateway_id=str(data.get("gateway_id") or ""),
            dp_schema=schema,
            status=dict(data.get("status") or {}),
            protocol_version=data.get("protocol_version"),
        )


class LoginKind(StrEnum):
    """How the user authenticates with a provider."""

    QR_CODE = "qr_code"
    CREDENTIALS = "credentials"
    API_KEYS = "api_keys"


@dataclass(slots=True)
class LoginStep:
    """Result of starting a login: what the config flow must show next."""

    kind: LoginKind
    # QR_CODE: the text to render as QR; CREDENTIALS/API_KEYS: nothing to show.
    qr_payload: str | None = None
    # Opaque provider state the flow hands back to `async_finish_login`.
    state: dict[str, Any] = field(default_factory=dict)
    description_placeholders: dict[str, str] = field(default_factory=dict)


class BaseProvider(ABC):
    """Abstract cloud adapter.

    Life-cycle:
      1. ``async_start_login(user_input)`` → ``LoginStep`` (may ask the flow to show a QR).
      2. ``async_finish_login(state)``  → raises ``ProviderLoginPending`` while the user has
         not completed the action, ``ProviderAuthError`` on failure, returns ``None`` on
         success. After success ``to_entry_data()`` is persisted.
      3. ``from_entry_data(hass, data)`` restores the provider on HA start.
      4. ``async_fetch_devices()`` is called periodically and on-demand.
    """

    key: str = ""
    name: str = ""
    login_kind: LoginKind = LoginKind.CREDENTIALS
    # Fields the config flow must ask for before `async_start_login` (voluptuous keys
    # are built by the flow from this list; values: "text", "password", "select:<opts>").
    login_fields: dict[str, str] = {}

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the provider."""
        self.hass = hass

    @abstractmethod
    async def async_start_login(self, user_input: dict[str, Any]) -> LoginStep:
        """Begin authentication."""

    @abstractmethod
    async def async_finish_login(self, state: dict[str, Any]) -> None:
        """Complete authentication (poll QR / exchange credentials)."""

    @abstractmethod
    async def async_fetch_devices(self) -> list[CloudDevice]:
        """Return all devices of the account with local keys and DP schema."""

    @abstractmethod
    def to_entry_data(self) -> dict[str, Any]:
        """Serialise the authenticated session for the config entry."""

    @classmethod
    @abstractmethod
    def from_entry_data(cls, hass: HomeAssistant, data: dict[str, Any]) -> BaseProvider:
        """Restore an authenticated provider from config-entry data."""

    @property
    def account_id(self) -> str:
        """A stable identifier for the account (used as config entry unique_id)."""
        return ""

    async def async_send_cloud_command(self, device_id: str, commands: list[dict[str, Any]]) -> bool:
        """Optional cloud fallback for commands (code/value dicts). Default: unsupported."""
        return False

    async def async_close(self) -> None:
        """Release resources."""
