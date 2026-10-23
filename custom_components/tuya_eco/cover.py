"""Cover platform (curtains, blinds, curtain robots, garage doors).

Tuya position semantics: raw 0 = fully open, raw max = fully closed, so the HA
position is inverted (``invert`` meta, default on). Curtain switches (``clkg``)
expose a ``control_back_mode`` DP; when it reads ``back`` the motor direction is
reversed and the position is *not* inverted.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import EntityDef, IntegerType
from .platform_setup import async_setup_platform_entities

_CLOSED_STATES = {"close", "fully_close", "closed"}
_OPEN_STATES = {"open", "fully_open", "opened"}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up covers."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.COVER, EcoCover)


def _pick(options: list[str], *candidates: str) -> str | None:
    lowered = {o.lower(): o for o in options}
    for cand in candidates:
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    return None


class EcoCover(EcoEntity, CoverEntity):
    """Curtain / blind / garage door."""

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        meta = definition.meta
        dps = definition.dps
        self._garage = bool(meta.get("garage"))
        self._bool_control = bool(meta.get("bool_control"))
        options = [str(o) for o in meta.get("options", [])]
        self._open = _pick(options, meta.get("open_cmd", ""), "open", "up", "fz")
        self._close = _pick(options, meta.get("close_cmd", ""), "close", "down", "zz")
        self._stop = _pick(options, meta.get("stop_cmd", ""), "stop", "pause")
        self._pos = IntegerType.from_values(meta.get("position")) if "position" in dps else None
        self._pos_state = IntegerType.from_values(meta.get("position_state")) if "position_state" in dps else None
        self._tilt = IntegerType.from_values(meta.get("tilt")) if "tilt" in dps else None
        features = CoverEntityFeature(0)
        if self._garage or self._bool_control or self._open:
            features |= CoverEntityFeature.OPEN
        if self._garage or self._bool_control or self._close:
            features |= CoverEntityFeature.CLOSE
        if self._stop:
            features |= CoverEntityFeature.STOP
        if self._pos is not None:
            features |= CoverEntityFeature.SET_POSITION
        if self._tilt is not None:
            features |= CoverEntityFeature.SET_TILT_POSITION
        self._attr_supported_features = features
        if self._garage:
            self._attr_device_class = CoverDeviceClass.GARAGE
        elif definition.device_class:
            self._attr_device_class = CoverDeviceClass(definition.device_class)
        else:
            self._attr_device_class = (
                CoverDeviceClass.CURTAIN
                if device.info.category in ("cl", "clkg", "jdcljqr")
                else CoverDeviceClass.SHUTTER
            )

    # ------------------------------------------------------------------ helpers

    @property
    def _inverted(self) -> bool:
        if "back_mode" in self.definition.dps:
            return self.dp_value("back_mode") != "back"
        return bool(self.definition.meta.get("invert", True))

    def _to_ha(self, kind: IntegerType, raw: Any) -> int | None:
        if raw is None:
            return None
        try:
            return int(round(kind.remap_to(float(raw), 0, 100, reverse=self._inverted)))
        except (TypeError, ValueError):
            return None

    def _to_raw(self, kind: IntegerType, position: float) -> int:
        return kind.remap_from(position, 0, 100, reverse=self._inverted)

    # ------------------------------------------------------------------ state

    @property
    def current_cover_position(self) -> int | None:
        """0 closed … 100 open."""
        if self._pos_state is not None:
            return self._to_ha(self._pos_state, self.dp_value("position_state"))
        if self._pos is not None:
            return self._to_ha(self._pos, self.dp_value("position"))
        return None

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Tilt."""
        if self._tilt is None:
            return None
        return self._to_ha(self._tilt, self.dp_value("tilt"))

    @property
    def is_closed(self) -> bool | None:
        """Closed."""
        if self._garage:
            contact = self.dp_value("contact")
            return None if contact is None else not bool(contact)
        pos = self.current_cover_position
        if pos is not None:
            return pos == 0
        state = self.dp_value("state")
        if state is not None:
            text = str(state).lower()
            if text in _CLOSED_STATES:
                return True
            if text in _OPEN_STATES:
                return False
        if self._bool_control:
            value = self.dp_value("switch")
            return None if value is None else not bool(value)
        return None

    # ------------------------------------------------------------------ commands

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open."""
        if self._garage or self._bool_control:
            await self.async_write("switch", True)
        elif self._pos is not None:
            await self.async_write("position", self._to_raw(self._pos, 100))
        elif self._open:
            await self.async_write("control", self._open)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close."""
        if self._garage or self._bool_control:
            await self.async_write("switch", False)
        elif self._pos is not None:
            await self.async_write("position", self._to_raw(self._pos, 0))
        elif self._close:
            await self.async_write("control", self._close)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop."""
        if self._stop:
            await self.async_write("control", self._stop)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Position."""
        if self._pos is None:
            return
        await self.async_write("position", self._to_raw(self._pos, float(kwargs[ATTR_POSITION])))

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Tilt."""
        if self._tilt is None:
            return
        await self.async_write("tilt", self._to_raw(self._tilt, float(kwargs[ATTR_TILT_POSITION])))
