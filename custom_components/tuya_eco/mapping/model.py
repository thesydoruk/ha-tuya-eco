"""Entity definitions produced by the mapping layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import Platform


@dataclass(slots=True)
class EntityDef:
    """A platform-agnostic description of one entity to create.

    `dps` maps a *role* (platform specific, e.g. ``"switch"``, ``"brightness"``) to
    a DP id. `meta` carries platform specific extras (types, ranges, mappings).
    """

    platform: Platform
    key: str
    dps: dict[str, int]
    name: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    unit: str | None = None
    entity_category: str | None = None
    icon: str | None = None
    translation_key: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def primary_dp(self) -> int:
        """The main DP of the entity."""
        return next(iter(self.dps.values()))

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the entry cache."""
        return {
            "platform": str(self.platform),
            "key": self.key,
            "dps": self.dps,
            "name": self.name,
            "device_class": self.device_class,
            "state_class": self.state_class,
            "unit": self.unit,
            "entity_category": self.entity_category,
            "icon": self.icon,
            "translation_key": self.translation_key,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntityDef:
        """Restore from the entry cache."""
        return cls(
            platform=Platform(data["platform"]),
            key=data["key"],
            dps={str(k): int(v) for k, v in data["dps"].items()},
            name=data.get("name"),
            device_class=data.get("device_class"),
            state_class=data.get("state_class"),
            unit=data.get("unit"),
            entity_category=data.get("entity_category"),
            icon=data.get("icon"),
            translation_key=data.get("translation_key"),
            meta=dict(data.get("meta") or {}),
        )
