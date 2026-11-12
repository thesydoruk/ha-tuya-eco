# Ledvance SMART+ WiFi — how the local key is obtained

Ledvance SMART+ WiFi devices are ordinary Tuya devices, but the SMART+ app is a *white-label*
Tuya app with its own account pool. That rules out the two "official" routes:

| Route | Works for SMART+? | Why |
|---|---|---|
| Tuya IoT Platform (`iot.tuya.com`, *Link Tuya App Account* QR) | **No** | The SMART+ app has no QR scanner for it and is not on Tuya's allow-list. Re-pairing the devices into Smart Life usually fails for Ledvance hardware. |
| Device-sharing API (`tuya-device-sharing-sdk`, the core HA integration) | **No** | QR login only works for Smart Life / Tuya Smart accounts (`schema=haauthorize`). |
| **Tuya mobile-app API** (`a1.tuya{eu,us,cn,in}.com/api.json`) | **Yes** | The SMART+ app itself uses it. Logging in with the app's identity and the user's e-mail/password returns every device with `localKey`, the DP `schema` and `pv` (protocol version). |
| Key displayed in the app (SMART+ app ≥ 2025) | Partly | Some app versions show the local key under *Device → ⋮ → Home Assistant*. Use the **Manual** provider for that. |

The mobile-app route is what [FlagX/ha-ledvance-tuya-resync-localkey](https://github.com/FlagX/ha-ledvance-tuya-resync-localkey),
[hass-ledvance](https://github.com/terkon28/hass-ledvance), [homebridge-ledvance-native](https://github.com/Kosztyk/homebridge-ledvance-native)
and the open localtuya PRs #1131 / #2301 use. It has been working continuously since 2022.

## Protocol summary (implemented in `providers/tuya_oem.py`)

* `POST https://a1.tuyaeu.com/api.json?…` — all parameters in the query string, body
  `postData=<compact JSON>` as a form field, header `User-Agent: TY-UA=APP/Android/1.1.6/SDK/null`.
* Fixed parameters: `appVersion`, `appRnVersion`, `channel=oem`, `deviceId`, `platform`, `lang`,
  `osSystem`, `os`, `timeZoneId`, `ttid=sdk_tuya@<clientId>`, `et`, `sdkVersion`, `v`, `time`,
  `requestId`, `a=<action>`, optional `sid` and `gid`.
* Signature: the values of `a, v, lat, lon, lang, deviceId, imei, imsi, appVersion, ttid, isH5,
  h5Token, os, clientId, postData, time, requestId, n4h5, sid, sp, et` (non-empty ones), sorted
  by key, joined as `key=value` with `||`; `postData` is replaced by a rearranged MD5
  (`h[8:16]+h[0:8]+h[24:32]+h[16:24]`); `sign = HMAC-SHA256(app secret, string)`.
* Login: `tuya.m.user.email.token.create` → `{token, publicKey, exponent}`, then
  `tuya.m.user.email.password.login` with `passwd = "0"*64 + hex(RSA_textbook(md5hex(password)))`.
  Refresh with `tuya.m.user.session.update {refreshToken}` (no "new login" push).
* Devices: `tuya.m.location.list` → `groupId`s; `tuya.m.my.group.device.list&gid=…` →
  `[{devId, name, productId, localKey, schema, dps, pv, isOnline, localIp (WAN!), …}]`.
* Errors: `USER_SESSION_INVALID` (re-login), `USER_PASSWD_WRONG` / `USER_NOT_EXIST` (auth),
  `REQUEST_TOO_FREQUENTLY_PLEASE_TRY_AGAIN_LATER` / HTTP 429 (back off).

The provider throttles device-list refreshes to once per 5 minutes and prefers the silent
session refresh to avoid rate limits and login notifications.

## Cloud backup: the app's MQTT session (implemented in `providers/oem_mqtt.py`)

The SMART+ app keeps an MQTT session with the Tuya broker for live updates and commands. The
integration opens the same session so that a bulb that is unreachable on the LAN (Wi-Fi
trouble, VLAN, the host's network) stays visible and controllable through the cloud. It is a
**backup only**: while the LAN link is up, cloud reports are ignored and commands go over the
LAN. The *Cloud fallback* option switches it off.

Verified live with a SMART+ account (31 bulbs, protocol 2.2 and 2.3 firmware):

* Broker: `domain.mobileMqttsUrl:mqttsPort` from the login result (EU: `m1.tuyaeu.com:8883`), TLS.
* Credentials: username = `sid`, password = `md5(ecode)[8:24]`; `ecode` comes with the
  password login (the silent refresh may omit it, so it is stored with the entry). Client id
  `<package>_mb_<random per installation>_<md5(uid + "sdkfasodifca")>_DEFAULT`; a will message
  on `tuya/smart/will` like the app's.
* Topics: `smart/mb/in/<devId>` (reports) and `smart/mb/out/<devId>` (commands), one exact topic
  per device; wildcards are refused.
* Frames, keyed by the device's `localKey`:
  * `2.2`: `"2.2" | crc32(frame[7:]) | seq | origin | AES-128-ECB(json)` (big-endian);
  * `2.3`: `"2.3" | seq | origin | 0x00` (the 12 bytes are the GCM AAD) `| nonce(12) | ciphertext | tag(16)`.
* Messages: `protocol 4` → `data.dps` report; `protocol 1`, `type: "update"` with
  `data.online: false` when the device drops off. No "online" event was seen: any report counts as
  online.
* A bulb reports to the cloud when its state changes **and whenever the LAN side polls it** (the
  DP 30–33 reports every 30 s come from the integration's own poll). A bulb the LAN cannot reach
  is therefore silent on MQTT. Cached "online" flags are not trusted after the channel (re)connects,
  so 90 s later the devices that have not reported are asked about once through
  `tuya.m.device.get` (`isOnline`, at most 30 devices). Tested by blackholing one bulb's IP on the
  HA host: the bulb switched to `cloud` within 24 s and on/off from the HA UI reached it.
* Commands: `{"data":{"dps":{…}},"protocol":5,"t":<unix s>}` in the frame version the device itself
  sends (the cloud `pv` field is empty for these bulbs, so the version is learned from its
  reports); `seq` counts up per session, `origin` is random. The broker does not echo these
  publishes to other subscribers of `smart/mb/out/…`. PUBACK ≈ 40 ms, the device's report
  ≈ 90 ms later. Without a known version, for sub-devices, or without PUBACK the command goes
  through the HTTP `tuya.m.device.dp.publish` instead.

`scripts/probe_oem_push.py` reproduces the measurements (`--ha-config /config` inside the HA
container takes the account from the installed integration; `--blink <device>` tests commands).

## App identifiers

The SMART+ (and Sylvania) client id / secret live in `providers/oem_profiles.py`. They are the
application's own identifiers shipped inside every copy of the app and published by the
projects above; they are not user secrets. If Ledvance ever rotates them, replace the values
there — or use the *Other Tuya OEM app* provider and paste new ones in the UI.

## Known quirks

* Newer SMART+ bulbs/tubes speak protocol 3.5 — supported.
* `localIp` from the cloud is the WAN address; the integration ignores it and relies on LAN
  discovery.
* Some plugs report odd `localKey` formats; the key is used verbatim (latin-1).
* Firmware updates occasionally break local control until the device is power-cycled.
