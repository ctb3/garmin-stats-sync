# garmin-stats-sync

Copies body weight from Android Health Connect into Garmin Connect, unattended.

Weight only. Anything else Health Connect holds is out of scope.

```
Health Connect  --(Android app, every 30 min)-->  this service  -->  Garmin Connect
                        POST /weigh-ins                 |
                                                  spool on disk
```

Whatever writes weight into Health Connect — a smart scale's app, a manual entry —
is upstream of all this and none of its business.

## Why it is shaped this way

**Health Connect has no cloud and no API.** It is an on-device datastore readable
only from an Android app, so nothing server-side can pull from it. The phone pushes.

**Garmin has no public API**, and the unofficial route is anti-bot-hardened:
`garminconnect` gets in by impersonating a browser's TLS fingerprint, which a stock
Android HTTP stack cannot do. So the phone stays a dumb sender and the login lives
here.

## Reliability

- The phone advances its high-water mark **only after the server confirms a 2xx**,
  and reads everything back to that mark — no fixed window. Health Connect is the
  queue; the app keeps no second copy that could diverge from it.
- The server does not answer `200` until the reading is `fsync`'d to `/data/inbox/`.
- A spooled reading is deleted only on **proof of delivery** — presence in
  `state.json`'s `synced` list, written only after a successful Garmin upload.
- So a weigh-in arriving while the Garmin session is expired is accepted, held, and
  uploaded once you log in again. No re-send from the phone needed.

## Setup

One unprivileged Proxmox LXC. 1 core / 512 MB / 8 GB is plenty.

### 1. The container

Docker inside an unprivileged LXC needs **nesting** and **keyctl**:

```bash
# on the Proxmox host
pct set <vmid> --features nesting=1,keyctl=1 --onboot 1
pct reboot <vmid> && pct enter <vmid>
```

Install Docker:

```bash
apt update && apt install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt update && apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

docker run --rm hello-world      # must pass before continuing
```

If that fails with an overlayfs error — common when the LXC rootfs is on ZFS:

```bash
apt install -y fuse-overlayfs
mkdir -p /etc/docker
echo '{"storage-driver": "fuse-overlayfs"}' > /etc/docker/daemon.json
systemctl restart docker && docker run --rm hello-world
```

### 2. The service

```bash
git clone https://github.com/ctb3/garmin-stats-sync.git /opt/garmin-stats-sync
cd /opt/garmin-stats-sync
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # INGEST_TOKEN
nano .env          # INGEST_TOKEN and LOCAL_TZ are the two that matter
chmod 600 .env && mkdir -p data
docker compose up -d --build
```

Port 8080 is published on the host's addresses, so the phone reaches it at
`http://<lxc-ip>:8080`. Expect `ingest listening on 0.0.0.0:8080` in the logs.

Raise `COLD_START_DAYS` **before the first sync** if you want more than the last
7 days backfilled — after that the window is bounded by state, not the setting.

### 3. Log in to Garmin

Open `http://<lxc-ip>:8080/login` and sign in; MFA gets a second step. Nothing is
stored but the resulting tokens, which last about a year.

Setting `GARMIN_EMAIL`/`GARMIN_PASSWORD` in `.env` instead enables unattended
re-login, at the cost of a stored password. Both work; neither is required.

### 4. Prove it before trusting it

```bash
TOKEN=$(grep ^INGEST_TOKEN= .env | cut -d= -f2-)
curl -sS -X POST localhost:8080/weigh-ins \
  -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"records\":[{\"metadata\":{\"id\":\"manual-1\"},
       \"time\":$(($(date +%s)*1000)),\"weight\":{\"kilograms\":80.0}}]}"
```

Expect `{"accepted": 1, "rejected": []}` and the upload in the logs seconds later.
Check Garmin under **Health Stats → Weight**, then delete the test entry there.

### 5. The Android app

