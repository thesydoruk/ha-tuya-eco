# Tuya Eco — architecture

Tuya Eco is a Home Assistant custom integration for devices of the Tuya ecosystem
(Smart Life / Tuya Smart, Ledvance SMART+, and any other Tuya OEM app in the future).
It controls devices **locally** (LAN protocol 3.1–3.5) and uses the vendor cloud only to
obtain device metadata: `local_key`, data-point (DP) schema and names.

```
┌───────────────────────────────────────────────────────────────────────┐
│ Home Assistant config entry (one per provider account)                │
│                                                                       │
│  ┌──────────────┐   devices + keys + DP schema   ┌─────────────────┐  │
│  │  Provider     │ ─────────────────────────────▶ │   EcoHub        │  │
│  │  (cloud)      │ ◀───────────────────────────── │  (per entry)    │  │
│  └──────────────┘   refresh on auth failure       └────────┬────────┘  │
│   smartlife / ledvance / tuya_iot / …                      │           │
│                                                            ▼           │
│  ┌──────────────┐  ip/version   ┌─────────────────────────────────┐    │
│  │ Discovery    │ ────────────▶ │  EcoDevice (one per device)     │    │
│  │ UDP 6666/    │               │  • TuyaLanProtocol (asyncio)    │    │
│  │ 6667/7000    │               │  • DP state + dp⇄code mapping   │    │
│  └──────────────┘               │  • reconnect / key refresh      │    │
│                                 └───────────────┬─────────────────┘    │
│                                                 │ entity descriptions   │
│                                                 ▼                       │
│                     switch · light · sensor · binary_sensor · number    │
│                     select · cover · fan · climate · button             │
└───────────────────────────────────────────────────────────────────────┘
```

## Layers

### 1. Providers (`providers/`)

A provider is the adapter for one cloud/app ecosystem. It knows how to log in,
how to list devices, and how to obtain the `local_key` and DP schema for each device.
Providers are registered in `providers/registry.py` and selected in the config flow.

```python
class BaseProvider(ABC):
    key: str                # "smartlife", "ledvance", "tuya_iot"
    name: str               # shown in UI
    login_kind: LoginKind   # QR_CODE | CREDENTIALS | API_KEYS

    async def async_start_login(self, user_input) -> LoginStep
    async def async_finish_login(self, state) -> ProviderSession
    async def async_fetch_devices(self) -> list[CloudDevice]
    def to_entry_data(self) -> dict
    @classmethod
    def from_entry_data(cls, hass, data) -> BaseProvider
```

`CloudDevice` is a provider-agnostic dataclass:

| field              | meaning                                                                  |
|--------------------|--------------------------------------------------------------------------|
| `id`               | Tuya device id (`gwId`)                                                   |
| `name`, `category`, `product_id`, `product_name`, `model`, `icon`         |
| `local_key`        | AES key for the LAN protocol                                              |
| `ip`, `online`     | last known cloud data (hint only, LAN discovery is authoritative)         |
| `dp_schema`        | `dict[int, DpSpec]` — DP id → code, type (`Boolean/Integer/Enum/String/Json/Raw/Bitmap`), values spec, read-only flag |
| `status`           | last cloud status by code (used as initial state before LAN connects)     |

Implemented providers:

* **smartlife** – Smart Life / Tuya Smart. QR login through `tuya-device-sharing-sdk`
  (same mechanism as the core HA integration). The sharing API returns `local_key` and
  `local_strategy` (DP id → code map) for every device.
* **ledvance** – Ledvance SMART+ WiFi. See `docs/LEDVANCE.md` for the key-retrieval path.
* **tuya_iot** – Tuya IoT Platform (Cloud project `client_id`/`secret`) through
  the integration's own OpenAPI client (`providers/tuya_openapi.py`).
* **sylvania**, **tuya_oem** – the same mobile-app API as Ledvance with another app profile,
  or with identifiers the user pastes.
* **manual** – device id + local key entered by hand; the DP schema is inferred from the first
  status poll.

Adding a provider = one new module in `providers/` and one line in the registry.

### 2. Local engine (`local/`)

* `protocol.py` – `TuyaLanProtocol`: a native `asyncio.Protocol` implementation of the Tuya
  LAN protocol 3.1 – 3.5 (55AA/6699 framing, AES-ECB/GCM, HMAC, 3.4/3.5 session-key handshake,
  device22 query style). `wire.py` holds the framing and ciphers (built on `cryptography`);
  nothing blocks the event loop and no third-party Tuya library is involved. Heartbeats every 10 s, two misses close the link.
