<p align="center">
  <img src="brand/logo@2x.png" alt="Tuya Eco" width="420">
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS custom repository"></a>
  <a href="https://github.com/thesydoruk/ha-tuya-eco/actions/workflows/ci.yml"><img src="https://github.com/thesydoruk/ha-tuya-eco/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/thesydoruk/ha-tuya-eco/releases"><img src="https://img.shields.io/github/v/release/thesydoruk/ha-tuya-eco?include_prereleases" alt="Release"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2025.1%2B-03a9f4.svg" alt="Home Assistant 2025.1+">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/thesydoruk/ha-tuya-eco" alt="MIT license"></a>
</p>

# Tuya Eco for Home Assistant

Local control of the **Tuya ecosystem** — Smart Life / Tuya Smart, **Ledvance SMART+ WiFi**,
Sylvania Smart WiFi, Tuya IoT Platform projects and any other white-label Tuya app — from a
single, modular integration. Think *tuya-local*, but with the device knowledge fetched from the
cloud once instead of maintained by hand, and with a provider layer that makes a new app a
single module.

* **Local first.** Devices are controlled over the LAN (Tuya protocol 3.1 – 3.5, including the
  session-key handshake of 3.4/3.5). The vendor cloud is only used to fetch the `local_key` and
  the data-point (DP) schema of each device, and optionally as a fallback for commands.
* **Self-healing.** A device that got a new IP, was power-cycled, or was re-paired in the app
  (which rotates its local key) comes back on its own: LAN discovery, exponential-backoff
  reconnects, and automatic key refresh from the cloud.
* **Smart mapping.** Entities are generated from the DP schema the cloud already knows
  (codes, types, ranges, units) instead of a hand-maintained per-product database. YAML
  profiles can still override the mapping for odd products.
* **Modular.** Each ecosystem is one *provider* module. Adding another Tuya-based app means
  implementing one class and registering it. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Supported providers

| Provider | Login | How the local key is obtained |
|---|---|---|
| Smart Life / Tuya Smart | QR code scanned in the app (same as the core HA integration) | Tuya device-sharing API returns `local_key` + DP⇄code map |
| Ledvance SMART+ WiFi | e-mail + password of the SMART+ account | Tuya mobile-app API with the SMART+ app identity; see [docs/LEDVANCE.md](docs/LEDVANCE.md) |
| Sylvania Smart WiFi | e-mail + password | same as Ledvance |
| Tuya IoT Platform | cloud project access id / secret | OpenAPI device list + specifications (own client) |
| Other Tuya OEM app | e-mail + password + the app's client id / secret | Tuya mobile-app API |
| Manual | device id + local key | you already have the key (e.g. shown in a newer SMART+ app) |

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → *Custom repositories* → add `https://github.com/thesydoruk/ha-tuya-eco`
   as *Integration*.
2. Install **Tuya Eco** and restart Home Assistant.

### Manual

Copy `custom_components/tuya_eco` into `<config>/custom_components/` and restart.

