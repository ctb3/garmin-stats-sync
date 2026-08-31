# garmin-stats-sync

Copies weigh-ins from an Etekcity/VeSync smart scale into Garmin Connect, unattended.

Weight only — body fat, hydration and muscle mass are deliberately out of scope.

## How it works

The scale talks BLE to the phone, the VeSync app writes the reading into Android
Health Connect, a small Android app in this repo reads it from there and POSTs it to
this service, and the service uploads it to Garmin Connect.

```
scale --BLE--> VeSync app --> Health Connect
                                   |
              (Android app, WorkManager 30m, unmetered, battery-not-low)
                                   |  POST /weigh-ins  + X-Auth-Token
                                   v      https://garmin-sync.example.net
                          reverse proxy (TLS)          
                                   |
                        receiver thread --> spool /data/inbox/*.json
                                                     |
                     sync loop --> upload --> Garmin Connect
```

### Why the data is pushed rather than pulled

Health Connect has no cloud and no REST API. It is an on-device datastore reachable
only from an Android app, so nothing server-side can read it. The phone has to push.

This replaced an earlier design that polled VeSync's cloud directly. That never
worked for this scale — a Bluetooth-only device appears in the device list but its
measurements are not addressable through the device-scoped cloud API. The findings
are written up in [`attic/vesync/`](attic/vesync/README.md).

### Why Garmin upload stays on the server

Garmin has no public API, and the unofficial route is now anti-bot-hardened.
`garth` was deprecated in March 2026 when Garmin changed their auth flow;
`garminconnect` 0.3.11 gets in by impersonating a real browser's TLS/JA3 fingerprint
(`curl-cffi`) across several fallback strategies. Android's HTTP stacks present their
own TLS fingerprint and cannot do that without bundling a native TLS build, so the
app stays a dumb sender and the login stays here.

### What this does not fix

A reading only reaches Health Connect after the VeSync app has pulled it off the scale
over Bluetooth. If your scale holds readings until you open the VeSync app, that step
stays manual. Everything after it is automatic.

## Reliability model

- The phone advances its high-water mark **only after the server confirms a 2xx**. A
  failed POST leaves it unmoved, so the next run re-reads the same records. Health
  Connect is the queue; the app deliberately keeps no second copy that could diverge.
- The phone sends a 7-day window whenever anything is new. The server dedupes by
  timestamp, so over-sending is free and repairs the pipeline if either side loses
  its place.
- The server does not answer `200` until the reading is `fsync`'d to `/data/inbox/`.
- A spooled reading is deleted only on **positive proof of delivery** — presence in
  `state.json`'s `synced` list, which is written only after a successful Garmin upload.
- So a weigh-in arriving while your Garmin token is expired is accepted, held, and
  uploaded once you log in again. No re-POST from the phone is needed.

## Deploying to Proxmox

End to end, from an empty Proxmox host to weigh-ins landing in Garmin. Everything
runs in one small unprivileged LXC.

### 1. Create the LXC

Docker inside an unprivileged container needs the **nesting** and **keyctl**
features. 1 core / 512 MB RAM / 8 GB disk is plenty.

```bash
# from the Proxmox host
pct create 120 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname garmin-sync \
  --cores 1 --memory 512 --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --features nesting=1,keyctl=1 \
  --unprivileged 1 --onboot 1 --start 1
pct enter 120
```

If the LXC already exists, set the features and restart it:

```bash
pct set 120 --features nesting=1,keyctl=1 --onboot 1
pct reboot 120
```

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

If that fails with an overlayfs error (common when the LXC rootfs is on ZFS),
switch Docker to fuse-overlayfs and retry:

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

If the repo is private, clone over SSH with a deploy key, or push the working
copy straight from your workstation:

```bash
rsync -av --exclude .venv --exclude data --exclude .env --exclude android/app/build \
  ./ root@<lxc-ip>:/opt/garmin-stats-sync/
```

### 4. Configure

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # INGEST_TOKEN
nano .env
chmod 600 .env
mkdir -p data
```

Two things that matter:

- **`INGEST_TOKEN`** — required, minimum 32 characters. The app sends it.
- **`LOCAL_TZ`** — decides which calendar day a weigh-in is recorded against. Set
  your own timezone (`America/New_York`, …), not UTC.

Leave `GARMIN_EMAIL`/`GARMIN_PASSWORD` blank unless you want unattended
re-login; the `/login` page covers it and stores no password.

`BIND_ADDR` is the **host-side interface** the container publishes on. It
defaults to `127.0.0.1`, which is reachable only from inside the LXC — so set it
to the LXC's own address whenever anything else has to connect:

```bash
BIND_ADDR=192.168.1.25     # this LXC's LAN address
HOST_PORT=8080
```

Leave the default only if your reverse proxy runs **on this same LXC**; then it
connects over loopback and nothing is published to the network at all.

`0.0.0.0` also works and means "every interface", but naming the address is
worth the few extra characters. Either way, publishing beyond loopback is an
exposure decision: Docker's iptables `DOCKER` chain DNATs *before*
`ufw`/`firewalld`, so a host firewall you believe blocks this port will not.

### 5. Build and start

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

Expect `ingest listening on 0.0.0.0:8080` within a second or two.

### 6. Log in to Garmin

```bash
curl -s localhost:8080/health     # token_state should be "absent"
```

Open `http://<lxc-ip>:8080/login` in a browser and sign in. If your account uses
MFA you get a second step for the code. Then confirm:

```bash
curl -s localhost:8080/health     # token_state should now be "valid"
```

### 7. Prove the pipeline before trusting it

Post a weigh-in by hand — no phone needed:

