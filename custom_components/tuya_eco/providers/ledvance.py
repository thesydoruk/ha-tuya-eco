"""Ledvance SMART+ WiFi provider (and Sylvania Smart WiFi, same code path)."""

from __future__ import annotations

from .oem_profiles import LEDVANCE, SYLVANIA
from .tuya_oem import CONF_EMAIL, CONF_PASSWORD, CONF_REGION, TuyaOemProvider


class LedvanceProvider(TuyaOemProvider):
    """Ledvance SMART+ WiFi app account (e-mail + password)."""

    key = "ledvance"
    name = "Ledvance SMART+ WiFi"
    profile = LEDVANCE
    login_fields = {
        CONF_EMAIL: "text",
        CONF_PASSWORD: "password",
        CONF_REGION: "select:EU,US,CN,IN",
    }


class SylvaniaProvider(TuyaOemProvider):
    """Sylvania Smart WiFi app account (e-mail + password)."""

    key = "sylvania"
    name = "Sylvania Smart WiFi"
    profile = SYLVANIA
    login_fields = {
        CONF_EMAIL: "text",
        CONF_PASSWORD: "password",
        CONF_REGION: "select:US,EU",
    }