The integration icon in the Home Assistant UI comes from the
[home-assistant/brands](https://github.com/home-assistant/brands) repository; until the brand
is merged there the integration shows a generic icon (see [docs/BRANDS.md](docs/BRANDS.md)).

## Setup

*Settings → Devices & services → Add integration → Tuya Eco*, pick the ecosystem and follow the
prompts.

* **Smart Life / Tuya Smart** — enter the *user code* from the app
  (*Me → ⚙ → Account and Security → User Code*), then scan the QR code shown by Home
  Assistant with the app (*+ → Scan*) and confirm.
* **Ledvance SMART+ / Sylvania** — enter the e-mail, password and the region of the account
  (EU for Europe, US for the Americas). The SMART+ app will show a "signed in on another
  device" notice; that is expected.
* **Tuya IoT Platform** — create a cloud project on <https://iot.tuya.com>, link your app
  account, and paste the *Access ID* and *Access Secret* with the data center.

Devices appear automatically as soon as they are found on the LAN. UDP ports 6666, 6667 and
7000 must reach Home Assistant (devices and HA on the same VLAN, or with broadcast relaying).

## Options

| Option | Default | Meaning |
|---|---|---|
| Full poll interval | 30 s | How often the full DP state is re-read (push updates arrive instantly anyway). |
| Cloud refresh interval | 12 h | Periodic re-sync of devices/keys from the cloud. `0` = only when a key stops working. |
| Cloud fallback | on | Send commands through the cloud when the device is unreachable on the LAN. |

## How recovery works

| Situation | What happens |
|---|---|
| Device got a new IP | Its discovery broadcast (or the reply to our probe on port 7000) carries the new address; the link is re-created immediately. |
| Device was off / unreachable | Reconnects with back-off (5 s → 5 min) and instantly when its broadcast is seen again. |
| Device re-paired in the app | The local key rejected twice in a row triggers a cloud refresh (rate-limited to once per 5 min); the device reconnects with the new key. New devices found during the refresh are added live. |
| Cloud down / credentials expired | Cached devices, keys and entities keep working. A re-auth prompt is raised when the session is invalid. |

## Entities

Entities are derived from the DP schema, following the semantics of Tuya's standard
instruction set (the same knowledge the core Home Assistant integration encodes):

| Platform | Built from |
|---|---|
| light | `switch_led*`, `bright_value*`, `temp_value*`, `colour_data*`, `work_mode`; multi-channel dimmers, floodlights, backlights, night lights, remote-style lights (`ykq`) |
| switch | writable booleans (`switch*`, `child_lock`, `anion`, camera settings…) |
| sensor | temperature, humidity, power/energy (incl. multi-channel), air quality, battery, weather stations, water/pool testers, vacuum consumables… |
| binary_sensor | door/motion/presence/leak/gas/smoke/CO/tamper/fault/vibration… |
| number / select | writable integers and enums (countdowns, sensitivities, volumes, power-on behaviour…) |
| cover | curtains (`control*`, `percent_*`, `situation_set`), curtain switches with motor direction (`control_back_mode`), curtain robots (`mach_operate`), tilt, Kogan blinds, garage openers |
| fan | `switch_fan`, `fan_speed*` (integer or enum), direction, oscillation, presets |
| climate | thermostats, radiator valves, heaters, air conditioners, water heaters: modes, presets, fan, swing, target humidity, °C/°F |
| humidifier | humidifiers (`jsq`) and dehumidifiers (`cs`) |
| vacuum | robot vacuums (`sd`): start/stop/pause/dock/locate, suction, activity from `status` |
| valve | irrigation controllers (`ggq`, `sfkzq`), per zone |
| siren | `alarm_switch` / `siren_switch` |
| alarm_control_panel | alarm hubs (`mal`, `wg2`): `master_mode` / `master_state` |
| button | one-shot actions (`reset_filter`, `reset_map`, `device_restart`, …) |
| event | scene/wireless switches (`wxkg`: click / double click / long press per button) and doorbell messages/pictures; fired on pushed reports only |

Anything unknown falls back to a sensible generic entity by DP type, and a diagnostics
download shows the full schema and link state. Custom profiles: drop a YAML file in
`custom_components/tuya_eco/profiles/` keyed by product id (format documented in
`mapping/profiles.py`).

Services: `tuya_eco.set_dps` (raw DP write), `tuya_eco.refresh_cloud`, `tuya_eco.reconnect`.

## Protocol version

Nothing to configure. The version is taken from the device's discovery broadcast when
available, otherwise from the last version that worked, otherwise probed (3.3 → 3.4 → 3.5 →
3.1 → 3.2). A firmware update that changes the protocol (e.g. 3.3 → 3.5) is picked up
automatically on the next reconnect; with a broadcast it is immediate, without one it takes a
few seconds of probing. The detected version is remembered per device.

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements_dev.txt
```

```bash
.venv/Scripts/python -m pytest -q
```

The protocol and device tests run against an in-process emulation of a Tuya device
(`tests/fake_device.py`) covering protocol 3.1 – 3.5, so no hardware is needed.

## Acknowledgements

Tuya Eco contains no code copied from other projects, but it would not exist without the
reverse-engineering and the years of maintenance behind them. Projects whose work was used
directly:

| Project | What we took from it |
|---|---|
| [jasonacox/tinytuya](https://github.com/jasonacox/tinytuya) (MIT) | The reference for the LAN wire format (55AA/6699 framing, 3.4/3.5 session-key handshake, device22 quirks, UDP discovery). Its output generated the golden vectors in `tests/test_wire.py` and the first version of this integration used its packing helpers. |
| [rospogrigio/localtuya](https://github.com/rospogrigio/localtuya) and the [xZetsubou/hass-localtuya](https://github.com/xZetsubou/hass-localtuya) fork (GPL-3.0) | The asyncio protocol design (dispatcher keyed by sequence number, sentinel keys for heartbeat/session replies), the passive IP re-resolution from discovery broadcasts and the reconnect-on-broadcast idea. |
| [make-all/tuya-local](https://github.com/make-all/tuya-local) (MIT) | Reconnect/back-off strategy, protocol-version rotation, the pending-update overlay for optimistic state, the YAML profile idea and many DP-code semantics. |
| [home-assistant/core — tuya](https://github.com/home-assistant/core/tree/dev/homeassistant/components/tuya) (Apache-2.0) | The QR login flow through the device-sharing API, the category/DP-code knowledge behind the entity mapping, and the colour-data encodings. |
| [tuya/tuya-device-sharing-sdk](https://github.com/tuya/tuya-device-sharing-sdk) (MIT) | Used as a dependency for the Smart Life / Tuya Smart login and device list. |
| [vineetchoudhary/tuya-local-key](https://github.com/vineetchoudhary/tuya-local-key) | Confirmed that the device-sharing API returns local keys and documented its limits for OEM apps. |
| [FlagX/ha-ledvance-tuya-resync-localkey](https://github.com/FlagX/ha-ledvance-tuya-resync-localkey) (MIT) | The Tuya mobile-app API route for Ledvance SMART+: request signing, login and the app identifiers. |
| [buggedcom/hass-ledvance](https://github.com/buggedcom/hass-ledvance) / [terkon28/hass-ledvance](https://github.com/terkon28/hass-ledvance) | A clean Python client of the same API (regions, session refresh, textbook RSA), used as the reference while porting it to aiohttp. |
| [Kosztyk/homebridge-ledvance-native](https://github.com/Kosztyk/homebridge-ledvance-native) | The silent `tuya.m.user.session.update` refresh and rate-limit handling. |
| [rgsilva — Reverse engineering Positivo's smart home app](https://rgsilva.com/blog/reverse-engineering-positivos-smart-home-app/) and [nalajcie/tuya-sign-hacking](https://github.com/nalajcie/tuya-sign-hacking) | The original analysis of the mobile-app signature scheme. |
| [tuya-convert](https://github.com/ct-Open-Source/tuya-convert) | The key of the encrypted discovery broadcasts. |
| [codetheweb/tuyapi](https://github.com/codetheweb/tuyapi) | Early documentation of the LAN protocol and of why OEM apps cannot be linked the normal way. |

Thanks to all of them.

## License

MIT
