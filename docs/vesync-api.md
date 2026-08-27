# VeSync cloud API notes

Everything this project knows about the unofficial VeSync API, written down so
the weigh-in endpoint can be hunted by hand (Insomnia, curl, whatever).

All of it was derived from reading [pyvesync](https://github.com/webdjoe/pyvesync)
3.4.2's source and from live responses on a real account with an
**Etekcity ESF-93 V2** Bluetooth scale.

## Base URL

| Region | Base URL |
|---|---|
| US / everything except EU | `https://smartapi.vesync.com` |
| EU | `https://smartapi.vesync.eu` |

Every call below is `POST` with a JSON body. The region comes back from login
(`currentRegion`); a wrong region returns a cross-region error telling you which
one to use.

## Authentication (two steps)

pyvesync 3.x no longer uses the old `/cloud/v1/user/login` endpoint. Login is
now an authorization-code exchange.

### Step 1 — get an authorization code

`POST /globalPlatform/api/accountAuth/v1/authByPWDOrOTM`

```json
{
  "email": "you@example.com",
  "method": "authByPWDOrOTM",
  "password": "<md5 hex of your password, lowercase>",
  "acceptLanguage": "en",
  "accountID": "",
  "authProtocolType": "generic",
  "clientInfo": "SM N9005",
  "clientType": "vesyncApp",
  "clientVersion": "VeSync 5.6.60",
  "debugMode": false,
  "osInfo": "Android",
  "terminalId": "29c2f003b88bd5bc7b87c54007dd28851",
  "timeZone": "America/New_York",
  "token": "",
  "userCountryCode": "US",
  "appID": "eldodkfj",
  "sourceAppID": "eldodkfj",
  "traceId": "<unix seconds as a string>"
}
```

The password is **MD5, not the plaintext**:

```bash
printf '%s' 'your-password' | md5sum
```

Response carries `result.accountID` and `result.authorizeCode`.

### Step 2 — exchange it for a token

`POST /user/api/accountManage/v1/loginByAuthorizeCode4Vesync`

```json
{
  "method": "loginByAuthorizeCode4Vesync",
  "authorizeCode": "<from step 1>",
  "acceptLanguage": "en",
  "accountID": "",
  "clientInfo": "SM N9005",
  "clientType": "vesyncApp",
  "clientVersion": "VeSync 5.6.60",
  "debugMode": false,
  "emailSubscriptions": false,
  "osInfo": "Android",
  "terminalId": "29c2f003b88bd5bc7b87c54007dd28851",
  "timeZone": "America/New_York",
  "token": "",
  "userCountryCode": "US",
  "traceId": "<unix seconds as a string>"
}
```

Response carries `result.token`, `result.accountID`, `result.countryCode`,
`result.currentRegion`.

Shortcut: `scripts/dump_session.py` does both steps and prints the token,
account id and a ready-to-paste Insomnia environment.

## Headers

Two header sets are in play. Both work for the device list; neither changes the
weigh-in errors.

**Legacy** (`Helpers.req_legacy_headers`) — what PR #108 used for the scale:

```
accept-language: en
accountId: <accountID>
appVersion: 5.6.60
content-type: application/json
tk: <token>
tz: America/New_York
```

**Bypass** (`Helpers.req_header_bypass`) — what pyvesync uses for the device list:

```
Content-Type: application/json; charset=UTF-8
User-Agent: okhttp/3.12.1
```

Note that most endpoints *also* want `accountID` and `token` **in the body**,
not just the headers.

## Standard body fields

pyvesync builds a common body for device calls (`Helpers.req_body(manager, "devicedetail")`):

```json
{
  "timeZone": "America/New_York",
  "acceptLanguage": "en",
  "accountID": "<accountID>",
  "token": "<token>",
  "appVersion": "5.6.60",
  "phoneBrand": "SM N9005",
  "phoneOS": "Android",
  "traceId": "<unix seconds as a string>",
  "method": "devicedetail",
  "mobileId": "1234567890123456"
}
```

Every weigh-in attempt below is that body with `method` swapped and extra keys added.

## Device list (works)

`POST /cloud/v1/deviceManaged/devices` with `method: "devices"`, `pageNo: "1"`,
`pageSize: "100"`.

The scale on this account comes back as:

```json
{
  "deviceRegion": "US",
  "isOwner": true,
  "deviceName": "scale",
  "cid": null,
  "deviceStatus": "off",
  "connectionStatus": "offline",
  "connectionType": "BT",
  "deviceType": "ESF-93 V2",
  "type": "BT-Scale",
  "uuid": "<the scale's BLE MAC, e.g. CF:E8:05:22:02:CD>",
  "configModule": "VS_BT_SCL_ESF-93-V2_US",
  "macID": "<same as uuid>",
  "currentFirmVersion": "1.0.03",
  "subDeviceNo": null,
  "deviceFirstSetupTime": "Dec 23, 2024 2:34:29 PM"
}
```

**`cid` is null and `connectionType` is `BT`.** This is the crux: the scale is
not a networked device, so endpoints that join on `cid` have nothing to join to.

## Weigh-in retrieval (unsolved)

### What is documented elsewhere

- [pyvesync PR #108](https://github.com/webdjoe/pyvesync/pull/108/files) (ESF24, never merged):
  `POST /cloud/v2/deviceManaged/getWeighingDataV2` with the standard body plus
  `pageSize: 100`, `page: 1`, `debugMode: false`, `allData: true`,
  `method: "getWeighingDataV2"`, `configModule: <configModule>`.
  Response: `result.weightDatas[]`, each row `{timestamp, weightG, subUserID}`.
- [pyvesync issue #56](https://github.com/webdjoe/pyvesync/issues/56) (ESF00+):
  mentions `/cloud/v1/deviceManaged/fatScale/getWeighData` from app traffic, with
  no body captured.

`gh search code` finds **zero** public code using either endpoint name.

### What actually happens on an ESF-93 V2

56 combinations tried: 8 endpoint names x 6 body shapes, plus 4 `bypassV2`
wrappers, each with legacy/bypass/no headers.

| Response | Count | Where |
|---|---|---|
| `-11000079` illegal argument | 12 | `fatScale/getWeighData`, `fatScale/getWeighDataV2` |
| `-11002029` the user does not have permission | 4 | `bypassV2` wrappers |
| `-11102086` internal error | 36 | everything else |
| `-11105079` MySQL error | — | `getWeighingDataV2` (seen on earlier runs) |

Read those as three different walls:

- **illegal argument** — the endpoint exists and validated the request. It is
  rejecting the *arguments*, so the path is right and some required key is
  missing or misnamed. **This is the thread worth pulling.**
- **the user does not have permission** — `bypassV2` reached the device layer
  and refused, consistent with a BT device that has no `cid`.
- **MySQL error / internal error** — server-side blowup, i.e. the wrong endpoint
  for this device class rather than a fixable body.

Body shapes already tried against every endpoint (all with and without the
standard fields above):

```
{configModule}
{uuid}
{uuid, configModule, subUserID: 0}
{uuid, page: 1, pageSize: 10, allData: true}
{uuid, subUserID: 0, startTime: <epoch>, endTime: <epoch>, page, pageSize}
{deviceId: uuid, configModel: configModule, subUserID: 0, page, pageSize}
```

Sub-user discovery also failed on all four guessed endpoints
(`fatScale/getSubUserList`, `fatScale/getAllSubUser`, `user/getSubUserList` v1
and v2), so the real `subUserID` is still unknown — and a wrong or missing
`subUserID` is a plausible cause of `illegal argument`.

### Ideas worth trying by hand

Aim at `/cloud/v1/deviceManaged/fatScale/getWeighData`, since it validates:

- Vary the key naming: `subUserId` vs `subUserID`, `uuid` vs `deviceUuid` vs
  `deviceId`, `configModule` vs `configModel`.
- Try `weightUnit` (`"kg"` / `"lb"`), `dataType`, `weighingDataType`.
- Try pagination as strings (`"1"`, `"100"`) — the device-list endpoint wants
  strings there, which suggests the API is inconsistent about it.
- Try time ranges in milliseconds rather than seconds.
- Try `method` values that do not match the path segment, e.g. `getWeighData`
  on a `getAllWeighData` path.
- Look for a user-scoped rather than device-scoped path: the app has to render
  history per household member, so something like
  `/cloud/v1/user/...` or `.../fatScale/getSubUserWeighData` may exist.

### The definitive answer

Capture what the app sends. See the troubleshooting section in the README for
the mitmproxy recipe. The app is Android; it does not appear to pin
certificates, but that is unverified.

## Error codes seen

| Code | Message | Meaning |
|---|---|---|
| `0` | request success | it worked |
| `-11000079` | illegal argument | endpoint exists, body rejected |
| `-11002029` | the user does not have permission | wrong device class for that call |
| `-11102086` | internal error | server blew up, wrong endpoint |
| `-11105079` | MySQL error | server query failed, wrong endpoint |
| `-11102000` | token expired | re-login |

pyvesync's full table is in `pyvesync/utils/errors.py` (`ErrorCodes`).

## Gotchas

- `async_call_api` **raises** on any non-zero code rather than returning it, so
  probing through pyvesync hides the actual reply. Post through
  `manager.session` directly, as `scripts/diagnose_weigh_endpoints.py` does.
- `save_credentials`, `load_credentials_from_file` and `login` are all
  coroutines. An un-awaited call is a truthy coroutine object that silently
  does nothing.
- `Helpers.req_body` is deprecated but still the easiest way to build the
  common fields; it emits a `DeprecationWarning`.
