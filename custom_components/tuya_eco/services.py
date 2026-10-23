"""Integration-level services."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .const import DOMAIN
from .hub import EcoHub
from .local.device import EcoDevice
from .local.protocol import TuyaLocalError

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_DPS = "set_dps"
SERVICE_REFRESH_CLOUD = "refresh_cloud"
SERVICE_RECONNECT = "reconnect"

ATTR_DEVICE_ID = "device_id"
ATTR_DPS = "dps"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

SET_DPS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_DPS): vol.Schema({cv.string: object}),
    }
)
DEVICE_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})
REFRESH_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})


def _hubs(hass: HomeAssistant) -> list[EcoHub]:
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]


def _find_device(hass: HomeAssistant, ha_device_id: str) -> tuple[EcoHub, EcoDevice]:
    registry = dr.async_get(hass)
    entry = registry.async_get(ha_device_id)
    if entry is None:
        raise ServiceValidationError(f"Unknown device {ha_device_id}")
    tuya_ids = {ident for domain, ident in entry.identifiers if domain == DOMAIN}
    for hub in _hubs(hass):
        for tuya_id in tuya_ids:
            if tuya_id in hub.devices:
                return hub, hub.devices[tuya_id]
    raise ServiceValidationError(f"Device {ha_device_id} is not a Tuya Eco device")


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the services once."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_DPS):
        return

    async def _set_dps(call: ServiceCall) -> None:
        hub, device = _find_device(hass, call.data[ATTR_DEVICE_ID])
        dps: dict[str, Any] = {str(k): v for k, v in call.data[ATTR_DPS].items()}
        try:
            await device.async_set_dps(dps)  # type: ignore[arg-type]
        except TuyaLocalError as err:
            if not await hub.async_send_cloud(device, dps):  # type: ignore[arg-type]
                raise HomeAssistantError(f"{device.name}: {err}") from err

    async def _refresh_cloud(call: ServiceCall) -> None:
        wanted = call.data.get(ATTR_CONFIG_ENTRY_ID)
        for hub in _hubs(hass):
            if wanted and hub.entry.entry_id != wanted:
                continue
            await hub.async_refresh_cloud(force=True)

    async def _reconnect(call: ServiceCall) -> None:
        _, device = _find_device(hass, call.data[ATTR_DEVICE_ID])
        await device.async_reconnect()

    hass.services.async_register(DOMAIN, SERVICE_SET_DPS, _set_dps, schema=SET_DPS_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_CLOUD, _refresh_cloud, schema=REFRESH_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RECONNECT, _reconnect, schema=DEVICE_SCHEMA)
