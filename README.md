# Ai-LiNK electric water heater · Home Assistant integration

Bring a **China-market A.O. Smith electric water heater** into Home Assistant through the official
Ai-LiNK cloud (the "AI家智控 / AI-LiNK 智慧家" app). Target temperature, power, instant heating,
disinfection, off-peak scheduling, warm holding and capacity boost — all bridgeable to Apple Home
via the HomeKit Bridge.

- Supported family: AI-LiNK electric water heaters such as `EWH-*HGAWi` (the official web client has
  branches for `isHGA`, `isHGS`, `isHGX`, `isE9W`, `isBPW`, `isHT5`, `isPEorNPE`, …)
- Tested on: **EWH-HGAWi** (`productMajorClassCode = 17`)
- **One capture is enough**: the token renews itself, including after it expires (see below)

## Why this integration exists

| Option | Works with a China-market electric water heater? |
| --- | --- |
| HA core `aosmith` integration | ❌ North-American iCOMM accounts only |
| Community Ai-LiNK integrations (`mopocv`, `Doker9527`, `mylofsh`, `gzzwozuiai`) | ❌ gas water heaters (hardcoded `19`) or a purifier/heater combo (`21`) — completely different command set |
| **This integration** | ✅ uses the electric water heater service `SetElectricWaterHeater` |

The protocol was reverse engineered from the official web client
(`ailink-appservice-h5-prd.hotwater.com.cn`, route `/ElectricWaterHeater`); field names and state
logic match the app, and the command payloads were verified byte-for-byte against a real capture
(`{"Temperature":"59"}`, `{"powerStatus":"1"}`).

## How it differs from the sibling projects

Everyone on this cloud captures a token by hand; the difference is how far the implementation goes.

| Aspect | The gas-water-heater projects | This integration |
| --- | --- | --- |
| Device discovery | hardcoded `deviceCategory == "19"` | **dynamic** — lists every device of the account, showing class code and model |
| Token expiry | manual re-authentication only (their READMEs state that automatic token refresh is deliberately not implemented), so users re-capture | **proactive sync + self-healing + never gives up** (below) |
| Auth failure | raises `ConfigEntryAuthFailed` → HA stops polling | raises `UpdateFailed` → **keeps polling so it can heal itself** |
| Command confirmation | raises on a failed read-back | logs a warning (avoids false alarms when two commands overwrite each other within a second) |
| Signature | some projects skip signing and use a hardcoded `encode` captured from the wire | implements the official algorithm; measured that the server currently does not verify signature or `encode` values, but keeps sending them in case verification returns |

## Token handling: one capture, then it looks after itself

The token is a short-lived JWT (~30 minutes), and the cloud keeps the account's **newest** token
around. Everything below was measured on a real account and device (2026-09-16):

1. **Proactive sync** — within 12 minutes of `exp`, ask
   `POST /AiLinkService/api/getLastToken` every 3 minutes and adopt whatever the account has newest
   (this picks up tokens minted by the phone app);
2. **After expiry, slower** — once `exp` has passed the interval widens to 30 minutes; the JWT `exp`
   is **not enforced** by the server, so there is no rush;
3. **Self-minting** — if nothing newer exists on the server, the cloud still hands out a **brand new
   token in the `Authorization` response header** of a few endpoints. That is exactly how the phone
   app gets its tokens, and this integration calls
   `POST /AiLinkService//appDevice/getAntifreeze` with the current (expired) token to obtain one.
   Verified: `HTTP 200` plus a fresh `Authorization` header, the new token works, and the account's
   "newest token" record moves to it. **The phone app is no longer needed at all.**
4. **Failures do not stop the integration** — a stale token raises `UpdateFailed`, not
   `ConfigEntryAuthFailed`, so polling continues and the renewal above keeps retrying. Throttling is
   time-based only; there is a unit test asserting that after four consecutive failures the fifth
   attempt still succeeds;
5. **Fallback** — "Reconfigure" / "Re-authenticate" lets you paste a new token if you ever need to.

Renewed tokens are written back to the config entry, so restarts keep them. `token_expires_at` is
exposed as an attribute of the water heater entity for observation.

