"""MAC addresses of LAN devices from the host's ARP / neighbour table.

Tuya clouds rarely tell the MAC of a device and the LAN broadcasts never do, so
the only reliable source is the ARP cache of the machine running Home Assistant.
A device we hold a TCP link to always has a fresh entry there. Linux only
(`/proc/net/arp`); elsewhere the lookup returns nothing.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

PROC_NET_ARP = Path("/proc/net/arp")
_ATF_COM = 0x2  # ARP entry is complete (hardware address resolved)
_ZERO_MAC = "00:00:00:00:00:00"
_HEX12 = re.compile(r"^[0-9a-f]{12}$")


def normalize_mac(value: object) -> str | None:
    """``AA-BB-CC-DD-EE-FF`` / ``aabbccddeeff`` / ``aa:bb:...`` → ``aa:bb:cc:dd:ee:ff``; None if not a MAC."""
    if not isinstance(value, str):
        return None
    digits = re.sub(r"[^0-9a-f]", "", value.lower())
    if not _HEX12.match(digits):
        return None
    mac = ":".join(digits[i : i + 2] for i in range(0, 12, 2))
    return None if mac == _ZERO_MAC else mac


def parse_proc_net_arp(text: str) -> dict[str, str]:
    """``/proc/net/arp`` → {ip: mac} for complete entries only."""
    table: dict[str, str] = {}
    for line in text.splitlines()[1:]:  # first line is the header
        parts = line.split()
        if len(parts) < 4:
            continue
        ip, flags, hw_addr = parts[0], parts[2], parts[3]
        try:
            if not int(flags, 16) & _ATF_COM:
                continue
        except ValueError:
            continue
        mac = normalize_mac(hw_addr)
        if mac:
            table[ip] = mac
    return table


def read_arp_table_sync() -> dict[str, str]:
    """Current {ip: mac} of the host (blocking; run in an executor)."""
    try:
        return parse_proc_net_arp(PROC_NET_ARP.read_text(encoding="ascii", errors="replace"))
    except OSError:
        return {}


async def async_arp_table(hass: HomeAssistant) -> dict[str, str]:
    """Current {ip: mac} of the host."""
    return await hass.async_add_executor_job(read_arp_table_sync)
