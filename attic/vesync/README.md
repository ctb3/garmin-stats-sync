# VeSync attic

Dead code, kept for reference. **Nothing here is imported by the application.**

`pyproject.toml` builds only `packages = ["garmin_stats_sync"]` and pytest runs only
`testpaths = ["tests"]`, so this directory stays inert without any extra exclusion.

## Why it's here

The service originally polled VeSync's cloud for weigh-ins. **That path never worked for
this scale.** It was replaced by an Android app that reads the same weigh-ins out of
Health Connect and pushes them to the service.

## What was learned

The scale is an **Etekcity ESF-93 V2**, a Bluetooth-only device. That single fact explains
every failure below.

- **`pyvesync` has never supported smart scales.** [PR #108](https://github.com/webdjoe/pyvesync/pull/108/files)
  added ESF24 support and was never merged. The library was used only for login, token
  caching and region handling; the device list and weigh-in history were raw calls through
  its authenticated session.
- **No weigh-in endpoint ever returned `code=0`.** Endpoints tried:
  `/cloud/v2/deviceManaged/getWeighingDataV2`,
  `/cloud/v1/deviceManaged/fatScale/getWeighData`, and variants. See `vesync-api.md` for
  the full matrix of bodies, endpoints and error codes.
- **`bypassV2` reached the device layer and refused with "user does not have permission"** —
  consistent with a BT device that has no `cid`. A Bluetooth scale has no cloud device
  record to address the way a WiFi plug does.
- **Sub-user discovery failed on all four guessed endpoints** (`fatScale/getSubUserList`,
  `fatScale/getAllSubUser`, `user/getSubUserList` v1 and v2), so the real `subUserID` was
  never found. A wrong or missing `subUserID` is a plausible cause of `illegal argument`.
- **`-11105079` "MySQL error"** meant a server-side blowup, i.e. the wrong endpoint for this
  device class rather than a fixable request body.

## The lesson that generalises

A Bluetooth-only VeSync device appears in `/cloud/v1/deviceManaged/devices` but its
measurements are not necessarily addressable through the device-scoped cloud API. The phone
app is the only component that talks to the scale, so the phone is the right place to read
from — which is what Health Connect gives us, with a documented and stable API.

## Contents

| Path | What it was |
|---|---|
| `vesync-api.md` | Full API notes: endpoints, bodies, error codes, ideas not yet tried |
| `vesync_client.py` | Authenticated session + weigh-in fetch, async behind a sync facade |
| `mapping.py` | `getWeighingDataV2` payload → `Reading` |
| `scripts/` | Endpoint diagnosis, session dumping, and by-hand probing |
| `insomnia/` | Insomnia collection for manual exploration |
| `tests/`, `fixtures/` | The tests and recorded payload that went with the above |

Note that `mapping.py` and the tests reference a `Reading` with a `sub_user_id` field. That
field was a VeSync concept with no Health Connect equivalent and has since been removed from
`garmin_stats_sync/models.py`, so this code will not run unmodified against the current
package.
