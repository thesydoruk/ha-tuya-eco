"""Known white-label Tuya app profiles.

The identifiers below are the public application ids of the vendor apps (they
ship inside every copy of the app and are documented in several open-source
projects: FlagX/ha-ledvance-tuya-resync-localkey, hass-ledvance,
homebridge-ledvance-native, localtuya PR #1131/#2301). They are *not* user secrets.
"""

from __future__ import annotations

from .tuya_oem import OemAppProfile

LEDVANCE = OemAppProfile(
    key="ledvance",
    name="Ledvance SMART+ WiFi",
    client_id="fx3fvkvusmw45d7jn8xh",
    secret="A_armptsqyfpxa4ftvtc739ardncett3uy_cgqx3ku34mh5qdesd7fcaru3gx7tyurr",
    regions=("EU", "US", "CN", "IN"),
    default_region="EU",
)

SYLVANIA = OemAppProfile(
    key="sylvania",
    name="Sylvania Smart WiFi",
    client_id="creq75hn4vdg5qvrgryp",
    secret="A_ag4xcmp9rjttkj9yf9e8c3wfxry7yr44_wparh3scdv8dc7rrnuegaf9mqmn4snpk",
    regions=("US", "EU"),
    default_region="US",
)

PROFILES: dict[str, OemAppProfile] = {
    LEDVANCE.key: LEDVANCE,
    SYLVANIA.key: SYLVANIA,
}
