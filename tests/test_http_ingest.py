"""The HTTP surface, driven against a real server on an ephemeral port."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request

import pytest

from garmin_stats_sync.http_ingest import App, build_server, start_in_thread
from garmin_stats_sync.inbox import Inbox
from garmin_stats_sync.runlog import RunLog

TOKEN = "t" * 32


class FakeAuth:
    """Stands in for garmin_auth so no test touches the network."""

    state = "valid"


@pytest.fixture
def server(config, monkeypatch):
    from garmin_stats_sync import http_ingest

    monkeypatch.setattr(
        http_ingest.garmin_auth, "token_state", lambda _config: FakeAuth.state
    )
    app = App(
        config,
        Inbox(config.inbox_dir),
        RunLog(config.runlog_file),
        threading.Event(),
    )
    httpd = build_server(app)
    start_in_thread(httpd, poll_interval=0.01)
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _post(url, body, headers=None, raw=False):
    data = body if raw else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _payload(*, time_ms=1_756_150_200_000, kg=82.1, record_id="abc"):
    return {
        "records": [
            {
                "metadata": {"id": record_id},
                "time": time_ms,
                "weight": {"kilograms": kg},
            }
        ]
    }


def _auth():
    return {"X-Auth-Token": TOKEN, "Content-Type": "application/json"}


# --- ingest -------------------------------------------------------------------


def test_valid_post_is_accepted_and_spooled(server):
    app, base = server
    status, body = _post(f"{base}/weigh-ins", _payload(), _auth())

    assert status == 200
    assert json.loads(body)["accepted"] == 1
    assert len(app.inbox.fetch_readings()) == 1


def test_accepted_post_wakes_the_sync_loop(server):
    app, base = server
    assert not app.wake.is_set()

    _post(f"{base}/weigh-ins", _payload(), _auth())

    assert app.wake.is_set()


def test_wrong_token_is_rejected_and_writes_nothing(server):
    app, base = server
    status, _ = _post(
        f"{base}/weigh-ins", _payload(), {"X-Auth-Token": "w" * 32}
    )

    assert status == 401
    assert app.inbox.fetch_readings() == []


def test_missing_token_is_rejected(server):
    _, base = server
    status, _ = _post(f"{base}/weigh-ins", _payload(), {})
    assert status == 401


def test_token_in_query_string_is_not_accepted(server):
    """Header only: a query-string token would be reachable from a browser."""
    app, base = server
    status, _ = _post(f"{base}/weigh-ins?token={TOKEN}", _payload(), {})

    assert status == 401
    assert app.inbox.fetch_readings() == []


def test_oversized_body_is_refused(server):
    app, base = server
    app.max_body_bytes = 64
    status, _ = _post(f"{base}/weigh-ins", _payload(kg=82.1), _auth())

    assert status == 413
    assert app.inbox.fetch_readings() == []


def test_too_many_records_is_refused(server):
    app, base = server
    app.max_records = 1
    payload = {"records": _payload()["records"] * 2}
    status, _ = _post(f"{base}/weigh-ins", payload, _auth())

    assert status == 413


def test_malformed_json_is_a_bad_request(server):
    _, base = server
    status, _ = _post(f"{base}/weigh-ins", b"{not json", _auth(), raw=True)
    assert status == 400


def test_payload_without_records_is_a_bad_request(server):
    _, base = server
    status, _ = _post(f"{base}/weigh-ins", {"pageToken": None}, _auth())
    assert status == 400


def test_one_bad_record_does_not_block_the_good_ones(server):
    app, base = server
    payload = _payload()
    payload["records"].append({"metadata": {"id": "bad"}, "time": None})
    status, body = _post(f"{base}/weigh-ins", payload, _auth())

    assert status == 200
    parsed = json.loads(body)
    assert parsed["accepted"] == 1
    assert parsed["rejected"][0]["index"] == 1
    assert len(app.inbox.fetch_readings()) == 1


def test_all_records_bad_is_a_bad_request(server):
    _, base = server
    payload = {"records": [{"metadata": {"id": "bad"}, "time": None}]}
    status, body = _post(f"{base}/weigh-ins", payload, _auth())

    assert status == 400
    assert json.loads(body)["accepted"] == 0


def test_same_record_twice_yields_one_spool_file(server):
    app, base = server
    _post(f"{base}/weigh-ins", _payload(), _auth())
    status, _ = _post(f"{base}/weigh-ins", _payload(), _auth())

    assert status == 200
    assert len(app.inbox.fetch_readings()) == 1


def test_get_on_ingest_path_is_method_not_allowed(server):
    _, base = server
    assert _get(f"{base}/weigh-ins")[0] == 405


def test_unknown_path_is_not_found(server):
    _, base = server
    assert _get(f"{base}/nope")[0] == 404


# --- pages --------------------------------------------------------------------


def test_health_needs_no_auth(server):
    _, base = server
    status, body = _get(f"{base}/health")
    parsed = json.loads(body)

    assert status == 200
    assert parsed["token_state"] == "valid"
    assert parsed["pending"] == 0


def test_health_counts_pending_spool(server):
    _, base = server
    _post(f"{base}/weigh-ins", _payload(), _auth())

    assert json.loads(_get(f"{base}/health")[1])["pending"] == 1


def test_status_page_renders(server):
    _, base = server
    status, body = _get(f"{base}/")

    assert status == 200
    assert b"garmin-stats-sync" in body


def test_status_page_escapes_hostile_values(server, config):
    _, base = server
    from garmin_stats_sync.runlog import RunEntry

    RunLog(config.runlog_file).append(
        RunEntry(
            at="2025-08-26T00:00:00+00:00",
            trigger="<script>alert(1)</script>",
            uploaded=0,
            skipped=0,
            failed=0,
            fetched=0,
        )
    )
    _, body = _get(f"{base}/")

    assert b"<script>alert(1)</script>" not in body
    assert b"&lt;script&gt;" in body


def test_login_page_renders_a_form(server):
    _, base = server
    status, body = _get(f"{base}/login")

    assert status == 200
    assert b'name=password' in body


def test_login_without_csrf_token_is_forbidden(server):
    _, base = server
    status, _ = _post(
        f"{base}/login",
        b"email=a%40b.com&password=secret",
        {"Content-Type": "application/x-www-form-urlencoded"},
        raw=True,
    )
    assert status == 403


def test_head_on_health_returns_headers_without_a_body(server):
    """Uptime monitors probe with HEAD; the default handler answers 501."""
    _, base = server
    request = urllib.request.Request(f"{base}/health", method="HEAD")
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
        assert response.read() == b""
        assert response.headers["Content-Type"] == "application/json"


def test_status_page_sets_no_store(server):
    _, base = server
    with urllib.request.urlopen(f"{base}/", timeout=5) as response:
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_successful_login_wakes_the_sync_loop(server, monkeypatch):
    """Logging in is the moment a stuck spool becomes deliverable."""
    from garmin_stats_sync import http_ingest

    app, base = server
    monkeypatch.setattr(
        http_ingest.garmin_auth, "begin_login", lambda *_args: None
    )
    csrf = re.search(rb'name=csrf value="([^"]+)"', _get(f"{base}/login")[1]).group(1)
    app.wake.clear()

    status, body = _post(
        f"{base}/login",
        b"csrf=" + csrf + b"&email=a%40b.com&password=secret",
        {"Content-Type": "application/x-www-form-urlencoded"},
        raw=True,
    )

    assert status == 200
    assert b"Logged in" in body
    assert app.wake.is_set()


def test_ingest_response_carries_token_state(server):
    """The phone learns a login is needed without a second request."""
    _, base = server
    _, body = _post(f"{base}/weigh-ins", _payload(), _auth())

    assert json.loads(body)["token_state"] == "valid"


def test_status_endpoint_serves_the_app_dashboard(server, config):
    _, base = server
    _post(f"{base}/weigh-ins", _payload(), _auth())

    status, body = _get(f"{base}/status")
    parsed = json.loads(body)

    assert status == 200
    assert parsed["token_state"] == "valid"
    assert parsed["timezone"] == "America/New_York"
    assert parsed["login_url"].endswith("/login")
    assert len(parsed["pending"]) == 1
    assert parsed["pending"][0]["weight_kg"] == 82.1


def test_status_page_renders_local_single_line_timestamps(server, config):
    from garmin_stats_sync.runlog import RunEntry

    _, base = server
    RunLog(config.runlog_file).append(
        RunEntry(
            at="2026-08-31T12:03:31.873005+00:00",
            trigger="interval",
            uploaded=0, skipped=0, failed=0, fetched=0,
        )
    )
    _, body = _get(f"{base}/")

    # America/New_York is UTC-4 in August: 12:03 UTC -> 08:03 local.
    assert b"2026-08-31 08:03:31" in body
    assert b"12:03:31.873005" not in body


def test_status_reads_the_run_log_once(server, config, monkeypatch):
    """Every helper on RunLog re-reads the file; a status view wants all three."""
    from garmin_stats_sync import runlog as runlog_module

    _, base = server
    reads = []
    original = runlog_module.RunLog.recent

    def counting(self, limit=50):
        reads.append(limit)
        return original(self, limit)

    monkeypatch.setattr(runlog_module.RunLog, "recent", counting)
    _get(f"{base}/status")

    assert len(reads) == 1
