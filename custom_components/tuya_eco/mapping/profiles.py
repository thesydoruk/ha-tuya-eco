"""Optional YAML profiles keyed by product id.

A profile either replaces the automatic mapping for a product (``mode: replace``, the
default) or extends it (``mode: extend``). Format::

    name: Energy monitoring smart plug
    products:
      - id: 37mnhia3pojleqfh
    entities:
      - platform: switch
        key: main
        dps: {switch: 1}
        device_class: outlet
      - platform: sensor
        key: power
        dps: {value: 5}
        device_class: power
        unit: W
        meta: {scale: 1}

An extending profile fixes what the cloud gets wrong or leaves out. ``dps`` entries are
merged into the cloud DP schema (a DP id already there is overridden) and go through the
normal code tables; ``entities`` are added on top of the automatic ones (an entity with
the same key replaces the generated one)::

    mode: extend
    dps:
      - {id: 7, code: rf_pairing, type: Boolean}
      - {id: 38, code: relay_status, type: Enum, values: {range: ["off", "on"]}}
      - {id: 116, code: movement_state, type: Enum, read_only: true, values: {range: [none, large_move]}}
    entities:
      - platform: button
        key: reset
        dps: {press: 101}
        meta: {press_value: Reset}

Files live in ``custom_components/tuya_eco/profiles/*.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import logging
from pathlib import Path
from typing import Any

from homeassistant.const import Platform
import yaml

from ..providers.base import CloudDevice, DpSpec, DpType
from .model import EntityDef

_LOGGER = logging.getLogger(__name__)
_PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"


@dataclass(slots=True)
class Profile:
    """A parsed profile file."""

    name: str
    extend: bool = False
    dps: dict[int, DpSpec] = field(default_factory=dict)
    entities: list[EntityDef] = field(default_factory=list)


@lru_cache(maxsize=1)
def _load_all() -> dict[str, Profile]:
    result: dict[str, Profile] = {}
    if not _PROFILE_DIR.is_dir():
        return result
    for path in sorted(_PROFILE_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            profile = parse_profile(data, path.stem)
            for product in data.get("products", []):
                pid = str(product.get("id", "")).strip()
                if pid:
                    result[pid] = profile
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Ignoring profile %s: %s", path.name, err)
    return result


def parse_profile(data: dict[str, Any], fallback_name: str = "") -> Profile:
    """Parse the content of one profile file."""
    mode = str(data.get("mode", "replace")).lower()
    if mode not in ("replace", "extend"):
        raise ValueError(f"unknown mode {mode!r}")
    return Profile(
        name=str(data.get("name") or fallback_name),
        extend=mode == "extend",
        dps={spec.dp_id: spec for spec in (_parse_dp(item) for item in data.get("dps", []))},
        entities=[_parse_entity(item) for item in data.get("entities", [])],
    )


def _parse_dp(item: dict[str, Any]) -> DpSpec:
    return DpSpec(
        dp_id=int(item["id"]),
        code=str(item["code"]),
        type=DpType(item.get("type", "Raw")),
        values=dict(item.get("values") or {}),
        read_only=bool(item.get("read_only", False)),
    )


def _parse_entity(item: dict[str, Any]) -> EntityDef:
    return EntityDef(
        platform=Platform(item["platform"]),
        key=str(item["key"]),
        dps={str(k): int(v) for k, v in item["dps"].items()},
        name=item.get("name"),
        device_class=item.get("device_class"),
        state_class=item.get("state_class"),
        unit=item.get("unit"),
        entity_category=item.get("entity_category"),
        icon=item.get("icon"),
        translation_key=item.get("translation_key"),
        meta=dict(item.get("meta") or {}),
    )


def preload_profiles() -> int:
    """Read the YAML profiles (blocking I/O; call from an executor). Returns the product count."""
    return len(_load_all())


def device_profile(device: CloudDevice) -> Profile | None:
    """Return the profile for the device's product id, if one exists."""
    if not device.product_id:
        return None
    return _load_all().get(device.product_id)