### Measurements (2026-09-16, EWH-HGAWi)

| Observation | Result |
| --- | --- |
| token `exp` | 29 minutes after the capture |
| **30 minutes / 10 hours after `exp`** | **still fully usable** (reading and commanding) → the cloud does not enforce the JWT `exp`; it validates its server-side session record |
| `getLastToken` with an old token | returns the account's **newest** token (the same one when the app has not rotated) |
| `999999` | two distinct meanings: `{"msg":"校验失败"}` = a required signed header (`timestamp`/`nonce`/`md5data`/`sign`) is **missing**; `{"msg":"您的设备系统时间不准确，请校准后使用！"}` = the `timestamp` header is **outside the server's time window** (accepted at −15 min, rejected at −40 min). A deliberately wrong `sign` value is accepted. |
| Password login | **does not exist**: `/user/login` accepts only `mobile` + `captcha`; anything with `password`/`pwd`/`account` returns `参数缺失`, and eight plausible password-login endpoint names all return 404 |
| SMS login | exists, but the server verifies the Tencent captcha ticket server-side (a fake ticket returns `验证失败`), so it cannot be automated — this is the emergency path only |
| Conclusion | the frequently repeated "capture again every 30 minutes" is a misunderstanding |

## Installation

### HACS (recommended)

1. HACS → three-dot menu → **Custom repositories**
2. Add this repository URL, category **Integration**
3. Search for "Ai-LiNK" and install, then restart Home Assistant

### Manual

Copy the `custom_components/ailink_ewh` directory into your Home Assistant
`config/custom_components/` directory and restart.

## 1. Capture (once)

Four values are needed. Run a capture tool on the phone (Stream or Charles on iOS, HttpCanary on
Android), let the phone's traffic go through the proxy, open the AI家智控 app and visit the water
heater detail page a few times.

Find the requests to **`ailink-api.hotwater.com.cn`** and read:

| Value | Where it comes from |
| --- | --- |
| `access_token` | request header `Authorization`, without the leading `Bearer ` |
| `user_id` | request header `UserId` |
| `family_id` | `familyId` in the JSON request body |
| `cookie` (optional) | request header `Cookie` |

> ⚠️ Turn the phone proxy off immediately afterwards, otherwise the app reports "server error / -1004".
> ⚠️ The token is a login credential for your account — do not share it. Diagnostics redact it.

**Verify before installing** (optional but recommended):

```bash
pip install aiohttp
python3 tools/probe.py --token 'eyJ...' --user-id YOUR_USER_ID --family-id YOUR_FAMILY_ID --renew
```

It lists the account's devices, prints every reported field, and tests token renewal.

## 2. Configure

