"""Config flow tests (manual provider: no network needed)."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.tuya_eco.const import CONF_PROVIDER, CONF_PROVIDER_DATA, DOMAIN


async def test_manual_provider_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PROVIDER: "manual"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "login"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Plug",
            "device_id": "bf0123456789abcdefghij",
            "local_key": "0123456789abcdef",
            "host": "192.168.1.50",
            "protocol_version": "3.3",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PROVIDER] == "manual"
    devices = result["data"][CONF_PROVIDER_DATA]["devices"]
    assert devices[0]["device_id"] == "bf0123456789abcdefghij"
    assert devices[0]["protocol_version"] == "3.3"

    # the same device cannot be added twice
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PROVIDER: "manual"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Plug", "device_id": "bf0123456789abcdefghij", "local_key": "x", "protocol_version": "auto"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_provider_list_contains_all_ecosystems(hass: HomeAssistant) -> None:
    from custom_components.tuya_eco.providers.registry import provider_choices

    keys = set(provider_choices())
    assert {"smartlife", "ledvance", "sylvania", "tuya_iot", "tuya_oem", "manual"} <= keys
