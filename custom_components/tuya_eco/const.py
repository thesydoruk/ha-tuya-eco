"""Constants for the Tuya Eco integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "tuya_eco"

PLATFORMS: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.EVENT,
    Platform.FAN,
    Platform.HUMIDIFIER,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SIREN,
    Platform.SWITCH,
    Platform.VACUUM,
    Platform.VALVE,
]

# Config entry data keys
CONF_PROVIDER = "provider"
CONF_PROVIDER_DATA = "provider_data"
CONF_DEVICES_CACHE = "devices_cache"
CONF_HOSTS = "hosts"
CONF_VERSIONS = "versions"

# Options
OPT_POLL_INTERVAL = "poll_interval"
OPT_CLOUD_REFRESH_HOURS = "cloud_refresh_hours"
OPT_CLOUD_FALLBACK = "cloud_fallback"
OPT_DISABLED_DEVICES = "disabled_devices"

DEFAULT_POLL_INTERVAL = 30
DEFAULT_CLOUD_REFRESH_HOURS = 12
DEFAULT_CLOUD_FALLBACK = True

# Minimum spacing between cloud refreshes triggered by key failures.
KEY_REFRESH_COOLDOWN = 300

SIGNAL_NEW_DEVICES = f"{DOMAIN}_new_devices"