Settings → Devices & services → Add integration → search for **Ai-LiNK** → paste the four values →
pick your water heater from the list (the list shows each device's class code and model).

## Entities

| Entity | Content |
| --- | --- |
| `water_heater` | power, target temperature, actual water temperature, work state, plus every raw field as attributes |
| `sensor` | actual water temperature (`realTemp`) |
| `sensor` | work state: off / heating / scheduled / holding (same derivation as the official app) |
| `sensor` | fault message (fault and warning codes with their text) |
| `binary_sensor` | heating (device class `running`) / fault (device class `problem`) |
| `switch` | instant heating, disinfection, AES eco, off-peak window, warm holding, capacity boost — only those the device actually reports |
| `select` | heating mode — single tank / dual tank / winter large volume (`workModel`, written with `HeaterMode`) |

## Options

Settings → Devices & services → the integration → Configure:

| Option | Default | Notes |
| --- | --- | --- |
| Poll interval | 60 s | 10–900 |
| Min / max target temperature | 35 / 75 °C | some models (e.g. the HGX instant-heating family) reach 85 °C in their second work mode |
| Expose every raw field | on | turn off to keep only the key attributes |
| Create all switch entities | off | for devices that do not report a field on the first poll |

## Apple Home / HomeKit

In the HomeKit Bridge config pick:

- the `water_heater` entity → thermostat (power + target temperature)
- the water temperature `sensor` → temperature sensor
- the switches you care about

## Known limitations

- Scheduled heating (`TimerOne` / `TimerTwo` / `CountdownOne`), the off-peak time window and the warm
  holding temperature need writes that carry a time range; only the on/off switches are exposed today —  use the official app for the time ranges.
- The cloud API is private and can change without notice. If you see `999999`, include the HA log in an
  issue (the integration redacts tokens).
- The account's "family" (`familyId`) is part of the credentials; changing family requires reconfiguring.
- In the *winter large volume* heating mode (`workModel = 4`) the device ignores target temperatures and
  refuses to switch capacity boost off — the official app hides/blocks both as well. Home Assistant logs a
  warning instead of failing, because switching to another heating mode makes the same call work.
- The heating mode list is defined per model family in the vendor app. This integration names the three
  modes of the models it was verified on (`1` / `2` / `4`); other families reuse the same field with their
  own labels and values (the third one is `0` for PE/NPE, E9W, BPW and D1, `3` for 50FW), so a mode that is
  not in that table is shown as `mode_<n>` and can still be selected again.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| "Invalid authentication" while adding the integration | wrong or expired token, or `user_id` / `family_id` mismatch. The cloud does not return an error for unusable credentials — it answers `HTTP 200` with an empty device list, and the integration treats that as an auth problem |
| Entities stay unavailable | device offline (it will also be missing in the app) or the token was rejected |
| "Command was accepted by the cloud but the device did not report … within 10s" | the command was accepted but the device did not report back; usually it applies shortly after. Two commands within a second overwrite each other and produce this warning by design |

## Development / testing

```bash
python3 -m venv .venv && .venv/bin/pip install aiohttp
.venv/bin/python -m unittest discover -s tests -v   # 17 offline tests (signing, status decoding, renewal)

# capture helper: list devices / dump every reported field / test token renewal
.venv/bin/python tools/probe.py --token 'eyJ…' --user-id … --family-id … --renew

# token watchdog: knock every 30 minutes and record whether the token still works
.venv/bin/python tools/token_watchdog.py --interval 1800 --days 7 --log .token-watchdog.log

# scan a HAR capture for tokens and rotations
.venv/bin/python tools/har_scan.py capture.har

# verify that we can mint a token ourselves (waits until the current one expired)
.venv/bin/python tools/mint_probe.py --device-id <id> --at HH:MM:SS --log /tmp/mint_probe.log

# deploy to a Home Assistant on another host (add --restart to restart it)
HA_HOST=root@ha.local HA_CONFIG=/opt/homeassistant/config ./scripts/deploy.sh --restart
```

### Reproducing the protocol research

The device detail page is a web view, and that front end can simply be read:

```bash
base=https://ailink-appservice-h5-prd.hotwater.com.cn/dist
curl -s $base/config.js                      # BASE_API
curl -s $base/index.html                     # script list
curl -s $base/js/runtime.*.js | grep -o '7700:"[a-f0-9]*"'   # chunk map (7700 = ElectricWaterHeater)
curl -s $base/js/ElectricWaterHeater.<hash>.js | grep -o 'SetElectricWaterHeater'
```

## Disclaimer

- Unofficial, not affiliated with A.O. Smith. For personal use and study.
- The API was obtained by capturing and reverse engineering traffic; the vendor may change it at any
  time and there is no guarantee of continued operation.
- Keep your `access_token` private — it is equivalent to a login credential. This integration never
  sends it anywhere except the vendor's own cloud.

## Credits

This work stands on the protocol research of
[mopocv/Ai-Link_A.O.Smith](https://github.com/mopocv/Ai-Link_A.O.Smith),
[Doker9527/Ai-Link-AOSmith-HA](https://github.com/Doker9527/Ai-Link-AOSmith-HA) (signature fix),
[xiaoyawei](https://github.com/xiaoyawei) (the H5 signing idea) and
[gzzwozuiai/ha_aosmith_water_heater](https://github.com/gzzwozuiai/ha_aosmith_water_heater)
(another device class on the same cloud).

## License

MIT
