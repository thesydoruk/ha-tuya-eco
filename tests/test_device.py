"""Tests of the EcoDevice state machine (connect, push, reconnect, IP change, key refresh)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.tuya_eco.local import device as device_mod
from custom_components.tuya_eco.local.device import EcoDevice
from custom_components.tuya_eco.local.discovery import DiscoveredDevice, TuyaDiscovery
from custom_components.tuya_eco.providers.base import CloudDevice, DpSpec, DpType

from .fake_device import FakeTuyaDevice

pytestmark = pytest.mark.enable_socket

DEVICE_ID = "bf0123456789abcdefghij"
KEY = "0123456789abcdef"


def _info(key: str = KEY) -> CloudDevice:
    return CloudDevice(
        id=DEVICE_ID,
        name="Plug",
        local_key=key,
        category="cz",
        dp_schema={1: DpSpec(1, "switch_1", DpType.BOOLEAN), 2: DpSpec(2, "countdown_1", DpType.INTEGER, {}, False)},
    )


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.05)


@pytest.fixture
async def fake_hass():
    return SimpleNamespace(loop=asyncio.get_running_loop())


@pytest.fixture
def discovery(fake_hass):
    return TuyaDiscovery(fake_hass)  # not started: no UDP sockets in tests


async def _connect_with_port(monkeypatch, port: int) -> None:
    """Point the device layer at the fake server's port."""
    original = device_mod.connect

    async def _patched(host, device_id, local_key, version, listener, **kwargs):
        kwargs["port"] = port
        return await original(host, device_id, local_key, version, listener, **kwargs)

    monkeypatch.setattr(device_mod, "connect", _patched)


async def test_connect_push_and_write(fake_hass, discovery, monkeypatch) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, KEY, 3.3)
    await fake.start()
    await _connect_with_port(monkeypatch, fake.port)
    updates: list[int] = []
    device = EcoDevice(fake_hass, _info(), discovery, host="127.0.0.1", version=3.3, poll_interval=0.5)
    device.async_add_listener(lambda: updates.append(1))
    try:
        await device.async_start()
        await _wait_for(lambda: device.available)
        assert device.dps == {"1": True, "2": 50}
        assert device.version == 3.3

        fake.push({"1": False})
        await _wait_for(lambda: device.dps.get("1") is False)

        await device.async_set_dps({1: True})
        assert device.get_dp(1) is True  # optimistic
        await _wait_for(lambda: fake.dps["1"] is True)
        # periodic poll keeps running
        before = len(fake.received)
        await _wait_for(lambda: len(fake.received) > before + 1, timeout=3)
    finally:
        await device.async_stop()
        await fake.stop()
    assert not device.available


async def test_reconnect_after_drop(fake_hass, discovery, monkeypatch) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, KEY, 3.4)
    await fake.start()
    await _connect_with_port(monkeypatch, fake.port)
    monkeypatch.setattr(device_mod, "BACKOFF_MIN", 0.2)
    device = EcoDevice(fake_hass, _info(), discovery, host="127.0.0.1", version=3.4, poll_interval=5)
    try:
        await device.async_start()
        await _wait_for(lambda: device.available)
        await fake.drop_connections()
        await _wait_for(lambda: not device.available)
        await _wait_for(lambda: device.available, timeout=10)
        assert device.connected
    finally:
        await device.async_stop()
        await fake.stop()


async def test_protocol_autodetect(fake_hass, discovery, monkeypatch) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, KEY, 3.5)
    await fake.start()
    await _connect_with_port(monkeypatch, fake.port)
    monkeypatch.setattr(device_mod, "DEFAULT_VERSION_ORDER", (3.3, 3.4, 3.5))
    device = EcoDevice(fake_hass, _info(), discovery, host="127.0.0.1", version=None, poll_interval=5)
    try:
        await device.async_start()
        await _wait_for(lambda: device.available, timeout=30)
        assert device.version == 3.5
    finally:
        await device.async_stop()
        await fake.stop()


async def test_discovery_updates_host_and_wakes(fake_hass, discovery, monkeypatch) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, KEY, 3.3)
    await fake.start()
    await _connect_with_port(monkeypatch, fake.port)
    monkeypatch.setattr(device_mod, "BACKOFF_MIN", 60)  # would wait a minute without the discovery wake-up
    hosts: list[str] = []
    device = EcoDevice(
        fake_hass,
        _info(),
        discovery,
        host="192.0.2.1",
        version=3.3,
        poll_interval=5,
        on_host_changed=lambda d: hosts.append(d.host),
    )
    try:
        await device.async_start()
        await asyncio.sleep(0.3)
        assert not device.available
        discovery.handle_datagram(b"", ("127.0.0.1", 6667), 6667)  # garbage is ignored
        discovery.devices[DEVICE_ID] = DiscoveredDevice(DEVICE_ID, "127.0.0.1", "3.3", None, 0)
        device._discovered(discovery.devices[DEVICE_ID])
        await _wait_for(lambda: device.available, timeout=10)
        assert hosts == ["127.0.0.1"]
    finally:
        await device.async_stop()
        await fake.stop()


async def test_wrong_key_triggers_refresh_and_new_key_connects(fake_hass, discovery, monkeypatch) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, "new_key_000000000", 3.4)
    fake.real_key = b"newkey0123456789"
    await fake.start()
    await _connect_with_port(monkeypatch, fake.port)
    monkeypatch.setattr(device_mod, "BACKOFF_MIN", 0.1)
    monkeypatch.setattr(device_mod, "DEFAULT_VERSION_ORDER", (3.4,))
    refreshed = asyncio.Event()
    device: EcoDevice | None = None

    async def _key_invalid(dev: EcoDevice) -> None:
        refreshed.set()
        dev.update_info(_info("newkey0123456789"))

    device = EcoDevice(
        fake_hass,
        _info("0123456789abcdef"),
        discovery,
        host="127.0.0.1",
        version=3.4,
        poll_interval=5,
        on_key_invalid=_key_invalid,
    )
    try:
        await device.async_start()
        await asyncio.wait_for(refreshed.wait(), 20)
        await _wait_for(lambda: device.available, timeout=20)
        assert device.info.local_key == "newkey0123456789"
    finally:
        await device.async_stop()
        await fake.stop()
