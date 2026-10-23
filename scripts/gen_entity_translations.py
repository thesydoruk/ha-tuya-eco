"""Generate the ``entity`` section of strings.json / translations from the code tables.

Run after changing ``mapping/codes.py``::

    .venv/Scripts/python scripts/gen_entity_translations.py

English names are derived from the tables (rule names or prettified codes) and written
to ``strings.json`` and ``translations/en.json``. Other languages keep their existing
entries; keys that are new appear there in English so a translator can fill them in.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))

from tuya_eco.mapping import codes as C  # noqa: E402

COMPONENT = ROOT / "custom_components" / "tuya_eco"

STATES = {
    "relay_status": {"power_off": "Off", "power_on": "On", "last": "Last state", "memory": "Last state"},
    "light_mode": {"relay": "Follows relay", "pos": "Opposite of relay", "none": "Off", "on": "On", "off": "Off"},
}


def _name(rule: object, code: str) -> str:
    return getattr(rule, "name", None) or C.pretty(code)


def build_en() -> dict[str, dict[str, dict]]:
    ent: dict[str, dict[str, dict]] = {}

    def put(platform: str, key: str, name: str, states: dict[str, str] | None = None) -> None:
        entry: dict = {"name": name}
        if states:
            entry["state"] = states
        ent.setdefault(platform, {})[key] = entry

    for code, rule in C.SWITCHES.items():
        put("switch", code, _name(rule, code))
    for code, rule in C.BINARY_SENSORS.items():
        put("binary_sensor", code, _name(rule, code))
    for code, rule in C.NUMBERS.items():
        put("number", code, _name(rule, code))
    for code, rule in C.SELECTS.items():
        put("select", code, _name(rule, code), STATES.get(code))
    for code, rule in C.SENSORS.items():
        if code == "bright_value_sensor":
            continue
        put("sensor", code, _name(rule, code))
    put("sensor", "bright_value", "Illuminance")
    for code, rule in C.BUTTONS.items():
        put("button", code, _name(rule, code))
    put("button", "snooze", "Snooze")
    put("siren", "siren", "Siren")
    put("valve", "zone_n", "Zone {n}")
    put("light", "light_n", "Light {n}")
    put("light", "floodlight", "Floodlight")
    put("light", "indicator", "Indicator light")
    put("light", "night_light", "Night light")
    put("light", "backlight", "Backlight")
    put("cover", "curtain_n", "Curtain {n}")
    put("cover", "door_n", "Door {n}")
    put("cover", "blind", "Blind")
    put("alarm_control_panel", "alarm", "Alarm")
    put("event", "button_n", "Button {n}", {"click": "Click", "double_click": "Double click", "press": "Long press"})
    put("event", "doorbell_message", "Doorbell message")
    put("event", "doorbell_picture", "Doorbell picture")
    return {p: dict(sorted(v.items())) for p, v in sorted(ent.items())}


def merge_language(path: Path, en: dict, *, is_english: bool) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    current = data.get("entity", {})
    added = 0
    merged: dict = {}
    for platform, keys in en.items():
        merged[platform] = {}
        for key, entry in keys.items():
            if is_english:
                merged[platform][key] = entry
                continue
            existing = current.get(platform, {}).get(key)
            if existing is None:
                merged[platform][key] = entry  # English until translated
                added += 1
            else:
                merged[platform][key] = existing
                if "state" in entry:
                    merged[platform][key].setdefault("state", {})
                    for sk, sv in entry["state"].items():
                        if sk not in merged[platform][key]["state"]:
                            merged[platform][key]["state"][sk] = sv
                            added += 1
    data["entity"] = merged
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return added


def main() -> None:
    en = build_en()
    merge_language(COMPONENT / "strings.json", en, is_english=True)
    merge_language(COMPONENT / "translations" / "en.json", en, is_english=True)
    total = sum(len(v) for v in en.values())
    print(f"english: {total} entity names")
    for lang in sorted((COMPONENT / "translations").glob("*.json")):
        if lang.name == "en.json":
            continue
        added = merge_language(lang, en, is_english=False)
        print(f"{lang.name}: {added} new keys left in English")


if __name__ == "__main__":
    main()
