"""Knowledge tables: Tuya DP codes → entity semantics.

These tables encode what the Tuya ecosystem means by a DP code, independent of a
particular product. They are consulted after the composite builders
(light / cover / climate / fan) have claimed their DPs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)


@dataclass(slots=True)
class SensorRule:
    """How to expose an integer/enum/string DP as a sensor."""

    device_class: str | None = None
    state_class: str | None = SensorStateClass.MEASUREMENT
    unit: str | None = None
    entity_category: str | None = None
    # Multiply the scaled value by this (e.g. mA → A handled via unit instead).
    icon: str | None = None
    # Force a scale when the cloud spec is missing/wrong (power often reports /10).
    default_scale: int | None = None


@dataclass(slots=True)
class BinaryRule:
    """How to expose a boolean/enum/bitmap DP as a binary sensor."""

    device_class: str | None = None
    # For enum DPs: values that mean "on".
    on_values: tuple[str, ...] = ()
    entity_category: str | None = None


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


_T = UnitOfTemperature
_P = PERCENTAGE

SENSORS: dict[str, SensorRule] = {
    "temp_current": SensorRule(SensorDeviceClass.TEMPERATURE, unit=_T.CELSIUS),
    "temp_current_f": SensorRule(SensorDeviceClass.TEMPERATURE, unit=_T.FAHRENHEIT),
    "va_temperature": SensorRule(SensorDeviceClass.TEMPERATURE, unit=_T.CELSIUS),
    "temperature": SensorRule(SensorDeviceClass.TEMPERATURE, unit=_T.CELSIUS),
    "temp_indoor": SensorRule(SensorDeviceClass.TEMPERATURE, unit=_T.CELSIUS),
    "humidity_value": SensorRule(SensorDeviceClass.HUMIDITY, unit=_P),
    "va_humidity": SensorRule(SensorDeviceClass.HUMIDITY, unit=_P),
    "humidity": SensorRule(SensorDeviceClass.HUMIDITY, unit=_P),
    "humidity_indoor": SensorRule(SensorDeviceClass.HUMIDITY, unit=_P),
    "cur_current": SensorRule(
        SensorDeviceClass.CURRENT, unit=UnitOfElectricCurrent.MILLIAMPERE, entity_category=EntityCategory.DIAGNOSTIC
    ),
    "cur_power": SensorRule(SensorDeviceClass.POWER, unit=UnitOfPower.WATT, default_scale=1),
    "cur_voltage": SensorRule(
        SensorDeviceClass.VOLTAGE,
        unit=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        default_scale=1,
    ),
    "power": SensorRule(SensorDeviceClass.POWER, unit=UnitOfPower.WATT),
    "add_ele": SensorRule(
        SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, UnitOfEnergy.KILO_WATT_HOUR, default_scale=3
    ),
    "total_forward_energy": SensorRule(
        SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, UnitOfEnergy.KILO_WATT_HOUR, default_scale=2
    ),
    "forward_energy_total": SensorRule(
        SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, UnitOfEnergy.KILO_WATT_HOUR, default_scale=2
    ),
    "battery_percentage": SensorRule(SensorDeviceClass.BATTERY, unit=_P, entity_category=EntityCategory.DIAGNOSTIC),
    "battery_value": SensorRule(SensorDeviceClass.BATTERY, unit=_P, entity_category=EntityCategory.DIAGNOSTIC),
    "battery": SensorRule(SensorDeviceClass.BATTERY, unit=_P, entity_category=EntityCategory.DIAGNOSTIC),
    "residual_electricity": SensorRule(SensorDeviceClass.BATTERY, unit=_P, entity_category=EntityCategory.DIAGNOSTIC),
    "co2_value": SensorRule(SensorDeviceClass.CO2, unit=CONCENTRATION_PARTS_PER_MILLION),
    "pm25_value": SensorRule(SensorDeviceClass.PM25, unit=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER),
    "pm25": SensorRule(SensorDeviceClass.PM25, unit=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER),
    "pm1": SensorRule(SensorDeviceClass.PM1, unit=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER),
    "pm10": SensorRule(SensorDeviceClass.PM10, unit=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER),
    "voc_value": SensorRule(SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS, unit=CONCENTRATION_PARTS_PER_MILLION),
    "ch2o_value": SensorRule(None, unit=CONCENTRATION_PARTS_PER_MILLION, icon="mdi:molecule"),
    "bright_value_sensor": SensorRule(SensorDeviceClass.ILLUMINANCE, unit=LIGHT_LUX),
    "illuminance_value": SensorRule(SensorDeviceClass.ILLUMINANCE, unit=LIGHT_LUX),
    "countdown_1": SensorRule(SensorDeviceClass.DURATION, None, UnitOfTime.SECONDS, EntityCategory.DIAGNOSTIC),
    "countdown": SensorRule(SensorDeviceClass.DURATION, None, UnitOfTime.SECONDS, EntityCategory.DIAGNOSTIC),
    "work_state": SensorRule(SensorDeviceClass.ENUM, None, entity_category=EntityCategory.DIAGNOSTIC),
    "status": SensorRule(SensorDeviceClass.ENUM, None, entity_category=EntityCategory.DIAGNOSTIC),
    "filter_life": SensorRule(None, unit=_P, entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:air-filter"),
    "water_level": SensorRule(SensorDeviceClass.ENUM, None, icon="mdi:water-percent"),
    "wind_speed": SensorRule(SensorDeviceClass.WIND_SPEED, unit="m/s"),
    "signal_strength": SensorRule(
        SensorDeviceClass.SIGNAL_STRENGTH, unit="dBm", entity_category=EntityCategory.DIAGNOSTIC
    ),
}

BINARY_SENSORS: dict[str, BinaryRule] = {
    "doorcontact_state": BinaryRule(BinarySensorDeviceClass.DOOR),
    "door_state": BinaryRule(BinarySensorDeviceClass.DOOR),
    "pir": BinaryRule(BinarySensorDeviceClass.MOTION, ("pir",)),
    "presence_state": BinaryRule(BinarySensorDeviceClass.OCCUPANCY, ("presence",)),
    "watersensor_state": BinaryRule(BinarySensorDeviceClass.MOISTURE, ("1", "alarm")),
    "gas_sensor_state": BinaryRule(BinarySensorDeviceClass.GAS, ("1", "alarm")),
    "gas_sensor_status": BinaryRule(BinarySensorDeviceClass.GAS, ("1", "alarm")),
    "smoke_sensor_state": BinaryRule(BinarySensorDeviceClass.SMOKE, ("1", "alarm")),
    "smoke_sensor_status": BinaryRule(BinarySensorDeviceClass.SMOKE, ("1", "alarm")),
    "co_state": BinaryRule(BinarySensorDeviceClass.CO, ("1", "alarm")),
    "ch2o_state": BinaryRule(BinarySensorDeviceClass.SAFETY, ("1", "alarm")),
    "temper_alarm": BinaryRule(BinarySensorDeviceClass.TAMPER),
    "fault": BinaryRule(BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    "feed_state": BinaryRule(None, ("feeding",)),
    "wet": BinaryRule(BinarySensorDeviceClass.MOISTURE),
    "overcurrent_alarm": BinaryRule(BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    "overvoltage_alarm": BinaryRule(BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    "charge_state": BinaryRule(BinarySensorDeviceClass.BATTERY_CHARGING),
    "tankfull": BinaryRule(BinarySensorDeviceClass.PROBLEM),
    "defrost": BinaryRule(None),
    "window_state": BinaryRule(BinarySensorDeviceClass.WINDOW, ("opened",)),
    "window_check": BinaryRule(BinarySensorDeviceClass.WINDOW),
    "online": BinaryRule(BinarySensorDeviceClass.CONNECTIVITY, entity_category=EntityCategory.DIAGNOSTIC),
}

SWITCHES: dict[str, SwitchRule] = {
    "switch": SwitchRule(SwitchDeviceClass.OUTLET),
    "switch_1": SwitchRule(SwitchDeviceClass.OUTLET),
    "switch_2": SwitchRule(SwitchDeviceClass.OUTLET),
    "switch_3": SwitchRule(SwitchDeviceClass.OUTLET),
    "switch_4": SwitchRule(SwitchDeviceClass.OUTLET),
    "switch_5": SwitchRule(SwitchDeviceClass.OUTLET),
    "switch_6": SwitchRule(SwitchDeviceClass.OUTLET),
    "switch_7": SwitchRule(SwitchDeviceClass.OUTLET),
    "switch_8": SwitchRule(SwitchDeviceClass.OUTLET),
    "switch_usb1": SwitchRule(SwitchDeviceClass.OUTLET, icon="mdi:usb-port"),
    "switch_usb2": SwitchRule(SwitchDeviceClass.OUTLET, icon="mdi:usb-port"),
    "switch_usb3": SwitchRule(SwitchDeviceClass.OUTLET, icon="mdi:usb-port"),
    "switch_usb4": SwitchRule(SwitchDeviceClass.OUTLET, icon="mdi:usb-port"),
    "switch_usb5": SwitchRule(SwitchDeviceClass.OUTLET, icon="mdi:usb-port"),
    "switch_usb6": SwitchRule(SwitchDeviceClass.OUTLET, icon="mdi:usb-port"),
    "child_lock": SwitchRule(None, EntityCategory.CONFIG, "mdi:account-lock"),
    "switch_backlight": SwitchRule(None, EntityCategory.CONFIG, "mdi:led-off"),
    "light": SwitchRule(None, EntityCategory.CONFIG, "mdi:led-on"),
    "switch_sound": SwitchRule(None, EntityCategory.CONFIG, "mdi:volume-high"),
    "anion": SwitchRule(None, EntityCategory.CONFIG, "mdi:atom"),
    "uv": SwitchRule(None, EntityCategory.CONFIG, "mdi:lightbulb"),
    "filter_reset": SwitchRule(None, EntityCategory.CONFIG, "mdi:air-filter"),
    "switch_spray": SwitchRule(None, icon="mdi:spray"),
    "switch_disturb": SwitchRule(None, EntityCategory.CONFIG, "mdi:bell-off"),
    "sleep": SwitchRule(None, EntityCategory.CONFIG, "mdi:power-sleep"),
    "frost": SwitchRule(None, EntityCategory.CONFIG, "mdi:snowflake"),
    "anti_frost": SwitchRule(None, EntityCategory.CONFIG, "mdi:snowflake"),
    "window_check": SwitchRule(None, EntityCategory.CONFIG, "mdi:window-open"),
    "eco": SwitchRule(None, EntityCategory.CONFIG, "mdi:leaf"),
    "switch_save_energy": SwitchRule(None, EntityCategory.CONFIG, "mdi:leaf"),
    "do_not_disturb": SwitchRule(None, EntityCategory.CONFIG, "mdi:minus-circle"),
    "relay_status": SwitchRule(None, EntityCategory.CONFIG),
    "start": SwitchRule(None, icon="mdi:play"),
    "pump_switch": SwitchRule(None, icon="mdi:water-pump"),
    "water_switch": SwitchRule(None, icon="mdi:water"),
    "ipc_work_mode": SwitchRule(None, EntityCategory.CONFIG),
    "switch_alarm_sound": SwitchRule(None, EntityCategory.CONFIG, "mdi:alarm-bell"),
    "switch_alarm_light": SwitchRule(None, EntityCategory.CONFIG, "mdi:alarm-light"),
}

NUMBERS: dict[str, NumberRule] = {
    "countdown": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer"),
    "countdown_1": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer"),
    "countdown_2": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer"),
    "countdown_3": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer"),
    "countdown_4": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer"),
    "countdown_usb1": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer"),
    "temp_set_f": NumberRule(NumberDeviceClass.TEMPERATURE, _T.FAHRENHEIT, None),
    "upper_temp": NumberRule(NumberDeviceClass.TEMPERATURE, _T.CELSIUS),
    "lower_temp": NumberRule(NumberDeviceClass.TEMPERATURE, _T.CELSIUS),
    "temp_correction": NumberRule(NumberDeviceClass.TEMPERATURE, _T.CELSIUS, icon="mdi:thermometer-plus"),
    "sensitivity": NumberRule(None, None, icon="mdi:tune"),
    "volume_set": NumberRule(None, None, icon="mdi:volume-high"),
    "alarm_time": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer"),
    "delay_set": NumberRule(NumberDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer"),
    "far_detection": NumberRule(NumberDeviceClass.DISTANCE, "cm", icon="mdi:signal-distance"),
    "near_detection": NumberRule(NumberDeviceClass.DISTANCE, "cm", icon="mdi:signal-distance"),
    "cloud_recipe_number": NumberRule(None, None),
    "feed_num": NumberRule(None, None, icon="mdi:bowl"),
    "manual_feed": NumberRule(None, None, icon="mdi:bowl"),
    "bright_value_sensor": NumberRule(None, None),
    "brightness_min_1": NumberRule(None, _P, icon="mdi:brightness-5"),
    "brightness_max_1": NumberRule(None, _P, icon="mdi:brightness-7"),
    "humidity_set": NumberRule(NumberDeviceClass.HUMIDITY, _P, None),
}

SELECTS: dict[str, SelectRule] = {
    "relay_status": SelectRule(icon="mdi:power-settings", name="Power-on behavior"),
    "light_mode": SelectRule(icon="mdi:led-on", name="Indicator light mode"),
    "led_type_1": SelectRule(icon="mdi:lightbulb"),
    "led_type_2": SelectRule(icon="mdi:lightbulb"),
    "led_type_3": SelectRule(icon="mdi:lightbulb"),
    "countdown_set": SelectRule(icon="mdi:timer"),
    "level": SelectRule(icon="mdi:tune"),
    "mode": SelectRule(icon="mdi:tune", entity_category=None),
    "fan_speed_enum": SelectRule(icon="mdi:fan", entity_category=None),
    "control_back_mode": SelectRule(icon="mdi:swap-vertical"),
    "sensitivity": SelectRule(icon="mdi:tune"),
    "decibel_sensitivity": SelectRule(icon="mdi:volume-high"),
    "record_mode": SelectRule(icon="mdi:record-rec"),
    "basic_nightvision": SelectRule(icon="mdi:weather-night"),
    "basic_anti_flicker": SelectRule(icon="mdi:image-outline"),
    "motion_sensitivity": SelectRule(icon="mdi:motion-sensor"),
    "work_mode_select": SelectRule(icon="mdi:tune"),
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
        "power_memory",
        "do_not_disturb",
        "cycle_time",
        "random_time",
        "remote_switch",
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
    }
)

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
