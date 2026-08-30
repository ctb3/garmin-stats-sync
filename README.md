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
                          reverse proxy (TLS, Basic auth)
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

## Setup

### 1. The service

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"   # INGEST_TOKEN
docker compose up -d
```

`GARMIN_EMAIL`/`GARMIN_PASSWORD` are optional. Leave them blank and log in through the
web page instead — Garmin sessions last about a year, so you will be prompted again
only when they expire, and no password is ever stored.

### 2. The reverse proxy

The container publishes to **loopback only**. Point your proxy at `127.0.0.1:8080`,
terminate TLS there, and put Basic auth in front of the whole host.

Basic auth matters: the status page shows your weight history. That is the real
access control, and it is one htpasswd line. Configure `/weigh-ins` to **bypass**
Basic auth — the phone authenticates with its own token header instead.

```nginx
server {
    server_name garmin-sync.example.net;
    # Public-CA certificate (e.g. Let's Encrypt DNS-01) so the phone's system
    # trust store covers it; no custom trust anchor in the app.

    location /weigh-ins {
        proxy_pass http://127.0.0.1:8080;
    }
    location / {
        auth_basic "garmin-sync";
        auth_basic_user_file /etc/nginx/garmin-sync.htpasswd;
        proxy_pass http://127.0.0.1:8080;
    }
}
```

> **Do not** publish port 8080 on a LAN interface as a shortcut. Docker's iptables
> `DOCKER` chain DNATs *before* `ufw`/`firewalld`, so a host firewall you believe is
> blocking 8080 will not block it.

### 3. Log in to Garmin

Visit `https://garmin-sync.example.net/login`. If your account uses MFA you will be
asked for the code on a second step.

### 4. The Android app

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
| `GET` | `/` | proxy Basic auth | Status: recent runs, pending spool, token state |
| `GET`/`POST` | `/login` | proxy Basic auth | Garmin login and MFA |
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
