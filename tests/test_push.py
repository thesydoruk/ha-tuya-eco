"""Cloud push events: SmartLife MQTT listener mapping and hub handling."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.tuya_eco.const import DOMAIN
from custom_components.tuya_eco.providers.base import PushListener
from custom_components.tuya_eco.providers.smartlife import SmartLifeProvider

from .test_hub import DEV_A, DEV_B, _entry, _setup


class Recorder(PushListener):
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def push_device_added(self, device_id: str) -> None:
        self.events.append(("added", device_id))

    def push_device_removed(self, device_id: str) -> None:
        self.events.append(("removed", device_id))

    def push_device_renamed(self, device_id: str, name: str) -> None:
        self.events.append(("renamed", (device_id, name)))

    def push_device_report(self, device_id: str, values: dict[str, Any]) -> None:
        self.events.append(("report", (device_id, values)))


async def test_smartlife_listener_maps_sdk_callbacks_to_the_loop() -> None:
    hass = SimpleNamespace(loop=asyncio.get_running_loop())
    provider = SmartLifeProvider(hass)  # type: ignore[arg-type]
    rec = Recorder()
    provider._push_listener = rec
    listener = provider._get_sdk_listener()

    device = SimpleNamespace(id=DEV_A, name="Plug A", status={"switch_1": True, "cur_power": 12})
    # called from the MQTT thread in real life; here from a worker thread
    await asyncio.to_thread(listener.update_device, device, ["switch_1", "cur_power"], None)
    await asyncio.to_thread(listener.update_device, device, None, None)
    await asyncio.to_thread(listener.add_device, SimpleNamespace(id=DEV_B))
    await asyncio.to_thread(listener.remove_device, DEV_B)
    await asyncio.sleep(0.05)

    assert rec.events == [
        ("report", (DEV_A, {"switch_1": True, "cur_power": 12})),
        ("renamed", (DEV_A, "Plug A")),
        ("added", DEV_B),
        ("removed", DEV_B),
    ]


async def test_hub_handles_push_events(hass: HomeAssistant) -> None:
    from custom_components.tuya_eco.const import CONF_DEVICES_CACHE
    from custom_components.tuya_eco.providers.base import CloudDevice, DpSpec, DpType

    entry = _entry()
    cached = CloudDevice(
        id=DEV_A,
        name="Plug A",
        local_key="0123456789abcdef",
        category="cz",
        dp_schema={1: DpSpec(1, "switch_1", DpType.BOOLEAN), 19: DpSpec(19, "cur_power", DpType.INTEGER)},
    )
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_DEVICES_CACHE: [cached.as_dict()]})
    await _setup(hass, entry)
    hub = entry.runtime_data
    device = hub.devices[DEV_A]

    # report while the LAN link is down → state applied, device stays unavailable
    hub.push_device_report(DEV_A, {"switch_1": False, "cur_power": 230, "unknown_code": 1})
    assert device.dps["1"] is False and device.dps["19"] == 230
    assert "unknown_code" not in device.dps and not device.available

    # rename follows the app unless the user renamed the device in HA
    hub.push_device_renamed(DEV_A, "Kettle plug")
    registry = dr.async_get(hass)
    reg = registry.async_get_device(identifiers={(DOMAIN, DEV_A)})
    assert reg is not None and reg.name == "Kettle plug"
    assert hub.cloud_devices[DEV_A].name == "Kettle plug"

    # added → forced cloud refresh; removed → device gone
    with patch.object(hub, "async_refresh_cloud", AsyncMock(return_value=True)) as refresh:
        hub.push_device_added("bf0000000000000000zz9")
        await hass.async_block_till_done()
        refresh.assert_awaited_once_with(force=True)
    with patch("custom_components.tuya_eco.local.device.EcoDevice.async_stop", AsyncMock()):
        hub.push_device_removed(DEV_B)
        await hass.async_block_till_done()
    assert DEV_B not in hub.devices
