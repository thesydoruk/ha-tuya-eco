"""Home selection: config flow step, options and filtering."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.tuya_eco.const import CONF_PROVIDER, DOMAIN, OPT_HOMES
from custom_components.tuya_eco.providers.base import BaseProvider, CloudDevice, Home, LoginKind, LoginStep

HOMES = [Home("100", "Kyiv flat"), Home("200", "Dacha")]
DEVICES = {
    "100": [CloudDevice(id="bf00000000000000000a1", name="Lamp", local_key="k")],
    "200": [CloudDevice(id="bf00000000000000000b2", name="Pump", local_key="k")],
}


class FakeHomesProvider(BaseProvider):
    key = "fake_homes"
    name = "Fake (homes)"
    login_kind = LoginKind.CREDENTIALS
    login_fields: dict[str, str] = {}
    supports_homes = True

    async def async_start_login(self, user_input: dict[str, Any]) -> LoginStep:
        return LoginStep(kind=LoginKind.CREDENTIALS)

    async def async_finish_login(self, state: dict[str, Any]) -> None:
        return None

    async def async_list_homes(self) -> list[Home]:
        return list(HOMES)

    async def async_fetch_devices(self) -> list[CloudDevice]:
        out: list[CloudDevice] = []
        for home_id, devs in DEVICES.items():
            if self.home_selected(home_id):
                out.extend(devs)
        return out

    def to_entry_data(self) -> dict[str, Any]:
        return {}

    @classmethod
    def from_entry_data(cls, hass: HomeAssistant, data: dict[str, Any]) -> FakeHomesProvider:
        return cls(hass)

    @property
    def account_id(self) -> str:
        return "fake:1"


def _patch_registry():
    return (
        patch("custom_components.tuya_eco.config_flow.get_provider_class", return_value=FakeHomesProvider),
        patch("custom_components.tuya_eco.config_flow.provider_choices", return_value={"fake_homes": "Fake (homes)"}),
    )


async def test_flow_offers_homes_and_stores_selection(hass: HomeAssistant) -> None:
    p1, p2 = _patch_registry()
    with p1, p2:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PROVIDER: "fake_homes"})
        assert result["step_id"] == "login"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "homes"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {OPT_HOMES: ["200"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {OPT_HOMES: ["200"]}


async def test_selected_homes_filter_devices() -> None:
    provider = FakeHomesProvider(None)  # type: ignore[arg-type]
    assert {d.name for d in await provider.async_fetch_devices()} == {"Lamp", "Pump"}
    provider.selected_homes = {"200"}
    assert {d.name for d in await provider.async_fetch_devices()} == {"Pump"}
    provider.selected_homes = None
    assert len(await provider.async_fetch_devices()) == 2
