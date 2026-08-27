# garmin-stats-sync

Copies weigh-ins from an Etekcity/VeSync smart scale into Garmin Connect, unattended.

Weight only — body fat, hydration and muscle mass are deliberately out of scope.

## How it works

The scale talks BLE to the phone, the VeSync app pushes readings to VeSync's
cloud, and this service polls that cloud and re-posts new readings to Garmin.

```
scale --BLE--> VeSync app --> VeSync cloud --poll--> this service --> Garmin Connect
```

pyvesync never merged smart-scale support, so the library is used for login,
token caching and region handling, while the device list and weigh-in history
are raw calls through its authenticated session
(`/cloud/v1/deviceManaged/devices` and `/cloud/v2/deviceManaged/getWeighingDataV2`,
with `/cloud/v1/deviceManaged/fatScale/getWeighData` as a fallback for older
scales). Garmin uploads go through `python-garminconnect`'s
`add_weigh_in_with_timestamps`, authenticated by garth OAuth tokens that last
about a year.

**One caveat that no server-side code can fix:** a reading only reaches VeSync's
cloud after the phone app has pulled it off the scale over Bluetooth. If your
scale holds readings until you open the VeSync app, that step stays manual.
Everything after it is automatic.

## First-time setup

End to end, from an empty Proxmox cluster to weigh-ins landing in Garmin.
Everything runs in one small unprivileged LXC.

### 1. Create the LXC

Docker inside an unprivileged container needs the **nesting** and **keyctl**
features.

1 core / 512 MB RAM / 8 GB disk


### 2. Install Docker in the container

```bash
apt update && apt install -y ca-certificates curl git

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list

apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Confirm the daemon actually works inside the LXC before going further:

```bash
docker run --rm hello-world
```

If that fails with an overlayfs error (common when the container's rootfs is on
ZFS), install `fuse-overlayfs`, point Docker at it, and retry:

```bash
apt install -y fuse-overlayfs
mkdir -p /etc/docker
echo '{"storage-driver": "fuse-overlayfs"}' > /etc/docker/daemon.json
systemctl restart docker && docker run --rm hello-world
```

### 3. Get the code

```bash
git clone https://github.com/ctb3/garmin-stats-sync.git /opt/garmin-stats-sync
cd /opt/garmin-stats-sync
```

If the repo is private, either clone over SSH with a deploy key, or push the
working copy straight from your workstation:

```bash
rsync -av --exclude .venv --exclude data --exclude .env \
  ./ root@<lxc-ip>:/opt/garmin-stats-sync/
