"""The HTTP surface: phone ingest, status page, Garmin login, health check.

Standard library only. A handful of routes does not justify a web framework in a
container that installs from pyproject.toml without a lockfile.

Access control is deliberately thin, and intentionally so: this runs on a home
LAN behind a reverse proxy that exists for a friendly hostname, not for auth.

The one credential is INGEST_TOKEN on /weigh-ins, because that path has
consequences outside the network - it writes to a real Garmin account. The pages
are readable by anything that can reach the host, which is the accepted trade;
deployments that want them protected put Basic auth on the proxy and leave
/weigh-ins exempt, since the app authenticates with the token instead.

The rest is cheap hygiene: a CSRF token on the login form and no-store on the
pages that render data.
"""

from __future__ import annotations

import hmac
import html
import json
import logging
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from garmin_stats_sync import garmin_auth
from garmin_stats_sync.runlog import local as runlog_local
from garmin_stats_sync.health_connect import (
    HealthConnectError,
    parse_record,
    record_key,
)

logger = logging.getLogger(__name__)

INGEST_PATH = "/weigh-ins"
_csrf_tokens: set[str] = set()
_csrf_lock = threading.Lock()


def _issue_csrf() -> str:
    token = secrets.token_urlsafe(32)
    with _csrf_lock:
        if len(_csrf_tokens) > 32:
            _csrf_tokens.clear()
        _csrf_tokens.add(token)
    return token


class IngestHandler(BaseHTTPRequestHandler):
    # Left at HTTP/1.0 deliberately. HTTP/1.1 requires an exact Content-Length on
    # every response including errors or clients hang, and keep-alive lets an
    # unread request body corrupt the next request's framing.
    server_version = "garmin-stats-sync"
    sys_version = ""

    # Set for HEAD, which must send identical headers but no body.
    _head_only = False

    # --- plumbing -----------------------------------------------------------

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # The default writes to stderr, bypassing the stdout logging config.
        logger.debug("%s %s", self.address_string(), format % args)

    def log_error(self, format: str, *args) -> None:  # noqa: A002
        logger.warning("%s %s", self.address_string(), format % args)

    @property
    def app(self):
        return self.server.app  # type: ignore[attr-defined]

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if content_type.startswith("text/html"):
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if not self._head_only:
            self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _html(self, status: HTTPStatus, markup: str) -> None:
        self._send(status, markup.encode("utf-8"), "text/html; charset=utf-8")

    # --- routing ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._json(HTTPStatus.OK, self.app.health())
        elif path == "/status":
            # Richer than /health, for the phone app's dashboard.
            self._json(HTTPStatus.OK, self.app.status())
        elif path == "/":
            self._html(HTTPStatus.OK, self.app.status_page())
        elif path == "/login":
            self._html(HTTPStatus.OK, self.app.login_page(_issue_csrf()))
        elif path == INGEST_PATH:
            self._json(
                HTTPStatus.METHOD_NOT_ALLOWED, {"error": "use POST"}
            )
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_HEAD(self) -> None:  # noqa: N802
        """Same headers as GET, no body.

        Uptime monitors commonly probe with HEAD, and the default handler would
        answer 501 - which reads as an outage.
        """
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == INGEST_PATH:
            self._handle_ingest()
        elif path == "/login":
            self._handle_login()
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    # --- ingest -------------------------------------------------------------

    def _authorised(self) -> bool:
        """Header only.

        A token accepted from a query string or cookie would let any page on the
        network drive this endpoint from a browser. Requiring a custom header
        means a cross-origin request needs a preflight, and we never answer
        OPTIONS - which is also why no CORS headers are sent anywhere.
        """
        provided = self.headers.get("X-Auth-Token", "")
        expected = self.app.token
        return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))

    def _read_body(self) -> bytes | None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._json(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length required"})
            return None
        try:
            length = int(raw_length)
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "bad Content-Length"})
            return None
        if length < 0 or length > self.app.max_body_bytes:
            # Refuse before allocating anything.
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body too large"})
            return None
        return self.rfile.read(length)

    def _handle_ingest(self) -> None:
        # Auth before reading the body: an unauthenticated client should never
        # get us to buffer its payload.
        if not self._authorised():
            logger.warning("rejected ingest from %s: bad token", self.address_string())
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return

        body = self._read_body()
        if body is None:
            return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "malformed json"})
            return

        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "payload has no records list"})
            return

        records = payload["records"]
        if len(records) > self.app.max_records:
            self._json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "too many records"}
            )
            return

        accepted, rejected = self.app.ingest(records)
        status = HTTPStatus.OK if accepted or not records else HTTPStatus.BAD_REQUEST
        self._json(
            status,
            {
                "accepted": accepted,
                "rejected": rejected,
                # Rides along so the phone learns about an expired Garmin login
                # without spending a second request on it. Cached server-side,
                # so this costs nothing.
                "token_state": garmin_auth.token_state(self.app.config),
            },
        )

    # --- login --------------------------------------------------------------

    def _handle_login(self) -> None:
        body = self._read_body()
        if body is None:
            return
        form = {k: v[0] for k, v in parse_qs(body.decode("utf-8")).items()}

        token = form.get("csrf", "")
        with _csrf_lock:
            valid = token in _csrf_tokens
            _csrf_tokens.discard(token)
        if not valid:
            self._html(
                HTTPStatus.FORBIDDEN,
                self.app.login_page(_issue_csrf(), error="Session expired, try again."),
            )
            return

        try:
            if form.get("mfa_code"):
                garmin_auth.submit_mfa(
                    self.app.config, form.get("session_id", ""), form["mfa_code"]
                )
                self.app.on_login()
                self._html(HTTPStatus.OK, self.app.login_done())
                return
            session_id = garmin_auth.begin_login(
                self.app.config, form.get("email", ""), form.get("password", "")
            )
        except garmin_auth.LoginError as exc:
            self._html(
                HTTPStatus.OK, self.app.login_page(_issue_csrf(), error=str(exc))
            )
            return

        if session_id is None:
            self.app.on_login()
            self._html(HTTPStatus.OK, self.app.login_done())
        else:
            self._html(HTTPStatus.OK, self.app.mfa_page(_issue_csrf(), session_id))


