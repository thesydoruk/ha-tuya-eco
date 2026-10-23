# Tuya Eco for Home Assistant

Local control of the **Tuya ecosystem** — Smart Life / Tuya Smart, **Ledvance SMART+ WiFi**,
Sylvania Smart WiFi, Tuya IoT Platform projects and any other white-label Tuya app — from a
single, modular integration.

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
| Tuya IoT Platform | cloud project access id / secret | OpenAPI device list + specifications (via `tinytuya.Cloud`) |
| Other Tuya OEM app | e-mail + password + the app's client id / secret | Tuya mobile-app API |
| Manual | device id + local key | you already have the key (e.g. shown in a newer SMART+ app) |

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → *Custom repositories* → add `https://github.com/thesydoruk/ha-tuya-eco`
   as *Integration*.
2. Install **Tuya Eco** and restart Home Assistant.

### Manual

Copy `custom_components/tuya_eco` into `<config>/custom_components/` and restart.

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

Entities are derived from the DP schema: lights (white / colour temperature / HS colour, Tuya
v1 and v2 colour encodings, multi-channel dimmers), switches, sensors (power, energy,
temperature, humidity, air quality, battery…), binary sensors, numbers, selects, covers
(including garage openers), fans and climates. Anything unknown falls back to a sensible
generic entity by DP type, and a diagnostics download shows the full schema and link state.

Custom profiles: drop a YAML file in `custom_components/tuya_eco/profiles/` keyed by product id
(format documented in `mapping/profiles.py`).

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements_dev.txt
```

```bash
.venv/Scripts/python -m pytest -q
```

The protocol and device tests run against an in-process emulation of a Tuya device
(`tests/fake_device.py`) covering protocol 3.1 – 3.5, so no hardware is needed.

## Credits

Built on the knowledge of [tinytuya](https://github.com/jasonacox/tinytuya) (MIT; its packing
and crypto helpers are used directly), [localtuya](https://github.com/rospogrigio/localtuya),
[tuya-local](https://github.com/make-all/tuya-local), the core Home Assistant Tuya integration
and the Ledvance local-key work of [FlagX](https://github.com/FlagX/ha-ledvance-tuya-resync-localkey).

## License

MIT