```

### 4. Configure

```bash
cp .env.example .env
nano .env          # VeSync + Garmin credentials, LOCAL_TZ
chmod 600 .env     # it holds plaintext account passwords
mkdir -p data
```

`LOCAL_TZ` decides which calendar day a weigh-in is recorded against — set it to
your own timezone (`America/New_York`, `Europe/Berlin`, …), not UTC.

### 5. Build the image

```bash
docker compose build
```

### 6. Bootstrap credentials (interactive, once)

These two commands log in and cache tokens into `data/`, so the unattended loop
never needs an interactive prompt. Run them from a real terminal — Garmin will
ask for an MFA code if your account has 2FA enabled.

```bash
docker compose run --rm sync bootstrap-vesync
docker compose run --rm sync bootstrap-garmin
```

### 7. Dry run before writing anything to Garmin

```bash
docker compose run --rm sync sync --since 7d --dry-run
```

Each line shows the converted weight and both timestamps. Check them against the
VeSync app — right weights, right days — before continuing. If they are wrong,
stop and run the probe (see [Development](#development)); the mapping constants
live in `garmin_stats_sync/mapping.py`.

### 8. Start it

```bash
docker compose up -d
docker compose logs -f
```

Confirm the first weigh-in appears in Garmin Connect under
**Health Stats → Weight**, at the correct date and time.

### 9. Confirm it survives a reboot

```bash
reboot                      # inside the LXC
docker compose ps           # after it comes back
```

The container restarts via `restart: unless-stopped`, and the LXC itself via
`--onboot 1`. Nothing else to schedule — the loop does its own timing.

### Day-to-day

```bash
docker compose logs -f                      # watch it work
docker compose restart                      # after editing .env
git pull && docker compose up -d --build    # update
```

## Commands

| Command | What it does |
|---|---|
| `sync` | One pass: fetch, upload new weigh-ins, exit |
| `loop` | `sync` every `SYNC_INTERVAL_SECONDS` (container default) |
| `bootstrap-vesync` | Log in and cache VeSync credentials |
| `bootstrap-garmin` | Log in (with MFA prompt) and cache Garmin OAuth tokens |

Flags: `--since 7d|12h|YYYY-MM-DD|all`, `--dry-run`, `--verbose`.

## State

Everything mutable lives in the `/data` volume:

| Path | Contents |
|---|---|
| `/data/garth/` | Garmin OAuth tokens |
| `/data/vesync.json` | VeSync token and account id |
| `/data/state.json` | Dedup record of synced weigh-ins |

Delete `state.json` to re-sync from scratch (`--since` controls how far back).

If Garmin tokens expire you'll see `GARMIN REAUTH REQUIRED` in the logs; rerun
`bootstrap-garmin`.

## Troubleshooting

### Exploring the API by hand

`docs/vesync-api.md` documents every endpoint, header and body this project
uses, the login flow, the error codes seen, and everything already tried for
weigh-in retrieval.

To poke at it yourself:

```bash
docker compose run --rm dump-session   # prints token, accountId, uuid, configModule
```

Import `insomnia/vesync-scale.json` into Insomnia, paste those values into the
Base environment, and the auth, device-list and weigh-in requests are ready to
run. The session token is a live credential - keep it out of issues and chats.

### VeSync returns an error instead of weigh-ins

Run the endpoint search and read the codes:

```bash
docker compose run --rm diagnose
```

VeSync's codes tell you which wall you hit:

| Code | Meaning |
|---|---|
| `0` | success |
| `-11000079` | illegal argument — the endpoint exists, the body is wrong |
| `-11105079` | MySQL error — the server-side query failed, usually the wrong endpoint for this device class |
| `-11102000` | token expired — delete `data/vesync.json` and re-run `bootstrap-vesync` |

BT-only scales (`"connectionType": "BT"`, `"cid": null`) do not answer the same
endpoints as WiFi devices, and no public VeSync client implements weigh-in
retrieval for them, so the request shape has to be discovered.

If no variant returns `code=0`, capture what the VeSync app itself sends:

1. Install [mitmproxy](https://mitmproxy.org/) on a machine on your LAN and run `mitmweb`.
2. On the phone, set that machine as the Wi-Fi HTTP proxy (port 8080) and
   install the mitm CA certificate from `http://mitm.it`.
3. Open the VeSync app and view the scale's weight history.
4. Filter for `smartapi.vesync.com` and find the request whose response
   contains your weights.
5. The endpoint path and request body from that flow are the answer — the
   matching constants live in `garmin_stats_sync/vesync_client.py`.

### Garmin login returns 429

`GarminConnectTooManyRequestsError: IP rate limited` during `bootstrap-garmin`
is garth retrying; it usually succeeds on the next attempt. Space out repeated
bootstraps rather than looping them.

### `GARMIN REAUTH REQUIRED` in the logs

The stored OAuth tokens expired (roughly yearly, or after a password change).
Re-run `docker compose run --rm sync bootstrap-garmin`.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run python scripts/probe_vesync.py              # dump raw VeSync responses
uv run python scripts/diagnose_weigh_endpoints.py  # search for the weigh-in endpoint
```

Tests are offline and run against recorded payloads in `tests/fixtures/`.