```bash
TOKEN=$(grep ^INGEST_TOKEN= .env | cut -d= -f2-)
curl -sS -X POST localhost:8080/weigh-ins \
  -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"records\":[{\"metadata\":{\"id\":\"manual-1\"},
       \"time\":$(($(date +%s)*1000)),\"weight\":{\"kilograms\":80.0}}]}"
```

Expect `{"accepted": 1, "rejected": []}`, then the upload in the logs within
seconds. Check Garmin Connect under **Health Stats → Weight** for an 80.0 kg
entry at today's date, and delete it there once you are satisfied.

A `412 ... upload consent is not yet granted` here means Garmin has the account
flagged EU-location — see [Troubleshooting](#troubleshooting).

### 8. Confirm it survives a reboot

```bash
reboot                      # inside the LXC
docker compose ps           # after it comes back, expect "healthy"
```

The container restarts via `restart: unless-stopped`, the LXC via `--onboot 1`.
Nothing to schedule — the loop does its own timing.

### Day to day

```bash
docker compose logs -f                      # watch it work
docker compose restart                      # after editing .env
git pull && docker compose up -d --build    # update
```

## Reverse proxy (optional)

Only worth it for a friendly hostname and TLS. A plain pass-through is all it
needs; point it at wherever you published the container:

```nginx
server {
    server_name garmin-sync.example.net;
    # Public-CA certificate (e.g. Let's Encrypt DNS-01) so the phone's system
    # trust store covers it - no custom trust anchor, no cleartext exception.

    location / {
        proxy_pass http://127.0.0.1:8080;
    }
}
```

**What this leaves open.** With no auth at the proxy, anything on your network can
read the status page — which shows your weigh-in history — and see the login form.
On a home LAN that is usually a fine trade for not maintaining an htpasswd file.

`INGEST_TOKEN` is then the only credential in the system, and it guards the one
thing with consequences outside your network: writes to your Garmin account.
Without it, any device on the LAN could inject weigh-ins into your history.

If you later want the pages protected too, add Basic auth on `location /` and give
`/weigh-ins` its own `location` block without it — the app authenticates with the
token, not with Basic auth.

> **Do not** publish port 8080 on a LAN interface instead of proxying. Docker's
> iptables `DOCKER` chain DNATs *before* `ufw`/`firewalld`, so a host firewall you
> believe is blocking 8080 will not block it.

## The Android app

There is no local Android toolchain by design. Push to GitHub and the
[workflow](.github/workflows/android.yml) builds a debug APK; download it from the run
artifacts and sideload it. Sideloading means no Play listing, so no Health Connect
declaration form and no privacy policy.

In the app: enter the server address and the `INGEST_TOKEN`, tap **Save and grant
permissions**, and grant **Weight** plus background access. Then tap **Sync now** once
to confirm it works.

Grant only Weight. The app requests nothing else.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/weigh-ins` | `X-Auth-Token` header | Ingest from the phone |
| `GET` | `/` | whatever the proxy enforces | Status: recent runs, pending spool, token state |
| `GET`/`POST` | `/login` | whatever the proxy enforces | Garmin login and MFA |
| `GET`/`HEAD` | `/health` | none | JSON for external monitoring |

The token is accepted **only** as a header — never a query string or cookie — so a
browser page cannot drive the endpoint without a preflight this service never answers.

`/health` is what alerts you to an expired Garmin token:

```json
{"ok": true, "last_success": "...", "pending": 0,
 "token_state": "valid", "consecutive_failures": 0}
```

Point an uptime monitor at it and alert on `token_state != "valid"`, a rising
`pending`, or a stale `last_success`. The app raises a phone notification for the one
failure this cannot see — the server being unreachable.

## Commands

| Command | What it does |
|---|---|
| `loop` | Run the listener and the sync loop. The container default. |
| `sync` | One cycle against whatever is already spooled. |
| `bootstrap-garmin` | Log in from the terminal, if you prefer that to `/login`. |

Flags: `--since 7d|12h|YYYY-MM-DD|all`, `--dry-run`, `-v`.

## State

| Path | Contents |
|---|---|
| `/data/garth/` | Garmin OAuth tokens (~1 year) |
| `/data/inbox/` | Weigh-ins received but not yet confirmed to Garmin |
| `/data/state.json` | Which weigh-ins have been delivered |
| `/data/runlog.jsonl` | Recent runs, shown on the status page |

Delete `state.json` to re-sync from scratch.

## Power

The phone polls every 30 minutes, constrained to unmetered networks and
battery-not-low, with a flex window so `JobScheduler` batches it into a wake it was
already making. A run with nothing new returns before touching the network, which is
almost every run — so the cost is a process wake and one Health Connect IPC read, not
a connection. Android 15+ background reads are what let the app avoid a foreground
service entirely; that, not the interval, is the thing that would have cost battery.

An arriving weigh-in also wakes the server's sync loop immediately, so end-to-end
latency is bounded by the phone's interval rather than the server's.

## Troubleshooting

**Weigh-ins are not arriving.** Check `/health` for `pending`. If it is rising, the
phone is delivering and Garmin is not — check `token_state`. If it stays zero, the
phone is not delivering: open the app and read its last result.

**`token_state` is `expired`.** Visit `/login`. Anything held in the spool uploads on
the next cycle.

**The app reports 401.** The `INGEST_TOKEN` in `.env` and in the app disagree, or the
proxy is stripping the `X-Auth-Token` header.

**`API Error 412 - The user is from EU location, but upload consent is not yet
granted or revoked`.** Garmin has the account flagged as EU-location and refuses
uploads until data-upload consent is granted in Garmin Connect's account/privacy
settings. Nothing here can grant it for you. The reading stays in the spool and
uploads once consent is given. New accounts commonly land in this state.

**Nothing in Health Connect.** Open the VeSync app so it pulls the reading off the
scale over Bluetooth. Nothing downstream can do this for you.