Download `garmin-sync-apk` from the latest
[CI run](https://github.com/ctb3/garmin-stats-sync/actions) and sideload it. It is a
signed release build; there is no Play listing, so no Health Connect declaration
form and no privacy policy.

On first launch it asks for the server address and `INGEST_TOKEN`. Grant **Weight**,
background access, and history when Health Connect asks — history is what stops
reads being capped at 30 days. Nothing else is requested.

The main screen shows the whole pipeline: token state, weigh-ins accepted but not
yet delivered, and recent runs, all in the phone's timezone. When Garmin needs a
login it says so and offers a button straight to the login page.

### 6. Survives a reboot

```bash
reboot            # inside the LXC
docker compose ps # expect "healthy"
```

`restart: unless-stopped` plus `--onboot 1`. The loop does its own timing.

## Reverse proxy (optional)

Only for a friendly hostname and TLS. A plain pass-through to the LXC's port 8080
is all it needs. Use a publicly-trusted certificate — the app then needs no custom
trust anchor.

With no auth at the proxy, anything on the network can read the status page. On a
home LAN that is usually the right trade; `INGEST_TOKEN` still guards the only path
with consequences outside it.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/weigh-ins` | `X-Auth-Token` header | Ingest from the phone |
| `GET` | `/` | none | Status page |
| `GET`/`POST` | `/login` | none | Garmin login and MFA |
| `GET` | `/status` | none | Full pipeline state as JSON |
| `GET`/`HEAD` | `/health` | none | For an uptime monitor |

The token is accepted **only** as a header, never a query string, so a browser page
cannot drive the endpoint without a preflight this service never answers.

Alert on `/health` for `token_state != "valid"`, a rising `pending`, or a stale
`last_success`. `token_state` is cached for 15 minutes because verifying it costs
two Garmin API calls; a login updates it immediately.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `INGEST_TOKEN` | — | Required, min 32 chars |
| `LOCAL_TZ` | `UTC` | Which calendar day a weigh-in lands on |
| `GARMIN_EMAIL` / `GARMIN_PASSWORD` | — | Optional; enables unattended re-login |
| `SYNC_INTERVAL_SECONDS` | `1800` | A floor — an arriving weigh-in wakes the loop |
| `COLD_START_DAYS` | `7` | Backfill limit when there is no state yet |
| `INBOX_RETENTION_DAYS` | `30` | How long an undelivered reading is kept |
| `HOST_PORT` | `8080` | Published port, if 8080 is taken |
| `PUBLIC_URL` | — | Used for the login link the app opens |

State lives in `/data`: `garth/` (Garmin tokens), `inbox/` (accepted, not yet
delivered), `state.json` (what has been delivered), `runlog.jsonl`. Delete
`state.json` to re-sync from scratch.

## Local development

### Service

```bash
uv sync                       # or: pip install -e ".[dev]"
pytest                        # 113 tests, no network

DATA_DIR=./data LOCAL_TZ=America/New_York DRY_RUN=1 \
  INGEST_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))") \
  python -m garmin_stats_sync loop -v
```

`DRY_RUN=1` logs what would be uploaded without touching Garmin. `sync` runs one
cycle instead of looping, and `--since 7d|all` overrides the window.

### Android app

No Android Studio needed; the toolchain installs user-local with no sudo. See
[`android/README.md`](android/README.md) for that one-time setup, then:

```bash
cd android
gradle testDebugUnitTest      # JVM only, no device or emulator
gradle assembleDebug          # app/build/outputs/apk/debug/garmin-sync-debug.apk
```

The unit tests cover the wire contract with this service. `PayloadTest` writes the
payload it produces to `build/wire-contract.json`, so the server's parser can be fed
the exact bytes the app emits rather than a hand-written sample:

```bash
gradle testDebugUnitTest && cd .. && python -c "
import json
from garmin_stats_sync.health_connect import parse_payload
print(parse_payload(json.load(open('android/app/build/wire-contract.json'))))"
```

CI builds the signed release APK; building one locally needs the keystore and its
password, also covered in [`android/README.md`](android/README.md).

## Troubleshooting

**Weigh-ins are not arriving.** Check `/health`. A rising `pending` means the phone
is delivering and Garmin is not — look at `token_state`. A `pending` of zero means
the phone is not delivering; open the app and read its last result.

**`token_state` is `expired`.** Visit `/login`. Anything held uploads on the next
cycle.

**The app reports 401.** The `INGEST_TOKEN` in `.env` and in the app disagree, or a
proxy is stripping the `X-Auth-Token` header.

**`API Error 412 — upload consent is not yet granted`.** Garmin has the account
flagged EU-location and refuses uploads until data-upload consent is granted in
Garmin Connect's account settings. Common on new accounts. The reading stays in the
spool and uploads once consent is given.

**A new APK will not install.** Release builds are signed with a fixed key, but the
first release APK replaced debug-signed builds, and Android refuses a signature
change — that one install needed an uninstall first. Later builds upgrade in place.

**Nothing reaches Health Connect at all.** That is upstream of this project: open
whichever app owns the scale and let it sync. Nothing here can do it for you.
