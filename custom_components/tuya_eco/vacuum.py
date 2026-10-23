"""Robot vacuum platform (category sd)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.vacuum import StateVacuumEntity, VacuumActivity, VacuumEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import EntityDef
from .platform_setup import async_setup_platform_entities

_STATUS_TO_ACTIVITY = {
    "charge_done": VacuumActivity.DOCKED,
    "chargecompleted": VacuumActivity.DOCKED,
    "chargego": VacuumActivity.DOCKED,
    "charging": VacuumActivity.DOCKED,
    "cleaning": VacuumActivity.CLEANING,
    "goto_pos": VacuumActivity.CLEANING,
    "mop_clean": VacuumActivity.CLEANING,
    "part_clean": VacuumActivity.CLEANING,
    "pick_zone_clean": VacuumActivity.CLEANING,
    "pos_arrived": VacuumActivity.CLEANING,
    "pos_unarrive": VacuumActivity.CLEANING,
    "random": VacuumActivity.CLEANING,
    "smart_clean": VacuumActivity.CLEANING,
    "smart": VacuumActivity.CLEANING,
    "spot_clean": VacuumActivity.CLEANING,
    "wall_clean": VacuumActivity.CLEANING,
    "wall_follow": VacuumActivity.CLEANING,
    "zone_clean": VacuumActivity.CLEANING,
    "docking": VacuumActivity.RETURNING,
    "goto_charge": VacuumActivity.RETURNING,
    "paused": VacuumActivity.PAUSED,
    "sleep": VacuumActivity.IDLE,
    "standby": VacuumActivity.IDLE,
    "error": VacuumActivity.ERROR,
    "fault": VacuumActivity.ERROR,
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up vacuums."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.VACUUM, EcoVacuum)


class EcoVacuum(EcoEntity, StateVacuumEntity):
    """Tuya robot vacuum."""

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        dps = definition.dps
        features = VacuumEntityFeature.STATE
        if "power_go" in dps:
            features |= VacuumEntityFeature.START | VacuumEntityFeature.STOP
        if "pause" in dps:
            features |= VacuumEntityFeature.PAUSE
        if "switch_charge" in dps or "chargego" in definition.meta.get("modes", []):
            features |= VacuumEntityFeature.RETURN_HOME
        if "seek" in dps:
            features |= VacuumEntityFeature.LOCATE
        if "fan_speed" in dps:
            features |= VacuumEntityFeature.FAN_SPEED
            self._attr_fan_speed_list = [str(s) for s in definition.meta.get("fan_speeds", [])]
        self._attr_supported_features = features

    @property
    def activity(self) -> VacuumActivity | None:
        """Activity."""
        status = self.dp_value("status")
        if status is not None and str(status) in _STATUS_TO_ACTIVITY:
            return _STATUS_TO_ACTIVITY[str(status)]
        if self.dp_value("pause") is True:
            return VacuumActivity.PAUSED
        if status is None and self.dp_value("power_go") is True:
            return VacuumActivity.CLEANING
        return None if status is None else VacuumActivity.IDLE

    @property
    def fan_speed(self) -> str | None:
        """Suction."""
        value = self.dp_value("fan_speed")
        return None if value is None else str(value)

    async def async_start(self) -> None:
        """Start."""
        await self.async_write("power_go", True)

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop."""
        await self.async_write("power_go", False)

    async def async_pause(self) -> None:
        """Pause."""
        if "pause" in self.definition.dps:
            await self.async_write("pause", True)
        else:
            await self.async_write("power_go", False)

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Dock."""
        if "switch_charge" in self.definition.dps:
            await self.async_write("switch_charge", True)
        elif "mode" in self.definition.dps:
            await self.async_write("mode", "chargego")

    async def async_locate(self, **kwargs: Any) -> None:
        """Locate."""
        await self.async_write("seek", True)

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Suction."""
        await self.async_write("fan_speed", fan_speed)

    async def async_send_command(
        self, command: str, params: dict[str, Any] | list[Any] | None = None, **kwargs: Any
    ) -> None:
        """Write an arbitrary DP by code: command = DP code, params = [value]."""
        dp_id = self.device.dp_id_for(command)
        if dp_id is None:
            return
        value = params[0] if isinstance(params, list) and params else params
        await self.async_write_dps({dp_id: value})
