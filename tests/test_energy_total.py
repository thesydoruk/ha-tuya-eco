"""`add_ele` reports the energy used since the previous report: the sensor sums the pushes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from homeassistant.const import Platform

from custom_components.tuya_eco import sensor as sensor_module
from custom_components.tuya_eco.mapping import build_entities
from custom_components.tuya_eco.providers.base import CloudDevice, DpSpec, DpType
from custom_components.tuya_eco.sensor import EcoEnergyTotalSensor

ADD_ELE = {"min": 0, "max": 50000, "scale": 3, "step": 100, "unit": "kwh"}


def _plug() -> CloudDevice:
    specs = [(1, "switch_1", DpType.BOOLEAN, {}, False), (17, "add_ele", DpType.INTEGER, ADD_ELE, True)]
    schema = {dp: DpSpec(dp_id=dp, code=code, type=t, values=v, read_only=ro) for dp, code, t, v, ro in specs}
    return CloudDevice(id="dev1", name="Plug", local_key="k", category="cz", product_id="", dp_schema=schema)


class _Device:
    def __init__(self, dps: dict[str, Any]) -> None:
        self.id = "dev1"
        self.name = "Plug"
        self.dps = dps
        self.push_counts: dict[str, int] = {}
        self.available = True
        self.info = SimpleNamespace(name="Plug", product_name="", product_id="", category="cz")
        self.listeners: list = []

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: None

    def get_dp(self, dp_id: int) -> Any:
        return self.dps.get(str(dp_id))

    def push(self, value: int) -> None:
        self.dps["17"] = value
        self.push_counts["17"] = self.push_counts.get("17", 0) + 1
        self.notify()

    def poll(self, value: int) -> None:
        self.dps["17"] = value
        self.notify()

    def notify(self) -> None:
        for listener in self.listeners:
            listener()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _sensor(dps: dict[str, Any], restored: float | None = None, clock: _Clock | None = None):
    definition = next(d for d in build_entities(_plug()) if d.platform == Platform.SENSOR and d.key == "add_ele")
    device = _Device(dps)
    hub = SimpleNamespace(provider=SimpleNamespace(name="Smart Life"), cloud_fallback=False)
    entity = EcoEnergyTotalSensor(hub, device, definition)
    last = None if restored is None else SimpleNamespace(native_value=restored)

    async def _last():
        return last

    entity.async_get_last_sensor_data = _last  # type: ignore[method-assign]
    entity.async_write_ha_state = lambda: None  # type: ignore[method-assign]
    with patch.object(sensor_module.time, "monotonic", clock or _Clock()):
        await entity.async_added_to_hass()
    return entity, device


def test_add_ele_is_mapped_as_an_accumulating_energy_sensor() -> None:
    definition = next(d for d in build_entities(_plug()) if d.key == "add_ele")
    assert definition.meta["accumulate"] is True
    assert definition.state_class == "total_increasing"
    assert definition.unit == "kWh"


async def test_pushed_reports_are_summed_and_polls_ignored() -> None:
    clock = _Clock()
    entity, device = await _sensor({"17": 194}, restored=10.0, clock=clock)
    assert entity.native_value == 10.0
    with patch.object(sensor_module.time, "monotonic", clock):
        clock.now += 600
        device.push(241)
        device.poll(241)  # every poll repeats the last report
        clock.now += 600
        device.push(238)
        clock.now += 600
        device.push(238)  # an identical report ten minutes later is real consumption
        device.push(0)  # idle interval
    assert entity.native_value == 10.717
    assert entity.extra_state_attributes == {"last_report": 0.238}


async def test_replay_after_reconnect_is_not_counted_twice() -> None:
    clock = _Clock()
    entity, device = await _sensor({"17": 241}, restored=1.0, clock=clock)
    with patch.object(sensor_module.time, "monotonic", clock):
        clock.now += 3600
        device.available = False
        device.notify()
        clock.now += 60
        device.available = True
        device.poll(241)  # status on connect
        clock.now += 1
        device.push(241)  # the device replays its last report
        clock.now += 600
        device.push(241)  # a genuine report of the same size later on
    assert entity.native_value == 1.241


async def test_starts_from_zero_without_history() -> None:
    clock = _Clock()
    entity, device = await _sensor({}, clock=clock)
    with patch.object(sensor_module.time, "monotonic", clock):
        clock.now += 600
        device.push(50)
    assert entity.native_value == 0.05
