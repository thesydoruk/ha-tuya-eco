"""Hub tests through a real config entry (manual provider, no network)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_eco.const import CONF_PROVIDER, CONF_PROVIDER_DATA, DOMAIN
from custom_components.tuya_eco.providers.base import CloudDevice

DEV_A = "bf0000000000000000a1"
DEV_B = "bf0000000000000000b2"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"manual:{DEV_A}",
        data={
            CONF_PROVIDER: "manual",
            CONF_PROVIDER_DATA: {
                "devices": [
                    {
                        "device_id": DEV_A,
                        "local_key": "0123456789abcdef",
                        "name": "Plug A",
                        "host": "192.0.2.1",
                        "protocol_version": "3.3",
                    },
                    {
                        "device_id": DEV_B,
                        "local_key": "0123456789abcdef",
                        "name": "Plug B",
                        "host": "192.0.2.2",
                        "protocol_version": "3.3",
                    },
                ]
            },
        },
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    # No UDP sockets and no real TCP attempts in this test.
    with (
        patch("custom_components.tuya_eco.local.discovery.TuyaDiscovery._async_start", AsyncMock()),
        patch("custom_components.tuya_eco.local.discovery.TuyaDiscovery._async_stop", AsyncMock()),
        patch("custom_components.tuya_eco.local.device.EcoDevice.async_start", AsyncMock()),
        patch("custom_components.tuya_eco.local.device.EcoDevice.async_stop", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_devices_removed_from_account_are_removed(hass: HomeAssistant) -> None:
    entry = _entry()
    await _setup(hass, entry)
    hub = entry.runtime_data
    registry = dr.async_get(hass)
    assert set(hub.devices) == {DEV_A, DEV_B}
    assert registry.async_get_device(identifiers={(DOMAIN, DEV_B)}) is not None

    # The account now only lists device A.
    only_a = [CloudDevice(id=DEV_A, name="Plug A", local_key="0123456789abcdef", ip="192.0.2.1")]
    with (
        patch.object(hub.provider, "async_fetch_devices", AsyncMock(return_value=only_a)),
        patch("custom_components.tuya_eco.local.device.EcoDevice.async_stop", AsyncMock()),
    ):
        assert await hub.async_refresh_cloud(force=True)
    await hass.async_block_till_done()

    assert set(hub.devices) == {DEV_A}
    assert DEV_B not in hub.cloud_devices
    assert registry.async_get_device(identifiers={(DOMAIN, DEV_B)}) is None
    assert registry.async_get_device(identifiers={(DOMAIN, DEV_A)}) is not None


async def test_empty_cloud_list_does_not_wipe_devices(hass: HomeAssistant) -> None:
    entry = _entry()
    await _setup(hass, entry)
    hub = entry.runtime_data
    with patch.object(hub.provider, "async_fetch_devices", AsyncMock(return_value=[])):
        assert await hub.async_refresh_cloud(force=True)
    assert set(hub.devices) == {DEV_A, DEV_B}


async def test_remove_device_from_ui(hass: HomeAssistant) -> None:
    from custom_components.tuya_eco import async_remove_config_entry_device

    entry = _entry()
    await _setup(hass, entry)
    hub = entry.runtime_data
    registry = dr.async_get(hass)
    reg_entry = registry.async_get_device(identifiers={(DOMAIN, DEV_B)})
    assert reg_entry is not None
    with patch("custom_components.tuya_eco.local.device.EcoDevice.async_stop", AsyncMock()):
        assert await async_remove_config_entry_device(hass, entry, reg_entry)
    assert DEV_B not in hub.devices


async def test_number_entity_state_does_not_recurse(hass: HomeAssistant) -> None:
    """Regression: EcoEntity.value() shadowed NumberEntity.value and HA recursed in __repr__."""
    from homeassistant.const import Platform

    from custom_components.tuya_eco.mapping import EntityDef
    from custom_components.tuya_eco.number import EcoNumber

    entry = _entry()
    await _setup(hass, entry)
    hub = entry.runtime_data
    device = hub.devices[DEV_A]
    device.dps["9"] = 120
    definition = EntityDef(
        Platform.NUMBER, "countdown_1", {"value": 9}, meta={"min": 0, "max": 86400, "scale": 0, "step": 1}
    )
    entity = EcoNumber(hub, device, definition)
    entity.hass = hass
    entity.entity_id = "number.test_countdown"
    assert entity.native_value == 120
    assert entity.value == 120  # NumberEntity's property must still work
    assert entity.state == 120
    repr(entity)  # must not recurse


async def test_stale_entities_are_purged_on_setup(hass: HomeAssistant) -> None:
    from homeassistant.helpers import entity_registry as er

    from custom_components.tuya_eco.const import CONF_DEVICES_CACHE
    from custom_components.tuya_eco.providers.base import DpSpec, DpType

    entry = _entry()
    # Device A has a known schema (one switch); device B has none yet.
    cached = CloudDevice(
        id=DEV_A,
        name="Plug A",
        local_key="0123456789abcdef",
        category="cz",
        dp_schema={1: DpSpec(1, "switch_1", DpType.BOOLEAN)},
    )
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_DEVICES_CACHE: [cached.as_dict()]})
    registry = er.async_get(hass)
    stale_a = registry.async_get_or_create("sensor", DOMAIN, f"{DEV_A}_old_thing", config_entry=entry)
    keep_a = registry.async_get_or_create("switch", DOMAIN, f"{DEV_A}_switch_1", config_entry=entry)
    keep_b = registry.async_get_or_create("switch", DOMAIN, f"{DEV_B}_whatever", config_entry=entry)
    orphan = registry.async_get_or_create("switch", DOMAIN, "bf0000000000000000zz9_switch_1", config_entry=entry)

    with (
        patch("custom_components.tuya_eco.local.discovery.TuyaDiscovery._async_start", AsyncMock()),
        patch("custom_components.tuya_eco.local.discovery.TuyaDiscovery._async_stop", AsyncMock()),
        patch("custom_components.tuya_eco.local.device.EcoDevice.async_start", AsyncMock()),
        patch("custom_components.tuya_eco.local.device.EcoDevice.async_stop", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert registry.async_get(stale_a.entity_id) is None  # key no longer generated
    assert registry.async_get(orphan.entity_id) is None  # device not in the account
    assert registry.async_get(keep_a.entity_id) is not None  # still generated
    assert registry.async_get(keep_b.entity_id) is not None  # device without schema: untouched


async def test_device_shared_by_two_entries_is_controlled_once(hass: HomeAssistant, caplog) -> None:
    """A device present in two tuya_eco entries is handled by the first one only."""
    first = _entry()  # devices A and B
    second = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"manual:{DEV_B}",
        title="Second account",
        data={
            CONF_PROVIDER: "manual",
            CONF_PROVIDER_DATA: {
                "devices": [
                    {
                        "device_id": DEV_B,
                        "local_key": "0123456789abcdef",
                        "name": "Plug B",
                        "host": "192.0.2.2",
                        "protocol_version": "3.3",
                    },
                    {
                        "device_id": "bf0000000000000000c3",
                        "local_key": "0123456789abcdef",
                        "name": "Plug C",
                        "host": "192.0.2.3",
                        "protocol_version": "3.3",
                    },
                ]
            },
        },
    )
    await _setup(hass, first)
    await _setup(hass, second)
    hub1, hub2 = first.runtime_data, second.runtime_data
    assert set(hub1.devices) == {DEV_A, DEV_B}
    assert set(hub2.devices) == {"bf0000000000000000c3"}
    assert hub2.conflicts == {DEV_B: first.title}
    assert "already controlled by the entry" in caplog.text

    registry = dr.async_get(hass)
    dev_b = registry.async_get_device(identifiers={(DOMAIN, DEV_B)})
    assert dev_b is not None and dev_b.config_entries == {first.entry_id}

    # Once the first entry lets the device go, the second one may take it over.
    with patch("custom_components.tuya_eco.local.device.EcoDevice.async_stop", AsyncMock()):
        await hub1.async_remove_device(DEV_B)
    taken = [CloudDevice(id=DEV_B, name="Plug B", local_key="0123456789abcdef")]
    with (
        patch.object(hub2.provider, "async_fetch_devices", AsyncMock(return_value=taken)),
        patch("custom_components.tuya_eco.local.device.EcoDevice.async_start", AsyncMock()),
        patch("custom_components.tuya_eco.local.device.EcoDevice.async_stop", AsyncMock()),
    ):
        assert await hub2.async_refresh_cloud(force=True)
    await hass.async_block_till_done()
    assert DEV_B in hub2.devices
    assert DEV_B not in hub2.conflicts


async def test_devices_grouped_by_home_subentries(hass: HomeAssistant) -> None:
    pytest.importorskip("homeassistant.config_entries", reason="subentries need HA 2025.3+")
    from homeassistant import config_entries as ce

    if not hasattr(ce, "ConfigSubentry"):
        pytest.skip("subentries need HA 2025.3+")
    from custom_components.tuya_eco.const import CONF_DEVICES_CACHE

    entry = _entry()
    cached = [
        CloudDevice(id=DEV_A, name="Plug A", local_key="0123456789abcdef", home_id="h1", home_name="Kyiv flat"),
        CloudDevice(id=DEV_B, name="Plug B", local_key="0123456789abcdef", home_id="h2", home_name="Dacha"),
    ]
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_DEVICES_CACHE: [d.as_dict() for d in cached]}
    )
    await _setup(hass, entry)

    subs = {sub.unique_id: sub for sub in entry.subentries.values()}
    assert {sid: s.title for sid, s in subs.items()} == {"h1": "Kyiv flat", "h2": "Dacha"}
    assert all(s.subentry_type == "home" for s in subs.values())

    registry = dr.async_get(hass)
    dev_a = registry.async_get_device(identifiers={(DOMAIN, DEV_A)})
    dev_b = registry.async_get_device(identifiers={(DOMAIN, DEV_B)})
    assert dev_a is not None and dev_b is not None

    def _subentries_of(dev):
        if getattr(dev, "config_subentry_id", None) is not None:
            return {dev.config_subentry_id}
        return set(dev.config_entries_subentries.get(entry.entry_id, set()))

    assert _subentries_of(dev_a) == {subs["h1"].subentry_id}
    assert _subentries_of(dev_b) == {subs["h2"].subentry_id}
    hub = entry.runtime_data
    assert hub.subentry_for(hub.devices[DEV_A]) == subs["h1"].subentry_id
