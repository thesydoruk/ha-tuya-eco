"""Alarm control panel platform (alarm hubs: mal, wg2)."""

from __future__ import annotations

import base64

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import EntityDef
from .platform_setup import async_setup_platform_entities

_MODE_TO_STATE = {
    "disarmed": AlarmControlPanelState.DISARMED,
    "arm": AlarmControlPanelState.ARMED_AWAY,
    "home": AlarmControlPanelState.ARMED_HOME,
    "sos": AlarmControlPanelState.TRIGGERED,
}
_ACTIONS = {"arm_home": "home", "arm_away": "arm", "disarm": "disarmed", "trigger": "sos"}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up alarm panels."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.ALARM_CONTROL_PANEL, EcoAlarm)


class EcoAlarm(EcoEntity, AlarmControlPanelEntity):
    """`master_mode` enum with an optional `master_state` override."""

    _attr_code_arm_required = False

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        options = {str(o) for o in definition.meta.get("options", [])}
        features = AlarmControlPanelEntityFeature(0)
        if "home" in options:
            features |= AlarmControlPanelEntityFeature.ARM_HOME
        if "arm" in options:
            features |= AlarmControlPanelEntityFeature.ARM_AWAY
        if "sos" in options:
            features |= AlarmControlPanelEntityFeature.TRIGGER
        self._attr_supported_features = features

    def _message(self) -> str | None:
        raw = self.value("message")
        if raw is None:
            return None
        try:
            return base64.b64decode(str(raw)).decode("utf-16be", "replace")
        except (ValueError, TypeError):
            return str(raw)

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """State."""
        if self.value("state") == "alarm":
            msg = self._message() or ""
            if "Sensor Low Battery" not in msg:
                return AlarmControlPanelState.TRIGGERED
        mode = self.value("mode")
        return None if mode is None else _MODE_TO_STATE.get(str(mode))

    @property
    def changed_by(self) -> str | None:
        """Last alarm message while alarming."""
        return self._message() if self.value("state") == "alarm" else None

    async def _async_set(self, action: str) -> None:
        await self.async_write("mode", _ACTIONS[action])

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm."""
        await self._async_set("disarm")

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm home."""
        await self._async_set("arm_home")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm away."""
        await self._async_set("arm_away")

    async def async_alarm_trigger(self, code: str | None = None) -> None:
        """Trigger."""
        await self._async_set("trigger")
