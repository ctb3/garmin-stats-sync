"""Sync policy: cold-start window, dedup, per-reading failure isolation."""

from datetime import UTC, datetime, timedelta

import pytest

from garmin_stats_sync.mapping import parse_readings
from garmin_stats_sync.state import State
from garmin_stats_sync.sync import parse_since, run_once

NOW = datetime(2025, 8, 26, 12, 0, tzinfo=UTC)


class FakeVeSync:
    def __init__(self, readings):
        self._readings = readings
        self.calls = 0

    def fetch_readings(self):
        self.calls += 1
        return list(self._readings)


class FakeGarmin:
    def __init__(self, fail_on=()):
        self.uploaded = []
        self.fail_on = set(fail_on)

    def upload_weight(self, reading):
        if reading.source_timestamp in self.fail_on:
            raise RuntimeError("garmin said no")
        self.uploaded.append(reading)


@pytest.fixture
def readings(weigh_data_payload):
    return parse_readings(weigh_data_payload)


def test_uploads_new_readings(tmp_path, readings):
    garmin = FakeGarmin()
    state = State.load(tmp_path / "state.json")

    result = run_once(
        FakeVeSync(readings), garmin, state, since=NOW - timedelta(days=7), now=NOW
    )

    assert result.uploaded == 3
    assert len(garmin.uploaded) == 3
    assert state.last_timestamp == readings[-1].source_timestamp


def test_second_run_uploads_nothing(tmp_path, readings):
    state = State.load(tmp_path / "state.json")
    since = NOW - timedelta(days=7)
    run_once(FakeVeSync(readings), FakeGarmin(), state, since=since, now=NOW)

    garmin = FakeGarmin()
    result = run_once(FakeVeSync(readings), garmin, state, since=since, now=NOW)

    assert result.uploaded == 0
    assert result.skipped == 3
    assert garmin.uploaded == []


def test_cold_start_ignores_readings_older_than_window(tmp_path, readings):
    garmin = FakeGarmin()
    state = State.load(tmp_path / "state.json")

    result = run_once(
        FakeVeSync(readings), garmin, state, since=NOW - timedelta(days=2), now=NOW
    )

    assert result.uploaded == 2
    assert result.skipped == 1
    uploaded = {r.source_timestamp for r in garmin.uploaded}
    assert readings[0].source_timestamp not in uploaded


def test_failed_upload_is_retried_next_run(tmp_path, readings):
    failing = readings[-1].source_timestamp
    state = State.load(tmp_path / "state.json")
    since = NOW - timedelta(days=7)

    first = run_once(
        FakeVeSync(readings), FakeGarmin(fail_on=[failing]), state, since=since, now=NOW
    )
    assert first.uploaded == 2
    assert first.failed == 1

    garmin = FakeGarmin()
    second = run_once(FakeVeSync(readings), garmin, state, since=since, now=NOW)

    assert second.uploaded == 1
    assert garmin.uploaded[0].source_timestamp == failing


def test_dry_run_uploads_nothing_and_keeps_state_clean(tmp_path, readings):
    garmin = FakeGarmin()
    state = State.load(tmp_path / "state.json")

    result = run_once(
        FakeVeSync(readings),
        garmin,
        state,
        since=NOW - timedelta(days=7),
        now=NOW,
        dry_run=True,
    )

    assert result.uploaded == 3
    assert garmin.uploaded == []
    assert state.last_timestamp is None


def test_parse_since_relative_days():
    assert parse_since("7d", now=NOW) == NOW - timedelta(days=7)
    assert parse_since("12h", now=NOW) == NOW - timedelta(hours=12)


def test_parse_since_iso_date():
    assert parse_since("2025-08-01", now=NOW) == datetime(2025, 8, 1, tzinfo=UTC)


def test_parse_since_all():
    assert parse_since("all", now=NOW) == datetime(1970, 1, 1, tzinfo=UTC)


def test_parse_since_rejects_garbage():
    with pytest.raises(ValueError):
        parse_since("last tuesday", now=NOW)
