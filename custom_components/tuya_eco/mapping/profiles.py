"""Optional YAML profiles keyed by product id.

A profile fully replaces the automatic mapping for a product. Format::

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

Files live in ``custom_components/tuya_eco/profiles/*.yaml``.
"""

from __future__ import annotations

from functools import lru_cache
import logging
from pathlib import Path
from typing import Any

from homeassistant.const import Platform
import yaml

from ..providers.base import CloudDevice
from .model import EntityDef

_LOGGER = logging.getLogger(__name__)
_PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"


@lru_cache(maxsize=1)
def _load_all() -> dict[str, list[EntityDef]]:
    result: dict[str, list[EntityDef]] = {}
    if not _PROFILE_DIR.is_dir():
        return result
    for path in sorted(_PROFILE_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            defs = [_parse_entity(item) for item in data.get("entities", [])]
            for product in data.get("products", []):
                pid = str(product.get("id", "")).strip()
                if pid:
                    result[pid] = defs
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Ignoring profile %s: %s", path.name, err)
    return result


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


def profile_entities(device: CloudDevice) -> list[EntityDef] | None:
    """Return the profile for the device's product id, if one exists."""
    if not device.product_id:
        return None
    defs = _load_all().get(device.product_id)
    return list(defs) if defs else None
