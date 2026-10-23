"""Climate platform (thermostats, heaters, air conditioners)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import EntityDef, IntegerType
from .platform_setup import async_setup_platform_entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up climates."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.CLIMATE, EcoClimate)


class EcoClimate(EcoEntity, ClimateEntity):
    """Generic Tuya climate."""

    _attr_target_temperature_step = 0.5

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        meta = definition.meta
        self._set = IntegerType.from_values(meta.get("temp_set"))
        self._cur = IntegerType.from_values(meta.get("temp_current")) if "temp_current" in definition.dps else None
        self._mode_map: dict[str, str] = dict(meta.get("hvac_modes") or {})  # tuya → hvac
        self._presets: list[str] = [str(p) for p in meta.get("presets", [])]
        self._attr_temperature_unit = (
            UnitOfTemperature.FAHRENHEIT if meta.get("unit") == "°F" else UnitOfTemperature.CELSIUS
        )
        self._attr_min_temp = self._set.min_scaled
        self._attr_max_temp = self._set.max_scaled if self._set.max > self._set.min else 35
        self._attr_target_temperature_step = self._set.step_scaled
        features = ClimateEntityFeature.TARGET_TEMPERATURE
        if "switch" in definition.dps:
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if "fan" in definition.dps:
            features |= ClimateEntityFeature.FAN_MODE
            self._attr_fan_modes = [str(f) for f in meta.get("fan_modes", [])]
        self._humidity_set = (
            IntegerType.from_values(meta.get("humidity_set")) if "humidity_set" in definition.dps else None
        )
        self._humidity_cur = IntegerType.from_values(meta.get("humidity")) if "humidity" in definition.dps else None
        if self._humidity_set is not None:
            features |= ClimateEntityFeature.TARGET_HUMIDITY
            self._attr_min_humidity = int(self._humidity_set.min_scaled)
            self._attr_max_humidity = (
                int(self._humidity_set.max_scaled) if self._humidity_set.max > self._humidity_set.min else 100
            )
        swing_modes: list[str] = []
        if "swing" in definition.dps:
            swing_modes = ["off", "on"]
        if "swing_h" in definition.dps or "swing_v" in definition.dps:
            swing_modes = ["off"]
            if "swing_h" in definition.dps:
                swing_modes.append("horizontal")
            if "swing_v" in definition.dps:
                swing_modes.append("vertical")
            if "swing_h" in definition.dps and "swing_v" in definition.dps:
                swing_modes.append("both")
        if swing_modes:
            features |= ClimateEntityFeature.SWING_MODE
            self._attr_swing_modes = swing_modes
        if self._presets:
            features |= ClimateEntityFeature.PRESET_MODE
            self._attr_preset_modes = self._presets
        self._attr_supported_features = features
        modes: list[HVACMode] = []
        if "switch" in definition.dps:
            modes.append(HVACMode.OFF)
        for hvac in self._mode_map.values():
            try:
                mode = HVACMode(hvac)
            except ValueError:
                continue
            if mode not in modes:
                modes.append(mode)
        self._switch_only = HVACMode(meta.get("switch_only_hvac", "heat"))
        if (not self._mode_map or self._presets) and self._switch_only not in modes:
            modes.append(self._switch_only)
        self._attr_hvac_modes = modes

    @property
    def current_temperature(self) -> float | None:
        """Current."""
        if self._cur is None:
            return None
        return self._cur.scale_value(self.value("temp_current"))

    @property
    def target_temperature(self) -> float | None:
        """Target."""
        return self._set.scale_value(self.value("temp_set"))

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Mode."""
        if "switch" in self.definition.dps:
            sw = self.value("switch")
            if sw is None:
                return None
            if not sw:
                return HVACMode.OFF
        if self._mode_map:
            raw = self.value("mode")
            hvac = self._mode_map.get(str(raw)) if raw is not None else None
            if hvac:
                try:
                    return HVACMode(hvac)
                except ValueError:
                    pass
            return self._switch_only if self._presets else None
        return self._switch_only

    @property
    def hvac_action(self) -> HVACAction | None:
        """Best-effort action."""
        mode = self.hvac_mode
        if mode == HVACMode.OFF:
            return HVACAction.OFF
        if mode == HVACMode.HEAT:
            cur, tgt = self.current_temperature, self.target_temperature
            if cur is not None and tgt is not None:
                return HVACAction.HEATING if cur < tgt else HVACAction.IDLE
            return HVACAction.HEATING
        if mode == HVACMode.COOL:
            cur, tgt = self.current_temperature, self.target_temperature
            if cur is not None and tgt is not None:
                return HVACAction.COOLING if cur > tgt else HVACAction.IDLE
            return HVACAction.COOLING
        return None

    @property
    def current_humidity(self) -> float | None:
        """Measured humidity."""
        return self._humidity_cur.scale_value(self.value("humidity")) if self._humidity_cur else None

    @property
    def target_humidity(self) -> float | None:
        """Target humidity."""
        return self._humidity_set.scale_value(self.value("humidity_set")) if self._humidity_set else None

    @property
    def swing_mode(self) -> str | None:
        """Swing."""
        if "swing" in self.definition.dps:
            value = self.value("swing")
            return None if value is None else ("on" if value else "off")
        h = bool(self.value("swing_h")) if "swing_h" in self.definition.dps else False
        v = bool(self.value("swing_v")) if "swing_v" in self.definition.dps else False
        if h and v:
            return "both"
        if h:
            return "horizontal"
        if v:
            return "vertical"
        return "off"

    async def async_set_humidity(self, humidity: int) -> None:
        """Target humidity."""
        if self._humidity_set is not None:
            await self.async_write("humidity_set", self._humidity_set.scale_back(humidity))

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Swing."""
        if "swing" in self.definition.dps:
            await self.async_write("swing", swing_mode == "on")
            return
        dps: dict[int | str, Any] = {}
        if "swing_h" in self.definition.dps:
            dps[self.dp("swing_h")] = swing_mode in ("horizontal", "both")  # type: ignore[index]
        if "swing_v" in self.definition.dps:
            dps[self.dp("swing_v")] = swing_mode in ("vertical", "both")  # type: ignore[index]
        if dps:
            await self.async_write_dps(dps)

    @property
    def fan_mode(self) -> str | None:
        """Fan."""
        value = self.value("fan")
        return None if value is None else str(value)

    @property
    def preset_mode(self) -> str | None:
        """Preset."""
        value = self.value("preset")
        return None if value is None else str(value)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Target."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        await self.async_write("temp_set", self._set.scale_back(float(temp)))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Mode."""
        dps: dict[int | str, Any] = {}
        if hvac_mode == HVACMode.OFF:
            if "switch" in self.definition.dps:
                dps[self.dp("switch")] = False  # type: ignore[index]
        else:
            if "switch" in self.definition.dps:
                dps[self.dp("switch")] = True  # type: ignore[index]
            for tuya, hvac in self._mode_map.items():
                if hvac == hvac_mode.value:
                    dps[self.dp("mode")] = tuya  # type: ignore[index]
                    break
        if dps:
            await self.async_write_dps(dps)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Fan."""
        await self.async_write("fan", fan_mode)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Preset."""
        await self.async_write("preset", preset_mode)

    async def async_turn_on(self) -> None:
        """On."""
        await self.async_write("switch", True)

    async def async_turn_off(self) -> None:
        """Off."""
        await self.async_write("switch", False)
