"""Typed helpers for DP value specifications."""

from __future__ import annotations

from dataclasses import dataclass
import json
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