class App:
    """Everything the handler needs, minus the socket."""

    def __init__(self, config, inbox, runlog, wake: threading.Event) -> None:
        self.config = config
        self.inbox = inbox
        self.runlog = runlog
        self.wake = wake
        self.token = config.ingest_token
        self.max_body_bytes = config.ingest_max_body_bytes
        self.max_records = config.ingest_max_records

    def ingest(self, records: list) -> tuple[int, list[dict]]:
        """Spool each valid record. One bad record must not block the rest."""
        accepted = 0
        rejected: list[dict] = []
        for index, record in enumerate(records):
            try:
                reading = parse_record(record)
            except HealthConnectError as exc:
                rejected.append({"index": index, "reason": str(exc)})
                continue
            try:
                self.inbox.append(reading, record, record_key(record))
            except OSError:
                logger.exception("could not spool reading at index %s", index)
                raise
            accepted += 1

        if accepted:
            logger.info("accepted %s weigh-in(s) from the phone", accepted)
            self.wake.set()
        return accepted, rejected

    def on_login(self) -> None:
        """Drain the spool now that we can talk to Garmin again.

        You log in precisely when deliveries are stuck, so waiting out the sync
        interval is the wrong behaviour at exactly the wrong moment.
        """
        logger.info("garmin login succeeded, running a sync cycle now")
        self.wake.set()

    # --- views --------------------------------------------------------------

    def health(self) -> dict:
        log = self.runlog.snapshot()
        return {
            "ok": log.consecutive_failures == 0,
            "last_success": (
                log.last_success.isoformat() if log.last_success else None
            ),
            "pending": len(self.inbox.pending()),
            "token_state": garmin_auth.token_state(self.config),
            "consecutive_failures": log.consecutive_failures,
        }

    def status(self) -> dict:
        """Everything the app needs for a one-look view of the pipeline.

        Timestamps stay UTC ISO here - the client renders them in its own
        timezone, which is the only one that means anything to the person
        holding the phone.
        """
        token = garmin_auth.token_state(self.config)
        pending = self.inbox.pending()
        log = self.runlog.snapshot(40)
        return {
            "ok": log.consecutive_failures == 0 and token == "valid",
            "token_state": token,
            # Handed over rather than assembled on the phone, so the app never
            # has to guess how this service is addressed.
            "login_url": f"{self.config.public_url.rstrip('/')}/login"
            if self.config.public_url
            else "/login",
            "timezone": str(self.config.local_tz),
            "last_success": (
                log.last_success.isoformat() if log.last_success else None
            ),
            "consecutive_failures": log.consecutive_failures,
            "pending": [
                {
                    "taken_at": entry.reading.taken_at.isoformat(),
                    "weight_kg": entry.reading.weight_kg,
                    "received_at": entry.received_at.isoformat(),
                }
                for entry in pending
            ],
            "runs": [
                {
                    "at": r.at,
                    "trigger": r.trigger,
                    "uploaded": r.uploaded,
                    "skipped": r.skipped,
                    "failed": r.failed,
                    "fetched": r.fetched,
                    "error": r.error,
                }
                for r in reversed(log.entries)
            ],
        }

    def _page(self, title: str, body: str) -> str:
        return (
            "<!doctype html><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title>"
            "<style>"
            "body{font:16px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:46rem;"
            "padding:0 1rem;color:#1a1a1a;background:#fff}"
            "@media(prefers-color-scheme:dark){body{color:#e8e8e8;background:#161616}"
            "td,th{border-color:#333!important}input{background:#222;color:#eee;"
            "border-color:#444}}"
            "table{border-collapse:collapse;width:100%}"
            "td,th{border-bottom:1px solid #ddd;padding:.4rem .6rem;text-align:left;"
            "font-variant-numeric:tabular-nums}"
            ".ts{white-space:nowrap;font-variant-numeric:tabular-nums}"
            "label{display:block;margin:.8rem 0 .2rem}"
            "input{padding:.5rem;width:100%;max-width:22rem;border:1px solid #bbb;"
            "border-radius:4px}"
            "button{margin-top:1rem;padding:.5rem 1rem;border-radius:4px;"
            "border:1px solid #888;cursor:pointer}"
            ".bad{color:#b00020}.good{color:#0a7a34}"
            "</style>"
            f"{body}"
        )

    def status_page(self) -> str:
        state = garmin_auth.token_state(self.config)
        pending = self.inbox.pending()
        tz = self.config.local_tz
        rows = "".join(
            "<tr><td class=ts>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td></tr>".format(
                html.escape(e.local_at(tz)),
                html.escape(e.trigger),
                e.uploaded,
                e.skipped,
                f'<span class="bad">{e.failed}</span>' if e.failed else "0",
                html.escape(e.error or ""),
            )
            for e in reversed(self.runlog.recent(25))
        )
        pending_rows = "".join(
            "<tr><td class=ts>{}</td><td>{:.1f} kg</td><td class=ts>{}</td></tr>"
            .format(
                html.escape(runlog_local(p.reading.taken_at, tz)),
                p.reading.weight_kg,
                html.escape(runlog_local(p.received_at, tz)),
            )
            for p in pending
        )
        cls = "good" if state == "valid" else "bad"
        body = (
            "<h1>garmin-stats-sync</h1>"
            f"<p>Garmin token: <strong class={cls}>{html.escape(state)}</strong>"
            ' &middot; <a href="/login">log in</a></p>'
            f"<h2>Awaiting upload ({len(pending)})</h2>"
            + (
                f"<table><tr><th>Taken</th><th>Weight</th><th>Received</th></tr>"
                f"{pending_rows}</table>"
                if pending
                else "<p>Nothing pending.</p>"
            )
            + "<h2>Recent runs</h2>"
            + (
                "<table><tr><th>When</th><th>Trigger</th><th>Up</th>"
                f"<th>Skip</th><th>Fail</th><th>Error</th></tr>{rows}</table>"
                if rows
                else "<p>No runs recorded yet.</p>"
            )
        )
        return self._page("garmin-stats-sync", body)

    def login_page(self, csrf: str, error: str = "") -> str:
        note = f'<p class="bad">{html.escape(error)}</p>' if error else ""
        body = (
            "<h1>Log in to Garmin</h1>"
            "<p>Credentials are used once to obtain tokens and are never stored.</p>"
            f"{note}"
            '<form method=post action="/login">'
            f'<input type=hidden name=csrf value="{html.escape(csrf)}">'
            "<label>Email<input name=email type=email autocomplete=username required>"
            "</label>"
            "<label>Password<input name=password type=password "
            "autocomplete=current-password required></label>"
            "<button type=submit>Log in</button></form>"
            '<p><a href="/">back to status</a></p>'
        )
        return self._page("Log in", body)

    def mfa_page(self, csrf: str, session_id: str) -> str:
        body = (
            "<h1>Enter your Garmin code</h1>"
            '<form method=post action="/login">'
            f'<input type=hidden name=csrf value="{html.escape(csrf)}">'
            f'<input type=hidden name=session_id value="{html.escape(session_id)}">'
            "<label>Code<input name=mfa_code inputmode=numeric autocomplete=one-time-code"
            " required></label>"
            "<button type=submit>Confirm</button></form>"
        )
        return self._page("Garmin code", body)

    def login_done(self) -> str:
        body = (
            '<h1 class=good>Logged in</h1><p>Tokens stored. Anything waiting in '
            'the spool is uploading now.</p><p><a href="/">back to status</a></p>'
        )
        return self._page("Logged in", body)


def build_server(app: App) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((app.config.ingest_host, app.config.ingest_port),
                                 IngestHandler)
    server.app = app  # type: ignore[attr-defined]
    # A client that opens a socket and sends nothing must not hold a thread.
    server.timeout = 30
    return server


def start_in_thread(
    server: ThreadingHTTPServer, poll_interval: float = 0.5
) -> threading.Thread:
    """Serve in a daemon thread.

    `poll_interval` is how long shutdown() may take to be noticed; the default
    matches serve_forever's own. Tests lower it so teardown is not the slowest
    part of the suite.
    """
    thread = threading.Thread(
        target=server.serve_forever,
        args=(poll_interval,),
        name="ingest",
        daemon=True,
    )
    thread.start()
    return thread
