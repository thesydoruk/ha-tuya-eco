"""Diagnostics (local keys and tokens are redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICES_CACHE, CONF_PROVIDER_DATA

TO_REDACT = {
    "local_key",
    "token_info",
    "access_token",
    "refresh_token",
    "sid",
    "password",
    "secret",
    "api_secret",
    "api_key",
    "client_id",
    "uid",
    "uuid",
    "user_code",
    "terminal_id",
    "email",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics."""
    hub = entry.runtime_data
    data = dict(entry.data)
    data.pop(CONF_DEVICES_CACHE, None)
    data[CONF_PROVIDER_DATA] = async_redact_data(data.get(CONF_PROVIDER_DATA, {}), TO_REDACT)
    return {
        "entry": async_redact_data(data, TO_REDACT),
        "options": dict(entry.options),
        "provider": hub.provider.key,
        "last_cloud_error": hub.last_cloud_error,
        "discovery": {
            dev_id: {"ip": d.ip, "version": d.version, "age": round(d.age)}
            for dev_id, d in hub.discovery.devices.items()
        },
        "devices": [
            {
                **async_redact_data(device.as_diagnostic_dict(), TO_REDACT),
                "category": device.info.category,
                "product_id": device.info.product_id,
                "entities": [d.as_dict() for d in hub.entity_defs(device)],
            }
            for device in hub.devices.values()
        ],
    }
