"""Cover platform."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import ATTR_POSITION, CoverDeviceClass, CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EcoEntity
from .hub import EcoHub
from .local.device import EcoDevice
from .mapping import EntityDef, IntegerType
from .platform_setup import async_setup_platform_entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up covers."""
    async_setup_platform_entities(hass, entry, async_add_entities, Platform.COVER, EcoCover)


def _pick(options: list[str], *candidates: str) -> str | None:
    lowered = {o.lower(): o for o in options}
    for cand in candidates:
        if cand in lowered:
            return lowered[cand]
    return None


class EcoCover(EcoEntity, CoverEntity):
    """Curtain / blind / garage door."""

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        super().__init__(hub, device, definition)
        meta = definition.meta
        self._garage = bool(meta.get("garage"))
        options = [str(o) for o in meta.get("options", [])]
        self._open = _pick(options, "open", "up", "fz")
        self._close = _pick(options, "close", "down", "zz")
        self._stop = _pick(options, "stop", "pause")
        self._pos = IntegerType.from_values(meta.get("position")) if "position" in definition.dps else None
        self._pos_state = (
            IntegerType.from_values(meta.get("position_state")) if "position_state" in definition.dps else None
        )
        features = CoverEntityFeature(0)
        if self._garage or self._open:
            features |= CoverEntityFeature.OPEN
        if self._garage or self._close:
            features |= CoverEntityFeature.CLOSE
        if self._stop:
            features |= CoverEntityFeature.STOP
        if self._pos is not None:
            features |= CoverEntityFeature.SET_POSITION
        self._attr_supported_features = features
        if self._garage:
            self._attr_device_class = CoverDeviceClass.GARAGE
        elif definition.device_class is None:
            self._attr_device_class = (
                CoverDeviceClass.CURTAIN if device.info.category in ("cl", "clkg") else CoverDeviceClass.SHUTTER
            )

    @property
    def current_cover_position(self) -> int | None:
        """0 closed … 100 open (Tuya reports 0 = open on most curtains, hence invert)."""
        src = self._pos_state if self._pos_state is not None else self._pos
        role = "position_state" if self._pos_state is not None else "position"
        if src is None:
            return None
        raw = self.value(role)
        if raw is None:
            return None
        return int(round(src.remap_to(float(raw), 0, 100, reverse=self.definition.meta.get("invert", True))))

    @property
    def is_closed(self) -> bool | None:
        """Closed."""
        if self._garage:
            contact = self.value("contact")
            return None if contact is None else bool(contact) is False
        pos = self.current_cover_position
        if pos is not None:
            return pos == 0
        return None

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open."""
        if self._garage:
            await self.async_write("switch", True)
        elif self._open:
            await self.async_write("control", self._open)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close."""
        if self._garage:
            await self.async_write("switch", False)
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
        position = float(kwargs[ATTR_POSITION])
        raw = self._pos.remap_from(position, 0, 100, reverse=self.definition.meta.get("invert", True))
        await self.async_write("position", raw)
