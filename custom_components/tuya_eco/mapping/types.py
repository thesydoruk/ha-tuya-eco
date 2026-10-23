"""Typed helpers for DP value specifications."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import struct
from typing import Any


@dataclass(slots=True)
class IntegerType:
    """Integer DP specification (`{"min","max","scale","step","unit"}`)."""

    min: int = 0
    max: int = 0
    scale: int = 0
    step: int = 1
    unit: str = ""

    @classmethod
    def from_values(cls, values: dict[str, Any] | None) -> IntegerType:
        """Build from the raw `values` dict (tolerant to missing keys)."""
        values = values or {}

        def _int(key: str, default: int) -> int:
            try:
                return int(float(values.get(key, default)))
            except (TypeError, ValueError):
                return default

        return cls(
            min=_int("min", 0),
            max=_int("max", 0),
            scale=_int("scale", 0),
            step=max(_int("step", 1), 1),
            unit=str(values.get("unit") or ""),
        )

    @property
    def factor(self) -> float:
        """10**scale."""
        return float(10**self.scale)

    def scale_value(self, raw: Any) -> float | None:
        """Raw → scaled (float)."""
        try:
            return float(raw) / self.factor
        except (TypeError, ValueError):
            return None

    def scale_back(self, value: float) -> int:
        """Scaled → raw int, clamped to the range when known."""
        raw = round(float(value) * self.factor)
        if self.max > self.min:
            raw = max(self.min, min(self.max, raw))
        return raw

    @property
    def min_scaled(self) -> float:
        """Scaled minimum."""
        return self.min / self.factor

    @property
    def max_scaled(self) -> float:
        """Scaled maximum."""
        return self.max / self.factor

    @property
    def step_scaled(self) -> float:
        """Scaled step."""
        return self.step / self.factor

    def remap_to(self, raw: float, to_min: float, to_max: float, *, reverse: bool = False) -> float:
        """Map a raw value within [min,max] onto [to_min,to_max]."""
        if self.max <= self.min:
            return to_min
        if reverse:
            raw = self.max - raw + self.min
        return ((raw - self.min) / (self.max - self.min)) * (to_max - to_min) + to_min

    def remap_from(self, value: float, from_min: float, from_max: float, *, reverse: bool = False) -> int:
        """Map a value within [from_min,from_max] onto the raw [min,max]."""
        if from_max <= from_min:
            return self.min
        raw = ((value - from_min) / (from_max - from_min)) * (self.max - self.min) + self.min
        if reverse:
            raw = self.max - raw + self.min
        return int(round(max(self.min, min(self.max, raw))))


@dataclass(slots=True)
class EnumType:
    """Enum DP specification (`{"range": [...]}`)."""

    range: list[str]

    @classmethod
    def from_values(cls, values: dict[str, Any] | None) -> EnumType:
        """Build from the raw `values` dict."""
        values = values or {}
        raw = values.get("range")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = [raw]
        return cls(range=[str(v) for v in (raw or [])])


@dataclass(slots=True)
class BitmapType:
    """Bitmap DP specification (`{"label": [...]}`)."""

    labels: list[str]

    @classmethod
    def from_values(cls, values: dict[str, Any] | None) -> BitmapType:
        """Build from the raw `values` dict."""
        values = values or {}
        return cls(labels=[str(v) for v in (values.get("label") or [])])

    def active(self, raw: Any) -> list[str]:
        """Labels whose bit is set."""
        try:
            bits = int(raw)
        except (TypeError, ValueError):
            return []
        return [label for i, label in enumerate(self.labels) if bits & (1 << i)]


@dataclass(slots=True)
class PowerMemory:
    """Raw ``power_memory`` DP of lights: what the light does when mains power returns.

    12 bytes, big-endian: version, mode (0 default / 1 last state / 2 custom), then the
    custom state as hue (0-360), saturation (0-1000), value (0-1000), brightness and colour
    temperature (raw light units). Transported as base64.
    """

    mode: int = 1
    hue: int = 0
    saturation: int = 0
    value: int = 0
    brightness: int = 0
    color_temp: int = 0
    version: int = 0

    _FORMAT = ">BBHHHHH"

    @classmethod
    def decode(cls, raw: Any) -> PowerMemory | None:
        """Parse the base64 DP value (``None`` when it is not a power-memory payload)."""
        if not isinstance(raw, str) or not raw:
            return None
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            return None
        if len(data) < 2:
            return None
        data = data[:12].ljust(12, b"\0")
        version, mode, h, s, v, bright, temp = struct.unpack(cls._FORMAT, data)
        return cls(mode=mode, hue=h, saturation=s, value=v, brightness=bright, color_temp=temp, version=version)

    def encode(self) -> str:
        """Base64 DP value."""
        clamp = lambda n: max(0, min(0xFFFF, int(n)))  # noqa: E731
        data = struct.pack(
            self._FORMAT,
            self.version & 0xFF,
            self.mode & 0xFF,
            clamp(self.hue),
            clamp(self.saturation),
            clamp(self.value),
            clamp(self.brightness),
            clamp(self.color_temp),
        )
        return base64.b64encode(data).decode("ascii")