* `discovery.py` – asyncio UDP listener on ports 6666 (plain), 6667 (AES-ECB with the
  well-known key) and 7000 (3.5, GCM). Emits `(gwId, ip, version)` events. Shared by all hubs.
* `device.py` – `EcoDevice`: owns one `LocalLink`, the DP state and the reconnect state
  machine:

```
            ┌───────────┐ discovery / cloud ip  ┌────────────┐
   start ──▶│ RESOLVING │ ─────────────────────▶│ CONNECTING │
            └───────────┘                        └─────┬──────┘
                 ▲   ▲                                 │ ok
       no answer │   │ key error ×N → refresh key      ▼
                 │   │ from provider (re-paired)  ┌───────────┐
                 └───┴────────────────────────────│ CONNECTED │──▶ unavailable on
                      backoff 5s→10s→…→300s        └───────────┘    heartbeat loss
```

The three "re-connected device" cases are all handled automatically:

1. **Device got a new IP** (DHCP, router reboot): discovery packet with a known `gwId`
   and a different IP → link is re-created to the new address immediately.
2. **Device was offline** (power cut): exponential backoff reconnect, plus an instant
   retry when its discovery broadcast is seen again.
3. **Device was re-paired in the app** (new `local_key`): decryption/handshake failures
   trigger a provider refresh; if the key changed the device reconnects with the new key.
   New devices found during such refresh are added without a restart.

### 3. DP → entity mapping (`mapping/`)

Entities are generated from the DP schema, not from a hand-written per-product table:

1. `profiles/*.yaml` – optional explicit profiles keyed by `product_id` (highest priority).
   `mode: replace` (default) defines all entities by hand; `mode: extend` patches the cloud
   schema instead — it adds vendor DPs the cloud does not list (e.g. the tuning DPs of mmWave
   presence sensors), renames DPs the cloud maps wrongly, overrides enum ranges that differ on
   the LAN, and appends explicit entities for DPs the code tables cannot express (string-valued
   buttons and switches).
2. `mapping/categories.py` – rules per Tuya `category` + DP `code` (ported from the
   knowledge in the core HA Tuya integration: e.g. `switch_led` → light, `temp_current`
   → temperature sensor, `fan_speed` → fan percentage).
3. `mapping/generic.py` – fallback heuristics by DP type: Boolean writable → switch,
   Boolean read-only → binary_sensor, Integer writable → number, Integer read-only → sensor,
   Enum writable → select, Enum read-only → sensor.

Each rule produces an `EcoEntityDescription` (platform + HA entity description + DP ids).
The result is cached in the config entry so entities are stable across restarts even
when the cloud is unreachable.

Units in the cloud spec are normalised by `codes.clean_unit` (`lux` → `lx`, `℃` → `°C`;
placeholders such as `x` are dropped). Bitmap DPs that become binary sensors (`fault`)
keep their bit labels so the entity can list the active faults as an attribute. Lights
with a raw `power_memory` DP get a *Power-on behavior* select (`mapping/types.py`
`PowerMemory` is the 12-byte codec: mode byte + custom HSV / brightness / colour
temperature).

The builder always appends a diagnostic **connection** sensor per device. Besides the
transport in use its attributes carry the LAN host, protocol version, last error, last
update and `unmapped_dps`: the raw values of every DP reported by the device that no
entity uses (keyed by code when the schema knows the DP, else `dp_<id>`). That is the
first place to look when a device has settings the integration does not show yet — the
DP ids there go straight into an extending profile.

### 4. HA glue

* `hub.py` – `EcoHub`: per config-entry manager. Loads the provider session, builds
  `EcoDevice`s, subscribes to discovery, schedules the periodic cloud refresh
  (default 12 h, plus on-demand), registers devices in the device registry.
* `entity.py` – `EcoEntity` base: availability from the device, DP read/write helpers,
  value scaling (`scale`, `step`, `min`, `max`), enum mapping.
* `config_flow.py` – provider selection → provider-specific login steps → options
  (polling interval, cloud refresh interval, per-device protocol override).
* `diagnostics.py` – redacted dump of devices, DP schema and link state.

## Design rules

* The cloud is never on the hot path. If the cloud is down, everything keeps working from
  the cached entry data.
* The LAN side is pure asyncio. Only the synchronous sharing SDK runs in executor threads.
* A provider must not import HA platform code; a platform must not import a provider.
* Everything that identifies a device is keyed by Tuya `device id`, never by IP.
* Protocol detection order: discovery broadcast version → last working version → provider hint →
  3.3, 3.4, 3.5, 3.1, 3.2. A TCP connect failure skips the remaining versions for that host.
