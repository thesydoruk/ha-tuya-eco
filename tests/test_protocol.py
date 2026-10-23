"""End-to-end tests of the LAN protocol against the fake device."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.tuya_eco.local.protocol import (
    TuyaDeviceError,
    TuyaKeyError,
    TuyaListener,
    TuyaTimeoutError,
    connect,
)

from .fake_device import FakeTuyaDevice

pytestmark = pytest.mark.enable_socket

DEVICE_ID = "bf0123456789abcdefghij"
KEY = "0123456789abcdef"


class Recorder(TuyaListener):
    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.disconnects = 0
        self.event = asyncio.Event()

    def status_updated(self, dps: dict) -> None:
        self.updates.append(dps)
        self.event.set()

    def disconnected(self, exc: Exception | None) -> None:
        self.disconnects += 1
        self.event.set()


@pytest.mark.parametrize("version", [3.1, 3.3, 3.4, 3.5])
async def test_status_and_control(version: float) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, KEY, version)
    await fake.start()
    rec = Recorder()
    try:
        link = await connect("127.0.0.1", DEVICE_ID, KEY, version, rec, port=fake.port)
        assert link.session_ready
        dps = await link.status()
        assert dps == {"1": True, "2": 50}

        await link.set_dps({"1": False})
        # the fake pushes a STATUS after a control
        await asyncio.wait_for(rec.event.wait(), 2)
        assert rec.updates and rec.updates[-1]["1"] is False
        assert fake.dps["1"] is False

        await link.heartbeat()
        await link.close()
    finally:
        await fake.stop()


@pytest.mark.parametrize("version", [3.4, 3.5])
async def test_wrong_key_fails_handshake(version: float) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, KEY, version)
    await fake.start()
    try:
        with pytest.raises(TuyaKeyError):
            await connect("127.0.0.1", DEVICE_ID, "ffffffffffffffff", version, Recorder(), port=fake.port)
    finally:
        await fake.stop()


async def test_wrong_key_33_decode_error() -> None:
    fake = FakeTuyaDevice(DEVICE_ID, KEY, 3.3)
    await fake.start()
    try:
        link = await connect("127.0.0.1", DEVICE_ID, "ffffffffffffffff", 3.3, Recorder(), port=fake.port)
        with pytest.raises((TuyaKeyError, TuyaTimeoutError, TuyaDeviceError)):
            await link.status()
        await link.close()
    finally:
        await fake.stop()


async def test_disconnect_notifies_listener() -> None:
    fake = FakeTuyaDevice(DEVICE_ID, KEY, 3.3)
    await fake.start()
    rec = Recorder()
    try:
        link = await connect("127.0.0.1", DEVICE_ID, KEY, 3.3, rec, port=fake.port)
        await link.status()
        rec.event.clear()
        await fake.drop_connections()
        await asyncio.wait_for(rec.event.wait(), 2)
        assert rec.disconnects == 1
        assert not link.connected
    finally:
        await fake.stop()


async def test_timeout_when_device_silent() -> None:
    fake = FakeTuyaDevice(DEVICE_ID, KEY, 3.3)
    fake.answer_dp_query = False
    await fake.start()
    try:
        link = await connect("127.0.0.1", DEVICE_ID, KEY, 3.3, Recorder(), port=fake.port)
        with pytest.raises(TuyaDeviceError):  # retcode 1 + empty payload
            await link.status()
        await link.close()
    finally:
        await fake.stop()


@pytest.mark.parametrize("version", [3.3, 3.4])
async def test_gateway_sub_device(version: float) -> None:
    """A Zigbee child is addressed through the gateway's link with its node id (cid)."""
    gateway = FakeTuyaDevice(DEVICE_ID, KEY, version)
    gateway.children["a4c138aabbccddee"] = {"1": False, "2": 20}
    await gateway.start()
    rec = Recorder()
    other = Recorder()
    try:
        child = await connect("127.0.0.1", DEVICE_ID, KEY, version, rec, port=gateway.port, node_id="a4c138aabbccddee")
        assert await child.status() == {"1": False, "2": 20}
        await child.set_dps({"1": True})
        await asyncio.wait_for(rec.event.wait(), 2)
        assert gateway.children["a4c138aabbccddee"]["1"] is True
        assert rec.updates[-1] == {"1": True}

        # the gateway itself on a separate link ignores pushes that carry a cid
        gw = await connect("127.0.0.1", DEVICE_ID, KEY, version, other, port=gateway.port)
        assert await gw.status() == gateway.dps
        gateway.push({"2": 99}, cid="a4c138aabbccddee")
        await asyncio.sleep(0.3)
        assert other.updates == []
        await child.close()
        await gw.close()
    finally:
        await gateway.stop()


@pytest.mark.parametrize("version", [3.3, 3.4])
async def test_device_with_global_sequence_numbers(version: float) -> None:
    """Regression: an RGB strip (3.4) answered with its own seqno counter and status timed out."""
    fake = FakeTuyaDevice(DEVICE_ID, KEY, version)
    fake.global_seqno = True
    await fake.start()
    rec = Recorder()
    try:
        link = await connect("127.0.0.1", DEVICE_ID, KEY, version, rec, port=fake.port)
        assert await link.status() == {"1": True, "2": 50}
        await link.set_dps({"1": False})
        await asyncio.wait_for(rec.event.wait(), 2)
        assert fake.dps["1"] is False
        await link.heartbeat()
        await link.close()
    finally:
        await fake.stop()
