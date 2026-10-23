"""Knowledge tables: Tuya DP codes → entity semantics.

These tables encode what the Tuya ecosystem means by a DP code, independent of a
particular product (the knowledge comes from Tuya's standard instruction set and
the core Home Assistant integration). They are consulted after the composite
builders (light / cover / climate / fan / humidifier / vacuum / alarm) have
claimed their DPs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfLength,
    UnitOfMass,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)


@dataclass(slots=True)
class SensorRule:
    """How to expose an integer/enum/string DP as a sensor."""

    device_class: str | None = None
    state_class: str | None = SensorStateClass.MEASUREMENT
    unit: str | None = None
    entity_category: str | None = None
    icon: str | None = None
    # Force a scale when the cloud spec lacks one (power often reports in 0.1 W).
    default_scale: int | None = None
    enabled_default: bool = True
    name: str | None = None


@dataclass(slots=True)
class BinaryRule:
    """How to expose a boolean/enum/bitmap DP as a binary sensor."""

    device_class: str | None = None
    # For enum DPs: values that mean "on".
    on_values: tuple[str, ...] = ()
    entity_category: str | None = None
    name: str | None = None


@dataclass(slots=True)
class SwitchRule:
    """How to expose a boolean DP as a switch."""

    device_class: str | None = None
    entity_category: str | None = None
    icon: str | None = None
    name: str | None = None


@dataclass(slots=True)
class NumberRule:
    """How to expose a writable integer DP as a number."""

    device_class: str | None = None
    unit: str | None = None
    entity_category: str | None = EntityCategory.CONFIG
    icon: str | None = None
    name: str | None = None


@dataclass(slots=True)
class SelectRule:
    """How to expose a writable enum DP as a select."""

    entity_category: str | None = EntityCategory.CONFIG
    icon: str | None = None
    name: str | None = None
    options: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ButtonRule:
    """How to expose a write-only boolean DP as a button."""

    device_class: str | None = None
    entity_category: str | None = EntityCategory.CONFIG
    icon: str | None = None
    name: str | None = None
    enabled_default: bool = True


_T = UnitOfTemperature
_P = PERCENTAGE
_DIAG = EntityCategory.DIAGNOSTIC
_CFG = EntityCategory.CONFIG
# Literal units: the named constants were renamed across HA versions (UnitOfDensity / UnitOfRatio).
_UG = "μg/m³"
_PPM = "ppm"
_M = SensorStateClass.MEASUREMENT
_TI = SensorStateClass.TOTAL_INCREASING


def _temp(unit: str = _T.CELSIUS, cat: str | None = None) -> SensorRule:
    return SensorRule(SensorDeviceClass.TEMPERATURE, _M, unit, cat)


def _hum() -> SensorRule:
    return SensorRule(SensorDeviceClass.HUMIDITY, _M, _P)


def _energy(scale: int | None = None) -> SensorRule:
    return SensorRule(SensorDeviceClass.ENERGY, _TI, UnitOfEnergy.KILO_WATT_HOUR, default_scale=scale)


def _power(scale: int | None = 1) -> SensorRule:
    return SensorRule(SensorDeviceClass.POWER, _M, UnitOfPower.WATT, default_scale=scale)


def _battery() -> SensorRule:
    return SensorRule(SensorDeviceClass.BATTERY, _M, _P, _DIAG)


SENSORS: dict[str, SensorRule] = {
    # temperature / humidity
    "temp_current": _temp(),
    "temp_current_f": _temp(_T.FAHRENHEIT),
    "temp_current_2": _temp(),
    "temp_current_external": _temp(),
    "temp_current_external_1": _temp(),
    "temp_current_external_2": _temp(),
    "temp_current_external_3": _temp(),
    "va_temperature": _temp(),
    "temperature": _temp(),
    "temp": _temp(),
    "temp_indoor": _temp(),
    "ext_temp": _temp(),
    "sensor_temperature": _temp(),
    "dew_point_temp": _temp(),
    "feellike_temp": _temp(),
    "heat_index": _temp(),
    "windchill_index": _temp(),
    "temp_around": _temp(),
    "temp_coiler": _temp(),
    "temp_effluent": _temp(),
    "temp_venting": _temp(),
    "humidity_value": _hum(),
    "va_humidity": _hum(),
    "humidity": _hum(),
    "humidity_indoor": _hum(),
    "humidity_current": _hum(),
    "humidity_outdoor": _hum(),
    "sensor_humidity": _hum(),
    # electricity
    "cur_current": SensorRule(SensorDeviceClass.CURRENT, _M, UnitOfElectricCurrent.MILLIAMPERE, _DIAG),
    "cur_current1": SensorRule(SensorDeviceClass.CURRENT, _M, UnitOfElectricCurrent.MILLIAMPERE, _DIAG),
    "cur_current2": SensorRule(SensorDeviceClass.CURRENT, _M, UnitOfElectricCurrent.MILLIAMPERE, _DIAG),
    "cur_power": _power(),
    "cur_power1": _power(),
    "cur_power2": _power(),
    "cur_voltage": SensorRule(SensorDeviceClass.VOLTAGE, _M, UnitOfElectricPotential.VOLT, _DIAG, default_scale=1),
    "cur_voltage1": SensorRule(SensorDeviceClass.VOLTAGE, _M, UnitOfElectricPotential.VOLT, _DIAG, default_scale=1),
    "cur_voltage2": SensorRule(SensorDeviceClass.VOLTAGE, _M, UnitOfElectricPotential.VOLT, _DIAG, default_scale=1),
    "power": _power(0),
    "work_power": _power(0),
    "power_total": _power(0),
    "total_power": _power(0),
    "pv_power_total": _power(0),
    "pv_power_channel_1": _power(0),
    "pv_power_channel_2": _power(0),
    "battery_power": _power(0),
    "inverter_output_power": _power(0),
    "add_ele": _energy(3),
    "pro_add_ele": _energy(3),
    "total_forward_energy": _energy(2),
    "forward_energy_total": _energy(2),
    "reverse_energy_total": _energy(2),
    "total_energy1": _energy(2),
    "total_energy2": _energy(2),
    "today_acc_energy1": _energy(2),
    "today_acc_energy2": _energy(2),
    "all_energy": _energy(2),
    "charge_energy_once": _energy(2),
    "cumulative_energy_generated_pv": _energy(2),
    "cumulative_energy_output_inv": _energy(2),
    "cumulative_energy_discharged": _energy(2),
    "cumulative_energy_charged": _energy(2),
    "supply_frequency": SensorRule(SensorDeviceClass.FREQUENCY, _M, UnitOfFrequency.HERTZ, _DIAG),
    # battery
    "battery_percentage": _battery(),
    "battery_value": _battery(),
    "battery": _battery(),
    "va_battery": _battery(),
    "residual_electricity": _battery(),
    "electricity_left": _battery(),
    "wireless_electricity": _battery(),
    "current_soc": _battery(),
    "battery_state": SensorRule(SensorDeviceClass.ENUM, None, entity_category=_DIAG),
    # air quality
    "co2_value": SensorRule(SensorDeviceClass.CO2, _M, _PPM),
    "eco2": SensorRule(SensorDeviceClass.CO2, _M, _PPM),
    "co_value": SensorRule(SensorDeviceClass.CO, _M, _PPM),
    "pm25_value": SensorRule(SensorDeviceClass.PM25, _M, _UG),
    "pm25": SensorRule(SensorDeviceClass.PM25, _M, _UG),
    "pm1": SensorRule(SensorDeviceClass.PM1, _M, _UG),
    "pm10": SensorRule(SensorDeviceClass.PM10, _M, _UG),
    "voc_value": SensorRule(SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS, _M, _PPM),
    "tvoc": SensorRule(SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS, _M, _PPM),
    "ch2o_value": SensorRule(None, _M, _PPM, icon="mdi:molecule"),
    "ch4_sensor_value": SensorRule(None, _M, icon="mdi:molecule"),
    "gas_sensor_value": SensorRule(None, _M, icon="mdi:molecule"),
    "smoke_sensor_value": SensorRule(None, _M, entity_category=_DIAG, icon="mdi:smoke"),
    "air_quality_index": SensorRule(SensorDeviceClass.AQI, _M),
    "air_quality": SensorRule(SensorDeviceClass.ENUM, None),
    # light / weather
    "bright_value_sensor": SensorRule(SensorDeviceClass.ILLUMINANCE, _M, LIGHT_LUX),
    "illuminance_value": SensorRule(SensorDeviceClass.ILLUMINANCE, _M, LIGHT_LUX),
    "movement_state": SensorRule(SensorDeviceClass.ENUM, None, icon="mdi:cursor-move"),
    "target_dis_closest": SensorRule(
        SensorDeviceClass.DISTANCE,
        _M,
        UnitOfLength.CENTIMETERS,
        icon="mdi:signal-distance-variant",
        name="Target distance",
    ),
    "bright_state": SensorRule(SensorDeviceClass.ENUM, None),
    "atmospheric_pressture": SensorRule(SensorDeviceClass.PRESSURE, _M, UnitOfPressure.HPA),
    "pressure_value": SensorRule(SensorDeviceClass.PRESSURE, _M),
    "windspeed_avg": SensorRule(SensorDeviceClass.WIND_SPEED, _M),
    "wind_speed": SensorRule(SensorDeviceClass.WIND_SPEED, _M),
    "wind_direct": SensorRule(SensorDeviceClass.ENUM, None, icon="mdi:compass"),
    "rain_24h": SensorRule(SensorDeviceClass.PRECIPITATION, _TI),
    "rain_rate": SensorRule(SensorDeviceClass.PRECIPITATION_INTENSITY, _M),
    "uv_index": SensorRule(None, _M, "UV index", icon="mdi:sun-wireless"),
    # water / liquids
    "liquid_depth": SensorRule(SensorDeviceClass.DISTANCE, _M, UnitOfLength.CENTIMETERS),
    "liquid_level_percent": SensorRule(None, _M, _P, icon="mdi:water-percent"),
    "liquid_state": SensorRule(SensorDeviceClass.ENUM, None),
    "water_once": SensorRule(SensorDeviceClass.WATER, _TI, UnitOfVolume.LITERS),
    "water_total": SensorRule(SensorDeviceClass.WATER, _TI, UnitOfVolume.LITERS),
    "water_level": SensorRule(SensorDeviceClass.ENUM, None, icon="mdi:water-percent"),
    "tds_in": SensorRule(None, _M, "ppm", icon="mdi:water-opacity"),
    "ph_current": SensorRule(SensorDeviceClass.PH, _M),
    "orp_current": SensorRule(None, _M, "mV", icon="mdi:flash"),
    "ec_current": SensorRule(SensorDeviceClass.CONDUCTIVITY, _M),
    # durations / counters
    "countdown_1": SensorRule(SensorDeviceClass.DURATION, None, UnitOfTime.SECONDS, _DIAG),
    "countdown": SensorRule(SensorDeviceClass.DURATION, None, UnitOfTime.SECONDS, _DIAG),
    "remain_time": SensorRule(SensorDeviceClass.DURATION, None, UnitOfTime.MINUTES),
    "time_total": SensorRule(SensorDeviceClass.DURATION, _TI, UnitOfTime.SECONDS, _DIAG),
    "total_time": SensorRule(SensorDeviceClass.DURATION, _TI, UnitOfTime.MINUTES, _DIAG),
    "time_use": SensorRule(SensorDeviceClass.DURATION, _TI, UnitOfTime.SECONDS, _DIAG),
    "use_time_one": SensorRule(SensorDeviceClass.DURATION, None, UnitOfTime.SECONDS, _DIAG),
    "uv_runtime": SensorRule(SensorDeviceClass.DURATION, _M, UnitOfTime.SECONDS, enabled_default=False),
    "pump_time": SensorRule(SensorDeviceClass.DURATION, _M, UnitOfTime.SECONDS),
    "filter_duration": SensorRule(SensorDeviceClass.DURATION, _M, UnitOfTime.MINUTES),
    "water_time": SensorRule(SensorDeviceClass.DURATION, _M, UnitOfTime.SECONDS, enabled_default=False),
    "excretion_time_day": SensorRule(SensorDeviceClass.DURATION, _M, UnitOfTime.SECONDS),
    "excretion_times_day": SensorRule(None, _M, icon="mdi:counter"),
    "total_pm": SensorRule(None, _TI, entity_category=_DIAG, icon="mdi:air-filter"),
    # vacuum / cleaning
    "clean_area": SensorRule(SensorDeviceClass.AREA, _M, "m²"),
    "clean_time": SensorRule(SensorDeviceClass.DURATION, _M, UnitOfTime.MINUTES),
    "total_clean_area": SensorRule(SensorDeviceClass.AREA, _TI, "m²"),
    "total_clean_time": SensorRule(SensorDeviceClass.DURATION, _TI, UnitOfTime.MINUTES),
    "total_clean_count": SensorRule(None, _TI, icon="mdi:counter"),
    "duster_cloth": SensorRule(None, _M, _P, icon="mdi:broom"),
    "edge_brush": SensorRule(None, _M, _P, icon="mdi:broom"),
    "roll_brush": SensorRule(None, _M, _P, icon="mdi:broom"),
    "filter_life": SensorRule(None, _M, _P, _DIAG, icon="mdi:air-filter"),
    "filter": SensorRule(None, _M, _P, _DIAG, icon="mdi:air-filter"),
    # misc
    "cat_weight": SensorRule(SensorDeviceClass.WEIGHT, _M, UnitOfMass.GRAMS),
    "speed_current": SensorRule(None, _M, icon="mdi:speedometer"),
    "compressor_strength": SensorRule(None, _M, _P),
    "feed_report": SensorRule(None, _M, icon="mdi:bowl"),
    "level_current": SensorRule(SensorDeviceClass.ENUM, None, entity_category=_DIAG),
    "work_state": SensorRule(SensorDeviceClass.ENUM, None, entity_category=_DIAG),
    "work_state_e": SensorRule(SensorDeviceClass.ENUM, None, entity_category=_DIAG),
    "status": SensorRule(SensorDeviceClass.ENUM, None, entity_category=_DIAG),
    "device_state1": SensorRule(SensorDeviceClass.ENUM, None, entity_category=_DIAG),
    "device_state2": SensorRule(SensorDeviceClass.ENUM, None, entity_category=_DIAG),
    "signal_strength": SensorRule(SensorDeviceClass.SIGNAL_STRENGTH, _M, "dBm", _DIAG),
    # factory calibration coefficients of energy plugs: keep them, but out of the way
    "voltage_coe": SensorRule(None, None, entity_category=_DIAG, enabled_default=False),
    "electric_coe": SensorRule(None, None, entity_category=_DIAG, enabled_default=False),
    "power_coe": SensorRule(None, None, entity_category=_DIAG, enabled_default=False),
    "electricity_coe": SensorRule(None, None, entity_category=_DIAG, enabled_default=False),
}

_ALARM = ("1", "alarm")

BINARY_SENSORS: dict[str, BinaryRule] = {
    "doorcontact_state": BinaryRule(BinarySensorDeviceClass.DOOR),
    "doorcontact_state_2": BinaryRule(BinarySensorDeviceClass.DOOR),
    "doorcontact_state_3": BinaryRule(BinarySensorDeviceClass.DOOR),
    "door_state": BinaryRule(BinarySensorDeviceClass.DOOR),
    "closed_opened_kit": BinaryRule(BinarySensorDeviceClass.LOCK, ("AQAB",)),
    "pir": BinaryRule(BinarySensorDeviceClass.MOTION, ("pir",)),
    "presence_state": BinaryRule(
        BinarySensorDeviceClass.OCCUPANCY, ("presence", "small_move", "large_move", "peaceful")
    ),
    "watersensor_state": BinaryRule(BinarySensorDeviceClass.MOISTURE, _ALARM),
    "gas_sensor_state": BinaryRule(BinarySensorDeviceClass.GAS, _ALARM),
    "gas_sensor_status": BinaryRule(BinarySensorDeviceClass.GAS, _ALARM),
    "ch4_sensor_state": BinaryRule(BinarySensorDeviceClass.GAS, _ALARM),
    "smoke_sensor_state": BinaryRule(BinarySensorDeviceClass.SMOKE, _ALARM),
    "smoke_sensor_status": BinaryRule(BinarySensorDeviceClass.SMOKE, _ALARM),
    "co_state": BinaryRule(BinarySensorDeviceClass.SAFETY, _ALARM),
    "co_status": BinaryRule(BinarySensorDeviceClass.SAFETY, _ALARM),
    "co2_state": BinaryRule(BinarySensorDeviceClass.SAFETY, _ALARM),
    "ch2o_state": BinaryRule(BinarySensorDeviceClass.SAFETY, _ALARM),
    "voc_state": BinaryRule(BinarySensorDeviceClass.SAFETY, _ALARM),
    "pm25_state": BinaryRule(BinarySensorDeviceClass.SAFETY, _ALARM),
    "pressure_state": BinaryRule(None, _ALARM),
    "sos_state": BinaryRule(BinarySensorDeviceClass.SAFETY),
    "temper_alarm": BinaryRule(BinarySensorDeviceClass.TAMPER, entity_category=_DIAG),
    "fault": BinaryRule(BinarySensorDeviceClass.PROBLEM, entity_category=_DIAG),
    "feed_state": BinaryRule(None, ("feeding",)),
    "wet": BinaryRule(BinarySensorDeviceClass.MOISTURE),
    "overcurrent_alarm": BinaryRule(BinarySensorDeviceClass.PROBLEM, entity_category=_DIAG),
    "overvoltage_alarm": BinaryRule(BinarySensorDeviceClass.PROBLEM, entity_category=_DIAG),
    "charge_state": BinaryRule(BinarySensorDeviceClass.BATTERY_CHARGING, entity_category=_DIAG),
    "tankfull": BinaryRule(BinarySensorDeviceClass.PROBLEM),
    "defrost": BinaryRule(None),
    "window_state": BinaryRule(BinarySensorDeviceClass.WINDOW, ("opened",)),
    "valve_state": BinaryRule(BinarySensorDeviceClass.OPENING, ("open",)),
    "master_state": BinaryRule(BinarySensorDeviceClass.PROBLEM, ("alarm",), _DIAG),
    "shock_state": BinaryRule(BinarySensorDeviceClass.VIBRATION, ("vibration", "drop", "tilt")),
    "online": BinaryRule(BinarySensorDeviceClass.CONNECTIVITY, entity_category=_DIAG),
}

SWITCHES: dict[str, SwitchRule] = {
    "switch": SwitchRule(SwitchDeviceClass.OUTLET),
    **{f"switch_{i}": SwitchRule(SwitchDeviceClass.OUTLET) for i in range(1, 23)},
    **{f"switch_usb{i}": SwitchRule(SwitchDeviceClass.OUTLET, icon="mdi:usb-port") for i in range(1, 7)},
    "child_lock": SwitchRule(None, _CFG, "mdi:account-lock"),
    "lock": SwitchRule(None, _CFG, "mdi:account-lock", "Child lock"),
    "switch_backlight": SwitchRule(None, _CFG, "mdi:led-off"),
    "switch_sound": SwitchRule(None, _CFG, "mdi:volume-high"),
    "switch_music": SwitchRule(None, _CFG, "mdi:music"),
    "switch_voice": SwitchRule(None, _CFG, "mdi:account-voice"),
    "voice_switch": SwitchRule(None, _CFG, "mdi:account-voice"),
    "switch_kb_sound": SwitchRule(None, _CFG, "mdi:volume-high"),
    "snooze": SwitchRule(None, _CFG, "mdi:alarm-snooze"),
    "anion": SwitchRule(None, _CFG, "mdi:atom"),
    "uv": SwitchRule(None, _CFG, "mdi:lightbulb"),
    "wet": SwitchRule(None, _CFG, "mdi:water-percent"),
    "humidifier": SwitchRule(None, _CFG, "mdi:air-humidifier"),
    "oxygen": SwitchRule(None, _CFG, "mdi:molecule"),
    "fan_cool": SwitchRule(None, _CFG, "mdi:snowflake"),
    "fan_beep": SwitchRule(None, _CFG, "mdi:volume-high"),
    "filter_reset": SwitchRule(None, _CFG, "mdi:air-filter"),
    "overcharge_switch": SwitchRule(None, _CFG, "mdi:battery-charging-high", "Overcharge protection"),
    "pump_reset": SwitchRule(None, _CFG, "mdi:water-pump"),
    "water_reset": SwitchRule(None, _CFG, "mdi:water-sync"),
    "switch_spray": SwitchRule(None, icon="mdi:spray"),
    "switch_disturb": SwitchRule(None, _CFG, "mdi:bell-off"),
    "do_not_disturb": SwitchRule(None, _CFG, "mdi:minus-circle"),
    "remote_switch": SwitchRule(None, _CFG, "mdi:remote", "Remote control"),
    "rf_pairing": SwitchRule(None, _CFG, "mdi:remote", "RF pairing"),
    "buzzer": SwitchRule(None, _CFG, "mdi:volume-high"),
    "adaptive_test": SwitchRule(None, _CFG, "mdi:test-tube"),
    "small_move_self_test": SwitchRule(None, _CFG, "mdi:test-tube", "Small motion self-test"),
    "breath_self_test": SwitchRule(None, _CFG, "mdi:test-tube", "Breathing self-test"),
    "breath_false_detection": SwitchRule(None, _CFG, "mdi:lungs", "Breathing false detection"),
    "sleep": SwitchRule(None, _CFG, "mdi:power-sleep"),
    "sterilization": SwitchRule(None, _CFG, "mdi:bacteria"),
    "frost": SwitchRule(None, _CFG, "mdi:snowflake"),
    "anti_frost": SwitchRule(None, _CFG, "mdi:snowflake"),
    "window_check": SwitchRule(None, _CFG, "mdi:window-open"),
    "eco": SwitchRule(None, _CFG, "mdi:leaf"),
    "switch_save_energy": SwitchRule(None, _CFG, "mdi:leaf"),
    "relay_status": SwitchRule(None, _CFG),
    "start": SwitchRule(None, icon="mdi:play"),
    "warm": SwitchRule(None, _CFG, "mdi:heat-wave"),
    "preheat": SwitchRule(None, icon="mdi:heat-wave"),
    "preheat_1": SwitchRule(None, icon="mdi:heat-wave"),
    "preheat_2": SwitchRule(None, icon="mdi:heat-wave"),
    "pump_switch": SwitchRule(None, icon="mdi:water-pump"),
    "pump": SwitchRule(None, icon="mdi:water-pump"),
    "water_switch": SwitchRule(None, icon="mdi:water"),
    "water": SwitchRule(None, icon="mdi:water"),
    "disinfection": SwitchRule(None, icon="mdi:bacteria"),
    "slow_feed": SwitchRule(None, _CFG, "mdi:speedometer-slow"),
    "auto_clean": SwitchRule(None, _CFG, "mdi:broom"),
    "muffling": SwitchRule(None, _CFG, "mdi:volume-off"),
    "switch_alarm_sound": SwitchRule(None, _CFG, "mdi:alarm-bell"),
    "switch_alarm_light": SwitchRule(None, _CFG, "mdi:alarm-light"),
    "switch_alarm_propel": SwitchRule(None, _CFG, "mdi:bell"),
    "control_back": SwitchRule(None, _CFG, "mdi:swap-vertical", "Reverse direction"),
    "opposite": SwitchRule(None, _CFG, "mdi:swap-vertical"),
    "ipc_work_mode": SwitchRule(None, _CFG),
    "feedin_power_limit_enable": SwitchRule(None, _CFG, "mdi:transmission-tower-export"),
    "wireless_batterylock": SwitchRule(None, _CFG, "mdi:battery-lock"),
    "cry_detection_switch": SwitchRule(None, _CFG, "mdi:emoticon-cry"),
    "decibel_switch": SwitchRule(None, _CFG, "mdi:microphone"),
    "record_switch": SwitchRule(None, _CFG, "mdi:record-rec"),
    "motion_record": SwitchRule(None, _CFG, "mdi:motion-sensor"),
    "basic_private": SwitchRule(None, _CFG, "mdi:eye-off"),
    "basic_flip": SwitchRule(None, _CFG, "mdi:flip-horizontal"),
    "basic_osd": SwitchRule(None, _CFG, "mdi:watermark"),
    "basic_wdr": SwitchRule(None, _CFG, "mdi:watermark"),
    "motion_tracking": SwitchRule(None, _CFG, "mdi:motion-sensor"),
    "motion_switch": SwitchRule(None, _CFG, "mdi:motion-sensor"),
    "motion_area_switch": SwitchRule(None, _CFG, "mdi:motion-sensor"),
    "ipc_auto_siren": SwitchRule(None, _CFG, "mdi:alarm-light"),
}

NUMBERS: dict[str, NumberRule] = {
    **{
        f"countdown{s}": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer")
        for s in ["", *[f"_{i}" for i in range(1, 9)], "_usb1"]
    },
    "temp_set_f": NumberRule(NumberDeviceClass.TEMPERATURE, _T.FAHRENHEIT, None),
    "temp_boiling_c": NumberRule(NumberDeviceClass.TEMPERATURE, _T.CELSIUS),
    "temp_boiling_f": NumberRule(NumberDeviceClass.TEMPERATURE, _T.FAHRENHEIT),
    "upper_temp": NumberRule(NumberDeviceClass.TEMPERATURE, _T.CELSIUS),
    "lower_temp": NumberRule(NumberDeviceClass.TEMPERATURE, _T.CELSIUS),
    "temp_correction": NumberRule(NumberDeviceClass.TEMPERATURE, _T.CELSIUS, icon="mdi:thermometer-plus"),
    "temp": NumberRule(NumberDeviceClass.TEMPERATURE, _T.CELSIUS, None),
    "cook_temperature": NumberRule(NumberDeviceClass.TEMPERATURE, _T.CELSIUS),
    "cook_temperature_2": NumberRule(NumberDeviceClass.TEMPERATURE, _T.CELSIUS),
    "cook_time": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.MINUTES),
    "warm_time": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.MINUTES),
    "sensitivity": NumberRule(None, None, icon="mdi:tune"),
    "volume_set": NumberRule(None, None, icon="mdi:volume-high"),
    "basic_device_volume": NumberRule(None, None, icon="mdi:volume-high"),
    "ipc_bright": NumberRule(None, None, icon="mdi:brightness-6"),
    "ipc_contrast": NumberRule(None, None, icon="mdi:contrast"),
    "ipc_sharp": NumberRule(None, None, icon="mdi:image-filter-center-focus"),
    "alarm_time": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer"),
    "alarm_delay_time": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer"),
    "delay_set": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer"),
    "delay_clean_time": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer"),
    "click_sustain_time": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer"),
    "far_detection": NumberRule(NumberDeviceClass.DISTANCE, UnitOfLength.CENTIMETERS, icon="mdi:signal-distance"),
    "near_detection": NumberRule(NumberDeviceClass.DISTANCE, UnitOfLength.CENTIMETERS, icon="mdi:signal-distance"),
    # mmWave presence sensors (ZG-205W and relatives)
    "move_sensitivity": NumberRule(None, None, icon="mdi:motion-sensor", name="Motion sensitivity"),
    "move_distance": NumberRule(
        NumberDeviceClass.DISTANCE,
        UnitOfLength.CENTIMETERS,
        icon="mdi:signal-distance",
        name="Motion detection distance",
    ),
    "static_distance": NumberRule(
        NumberDeviceClass.DISTANCE,
        UnitOfLength.CENTIMETERS,
        icon="mdi:signal-distance",
        name="Stationary detection distance",
    ),
    "static_sensitivity": NumberRule(None, None, icon="mdi:motion-sensor", name="Stationary sensitivity"),
    "small_move_sensitivity": NumberRule(None, None, icon="mdi:motion-sensor", name="Small motion sensitivity"),
    "small_move_distance": NumberRule(
        NumberDeviceClass.DISTANCE, UnitOfLength.CENTIMETERS, icon="mdi:signal-distance", name="Small motion distance"
    ),
    "move_min_distance": NumberRule(
        NumberDeviceClass.DISTANCE, UnitOfLength.CENTIMETERS, icon="mdi:signal-distance", name="Motion minimum distance"
    ),
    "small_move_min_distance": NumberRule(
        NumberDeviceClass.DISTANCE,
        UnitOfLength.CENTIMETERS,
        icon="mdi:signal-distance",
        name="Small motion minimum distance",
    ),
    "breath_min_distance": NumberRule(
        NumberDeviceClass.DISTANCE,
        UnitOfLength.CENTIMETERS,
        icon="mdi:signal-distance",
        name="Breathing minimum distance",
    ),
    "none_delay_time": NumberRule(
        NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer-sand", name="Exit delay"
    ),
    "alarm_duration": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.MINUTES, icon="mdi:timer"),
    "adaptive_time": NumberRule(
        NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer", name="Adaptive test time"
    ),
    "move_false_detection": NumberRule(None, None, icon="mdi:alpha-x-circle", name="Motion false detection"),
    "target_dis_closest": NumberRule(
        NumberDeviceClass.DISTANCE, UnitOfLength.CENTIMETERS, None, name="Target distance"
    ),
    "installation_height": NumberRule(NumberDeviceClass.DISTANCE, UnitOfLength.CENTIMETERS),
    "liquid_depth_max": NumberRule(NumberDeviceClass.DISTANCE, UnitOfLength.CENTIMETERS),
    "max_set": NumberRule(None, _P),
    "mini_set": NumberRule(None, _P),
    "cloud_recipe_number": NumberRule(None, None),
    "feed_num": NumberRule(None, None, icon="mdi:bowl"),
    "manual_feed": NumberRule(None, None, None, "mdi:bowl"),
    "voice_times": NumberRule(None, None, None, "mdi:repeat"),
    "water_set": NumberRule(None, "mL", icon="mdi:cup-water"),
    "powder_set": NumberRule(None, None, icon="mdi:coffee"),
    "speed_set": NumberRule(None, _P, None, "mdi:speedometer"),
    "warn_power1": NumberRule(NumberDeviceClass.POWER, UnitOfPower.WATT),
    "warn_power2": NumberRule(NumberDeviceClass.POWER, UnitOfPower.WATT),
    "output_power_limit": NumberRule(NumberDeviceClass.POWER, UnitOfPower.WATT),
    "backup_reserve": NumberRule(None, _P, icon="mdi:battery-arrow-down"),
    "charge_cur_set": NumberRule(NumberDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, None),
    "arm_down_percent": NumberRule(None, _P),
    "arm_up_percent": NumberRule(None, _P),
    "bright_value_sensor": NumberRule(None, None),
    **{
        f"brightness_{k}_{i}": NumberRule(None, _P, icon=f"mdi:brightness-{'5' if k == 'min' else '7'}")
        for k in ("min", "max")
        for i in (1, 2, 3)
    },
    "humidity_set": NumberRule(NumberDeviceClass.HUMIDITY, _P, None),
    "dehumidify_set_value": NumberRule(NumberDeviceClass.HUMIDITY, _P, None),
}

SELECTS: dict[str, SelectRule] = {
    "relay_status": SelectRule(icon="mdi:power-settings", name="Power-on behavior"),
    "light_mode": SelectRule(icon="mdi:led-on", name="Indicator light mode"),
    "led_type_1": SelectRule(icon="mdi:lightbulb"),
    "led_type_2": SelectRule(icon="mdi:lightbulb"),
    "led_type_3": SelectRule(icon="mdi:lightbulb"),
    "countdown_set": SelectRule(icon="mdi:timer"),
    "countdown": SelectRule(icon="mdi:timer"),
    "weather_delay": SelectRule(icon="mdi:weather-cloudy-clock"),
    "level": SelectRule(icon="mdi:tune", entity_category=None),
    "level_1": SelectRule(icon="mdi:tune", entity_category=None),
    "level_2": SelectRule(icon="mdi:tune", entity_category=None),
    "mode": SelectRule(icon="mdi:tune", entity_category=None),
    "work_mode": SelectRule(icon="mdi:tune", entity_category=None),
    "work_type": SelectRule(icon="mdi:tune"),
    "spray_mode": SelectRule(icon="mdi:spray", entity_category=None),
    "moodlighting": SelectRule(icon="mdi:lightbulb-group"),
    "fan_speed_enum": SelectRule(icon="mdi:fan", entity_category=None),
    "fan_vertical": SelectRule(icon="mdi:arrow-up-down"),
    "fan_horizontal": SelectRule(icon="mdi:arrow-left-right"),
    "control_back_mode": SelectRule(icon="mdi:swap-vertical", name="Motor direction"),
    "sensitivity": SelectRule(icon="mdi:tune"),
    "decibel_sensitivity": SelectRule(icon="mdi:volume-high"),
    "record_mode": SelectRule(icon="mdi:record-rec"),
    "basic_nightvision": SelectRule(icon="mdi:weather-night"),
    "basic_anti_flicker": SelectRule(icon="mdi:image-outline"),
    "motion_sensitivity": SelectRule(icon="mdi:motion-sensor"),
    "ipc_work_mode": SelectRule(icon="mdi:cctv"),
    "alarm_volume": SelectRule(icon="mdi:volume-high"),
    "alarm_mode": SelectRule(icon="mdi:alarm-light"),
    "alarm_state": SelectRule(icon="mdi:alarm-light"),
    "bright_state": SelectRule(icon="mdi:brightness-6"),
    "dehumidify_set_enum": SelectRule(icon="mdi:water-percent", entity_category=None),
    "cistern": SelectRule(icon="mdi:water-opacity"),
    "collection_mode": SelectRule(icon="mdi:air-filter"),
    "cup_number": SelectRule(icon="mdi:cup", entity_category=None),
    "concentration_set": SelectRule(icon="mdi:coffee"),
    "material": SelectRule(icon="mdi:coffee"),
    "temp_setting_quick_c": SelectRule(icon="mdi:thermometer"),
    "up_down": SelectRule(icon="mdi:arrow-up-down"),
}

BUTTONS: dict[str, ButtonRule] = {
    "reset_filter": ButtonRule(icon="mdi:air-filter"),
    "reset_map": ButtonRule(icon="mdi:map-marker-remove"),
    "reset_duster_cloth": ButtonRule(icon="mdi:broom"),
    "reset_edge_brush": ButtonRule(icon="mdi:broom"),
    "reset_roll_brush": ButtonRule(icon="mdi:broom"),
    "factory_reset": ButtonRule(None, _DIAG, "mdi:factory", enabled_default=False),
    "manual_clean": ButtonRule(icon="mdi:broom"),
    "device_restart": ButtonRule("restart", _CFG),
}

# Boolean DPs that are sirens rather than switches.
SIREN_CODES: frozenset[str] = frozenset({"alarm_switch", "siren_switch"})
# Categories whose plain `switch` / `switch_n` DPs are water valves.
VALVE_CATEGORIES: frozenset[str] = frozenset({"ggq", "sfkzq"})

# Factory scenes of v1 bulbs (hex `scene_data`; first byte = scene number). The values
# are the Tuya defaults, as collected by tuya-local; a bulb may report other speeds or
# colours for the same scene number, so the select matches on the number only.
LIGHT_SCENES_V1: dict[str, str] = {
    "night": "000c0d0000000000000000c80000",
    "reading": "010e0d0000840000000003e801f4",
    "working": "020e0d0000e80383000003e803e8",
    "leisure": "030e0d00001403e8000001f401f4",
    "soft": "04464602007803e803e800000000464602007803e8000a00000000",
    "colorful": "05464601000003e803e800000000464601007803e803e80000000046460100f003e803e800000000"
    "464601003d03e803e80000000046460100ae03e803e800000000464601011303e803e800000000",
    "dazzling": "06464601000003e803e800000000464601007803e803e80000000046460100f003e803e800000000",
    "gorgeous": "07464602000003e803e800000000464602007803e803e80000000046460200f003e803e800000000"
    "464602003d03e803e80000000046460200ae03e803e800000000464602011303e803e800000000",
}

# Codes that are internal/noisy and should not become entities on their own.
IGNORED_CODES: frozenset[str] = frozenset(
    {
        "scene_data",
        "scene_data_v2",
        "music_data",
        "control_data",
        "rhythm_mode",
        "sleep_mode",
        "wakeup_mode",
        "cycle_time",
        "random_time",
        "switch_inching",
        "cycle_schedule",
        "countdown_set_1",
        "light_pixel",
        "mic_data",
        "dreamlight_scene_mode",
        "lightpixel_number_set",
        "dreamlightmic_music_data",
        "pir_sensitivity",
        "ir_send",
        "ir_study",
        "key_study",
        "study_code",
        "ir_sendkey",
        "alarm_msg",
        "temp_unit_convert",
        "colour_data_hsv_scene",
        "situation_set",
    }
)

# Power-on behaviour of lights (raw ``power_memory`` DP, byte 1 = mode).
POWER_MEMORY_MODES: dict[int, str] = {0: "default", 1: "memory", 2: "custom"}

# Units as the cloud spells them → Home Assistant units. ``None`` drops a bogus unit
# (the ZG-205W reports its sensitivity in "x").
UNIT_ALIASES: dict[str, str | None] = {
    "": None,
    "x": None,
    "X": None,
    "lux": LIGHT_LUX,
    "Lux": LIGHT_LUX,
    "℃": UnitOfTemperature.CELSIUS,
    "℉": UnitOfTemperature.FAHRENHEIT,
    "C": UnitOfTemperature.CELSIUS,
    "F": UnitOfTemperature.FAHRENHEIT,
    "S": UnitOfTime.SECONDS,
    "sec": UnitOfTime.SECONDS,
    "Min": UnitOfTime.MINUTES,
    "mins": UnitOfTime.MINUTES,
    "H": UnitOfTime.HOURS,
    "h": UnitOfTime.HOURS,
    "hour": UnitOfTime.HOURS,
    "KWh": UnitOfEnergy.KILO_WATT_HOUR,
    "kwh": UnitOfEnergy.KILO_WATT_HOUR,
}


def clean_unit(unit: str | None) -> str | None:
    """Normalise a unit string from a DP spec (``None`` when it is empty or meaningless)."""
    if unit is None:
        return None
    text = str(unit).strip()
    return UNIT_ALIASES.get(text, text) if text in UNIT_ALIASES else (text or None)


_CHANNEL_RE = re.compile(r"^(?P<base>.+?)(?:_(?P<idx>\d+))?$")


def split_channel(code: str) -> tuple[str, int | None]:
    """``switch_led_2`` → (``switch_led``, 2); ``bright_value`` → (``bright_value``, None)."""
    match = _CHANNEL_RE.match(code)
    if not match:
        return code, None
    idx = match.group("idx")
    return match.group("base"), int(idx) if idx is not None else None


def pretty(code: str) -> str:
    """Human-readable name from a DP code."""
    return code.replace("_", " ").strip().capitalize()


# Human-readable names for Tuya product categories. The category code is always
# present on a device (the entity builders rely on it); it is the most reliable
# source for a readable device "type" when the OEM API gives no ``productName``.
# Unknown categories fall back to the product id at the call site.
CATEGORY_NAMES: dict[str, str] = {
    # Lighting
    "dj": "Light",
    "dd": "Light strip",
    "dc": "String lights",
    "fwd": "Ambient light",
    "xdd": "Ceiling light",
    "gyd": "Motion sensor light",
    "tyndj": "Solar light",
    "tgq": "Dimmer",
    "tgkg": "Dimmer switch",
    "ykq": "IR remote",
    "fsd": "Ceiling fan light",
    # Electrical
    "cz": "Socket",
    "pc": "Power strip",
    "kg": "Switch",
    "clkg": "Curtain switch",
    "wxkg": "Scene switch",
    "tdq": "Circuit breaker",
    "dlq": "Circuit breaker",
    # Climate
    "wk": "Thermostat",
    "wkf": "Radiator valve",
    "qn": "Heater",
    "kt": "Air conditioner",
    "ktkzq": "AC controller",
    "rs": "Water heater",
    "fs": "Fan",
    "fskg": "Fan switch",
    "kj": "Air purifier",
    "cs": "Dehumidifier",
    "jsq": "Humidifier",
    "xfj": "Fresh air system",
    # Covers
    "cl": "Curtain motor",
    # Sensors & safety
    "mcs": "Contact sensor",
    "pir": "Motion sensor",
    "rqbj": "Gas alarm",
    "ywbj": "Smoke alarm",
    "jwbj": "Methane alarm",
    "sj": "Water leak sensor",
    "wsdcg": "Temperature & humidity sensor",
    "ldcg": "Luminance sensor",
    "zd": "Vibration sensor",
    "sos": "SOS button",
    "co2bj": "CO2 detector",
    "cobj": "CO detector",
    # Other
    "sp": "Camera",
    "dghsxj": "Doorbell camera",
    "sd": "Robot vacuum",
    "mal": "Alarm host",
    "cwwsq": "Pet feeder",
    "ms": "Smart lock",
}


def category_name(category: str) -> str | None:
    """Readable device type for a Tuya product category, or ``None`` if unknown."""
    return CATEGORY_NAMES.get(category.strip().lower()) if category else None
