"""MAC lookup from the host's ARP table."""

from __future__ import annotations

from custom_components.tuya_eco.local.arp import normalize_mac, parse_proc_net_arp
from custom_components.tuya_eco.providers.base import CloudDevice

PROC_NET_ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.20     0x1         0x2         D4:A6:51:00:11:22     *        eth0
192.168.1.21     0x1         0x0         00:00:00:00:00:00     *        eth0
192.168.1.22     0x1         0x2         00:00:00:00:00:00     *        eth0
192.168.1.23     0x1         0x6         aa:bb:cc:dd:ee:ff     *        wlan0
garbage line
"""


def test_parse_proc_net_arp_keeps_complete_entries_only() -> None:
    assert parse_proc_net_arp(PROC_NET_ARP) == {
        "192.168.1.20": "d4:a6:51:00:11:22",
        "192.168.1.23": "aa:bb:cc:dd:ee:ff",
    }
    assert parse_proc_net_arp("") == {}


def test_normalize_mac_accepts_common_spellings() -> None:
    assert normalize_mac("D4A651001122") == "d4:a6:51:00:11:22"
    assert normalize_mac("d4-a6-51-00-11-22") == "d4:a6:51:00:11:22"
    assert normalize_mac("d4:a6:51:00:11:22") == "d4:a6:51:00:11:22"
    assert normalize_mac("00:00:00:00:00:00") is None
    assert normalize_mac("") is None
    assert normalize_mac("not a mac") is None
    assert normalize_mac(None) is None
    assert normalize_mac(12345) is None


def test_cloud_device_normalises_mac_and_round_trips() -> None:
    device = CloudDevice(id="d", name="n", local_key="k", mac="D4-A6-51-00-11-22")
    assert device.mac == "d4:a6:51:00:11:22"
    assert CloudDevice.from_dict(device.as_dict()).mac == "d4:a6:51:00:11:22"
    assert CloudDevice.from_dict({"id": "d"}).mac == ""
    assert CloudDevice(id="d", name="n", local_key="k", mac="bogus").mac == ""
