"""The durable spool: atomicity, idempotence, and what prune may delete."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from garmin_stats_sync.inbox import Inbox
from garmin_stats_sync.models import Reading
from garmin_stats_sync.state import State

NOW = datetime(2025, 8, 26, tzinfo=UTC)


def _reading(ts: int = 1_756_150_200, kg: float = 82.1) -> Reading:
    return Reading(
        taken_at=datetime.fromtimestamp(ts, UTC),
        weight_kg=kg,
        source_timestamp=ts,
    )


def _raw(ts: int = 1_756_150_200) -> dict:
    return {"time": ts * 1000, "weight": {"kilograms": 82.1}}


def test_append_then_fetch_round_trips(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    inbox.append(_reading(), _raw(), "key0", now=NOW)

    (fetched,) = inbox.fetch_readings()
    assert fetched == _reading()


def test_same_record_twice_yields_one_file(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    inbox.append(_reading(), _raw(), "key0", now=NOW)
    inbox.append(_reading(), _raw(), "key0", now=NOW)

    assert len(inbox.fetch_readings()) == 1


def test_fetch_is_oldest_first(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    for ts in (1_756_150_200, 1_755_977_400, 1_756_063_800):
        inbox.append(_reading(ts), _raw(ts), f"k{ts}", now=NOW)

    stamps = [r.source_timestamp for r in inbox.fetch_readings()]
    assert stamps == sorted(stamps)


def test_staging_files_are_invisible_to_the_drain(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    inbox.append(_reading(), _raw(), "key0", now=NOW)
    (inbox.tmp_dir / "half-written.part").write_text("{not json")

    assert len(inbox.fetch_readings()) == 1


def test_unreadable_file_is_set_aside_not_deleted(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    inbox.append(_reading(), _raw(), "key0", now=NOW)
    (inbox.directory / "9999-bad.json").write_text("{ truncated")

    assert len(inbox.fetch_readings()) == 1
    assert (inbox.directory / "9999-bad.corrupt").exists()


def test_client_supplied_id_cannot_escape_the_directory(tmp_path):
    from garmin_stats_sync.health_connect import record_key

    inbox = Inbox(tmp_path / "inbox")
    hostile = {"metadata": {"id": "../../etc/passwd"}, "time": 1_756_150_200_000}
    path = inbox.append(_reading(), hostile, record_key(hostile), now=NOW)

    assert path.parent == inbox.directory
    assert path.resolve().is_relative_to(inbox.directory.resolve())


def test_prune_removes_confirmed_deliveries(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    inbox.append(_reading(), _raw(), "key0", now=NOW)
    state = State(path=tmp_path / "state.json")
    state.record(1_756_150_200)

    assert inbox.prune(state, retention_days=30, now=NOW) == 1
    assert inbox.fetch_readings() == []


def test_prune_keeps_a_reading_that_failed_out_of_order(tmp_path):
    """The bug the positive-proof predicate exists to avoid.

    r0 fails to upload while r1 and r2 succeed, so State.last_timestamp advances
    past r0. `not state.is_new(r0)` is then False - it looks synced - but it was
    never delivered. Pruning on that predicate would destroy the weigh-in.
    """
    inbox = Inbox(tmp_path / "inbox")
    r0, r1, r2 = 1_755_977_400, 1_756_063_800, 1_756_150_200
    inbox.append(_reading(r0), _raw(r0), "k0", now=NOW)

    state = State(path=tmp_path / "state.json")
    state.record(r1)
    state.record(r2)

    assert state.is_new(r0) is False  # the trap
    assert inbox.prune(state, retention_days=30, now=NOW) == 0
    assert [r.source_timestamp for r in inbox.fetch_readings()] == [r0]


def test_prune_sweeps_undelivered_entries_past_retention(tmp_path, caplog):
    inbox = Inbox(tmp_path / "inbox")
    old = NOW - timedelta(days=45)
    inbox.append(_reading(), _raw(), "key0", now=old)
    state = State(path=tmp_path / "state.json")

    with caplog.at_level("WARNING"):
        assert inbox.prune(state, retention_days=30, now=NOW) == 1

    assert inbox.fetch_readings() == []
    assert "never confirmed" in caplog.text


def test_prune_leaves_recent_undelivered_entries(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    inbox.append(_reading(), _raw(), "key0", now=NOW - timedelta(days=2))
    state = State(path=tmp_path / "state.json")

    assert inbox.prune(state, retention_days=30, now=NOW) == 0
    assert len(inbox.fetch_readings()) == 1


def test_prune_sweeps_stale_staging_files(tmp_path):
    import os

    inbox = Inbox(tmp_path / "inbox")
    inbox.append(_reading(), _raw(), "key0", now=NOW)
    stale = inbox.tmp_dir / "abandoned.part"
    stale.write_text("{")
    old = (NOW - timedelta(days=1)).timestamp()
    os.utime(stale, (old, old))

    inbox.prune(State(path=tmp_path / "state.json"), retention_days=30, now=NOW)
    assert not stale.exists()


def test_pending_reports_receipt_time(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    inbox.append(_reading(), _raw(), "key0", now=NOW)

    (entry,) = inbox.pending()
    assert entry.received_at == NOW
    assert entry.reading.weight_kg == 82.1


def test_missing_directory_is_not_an_error(tmp_path):
    assert Inbox(tmp_path / "nope").fetch_readings() == []


def test_prune_drops_readings_the_service_declined(tmp_path, caplog):
    """The phone backfills everything it holds; the server only wants readings
    newer than `since`. Those it declines are not failures."""
    inbox = Inbox(tmp_path / "inbox")
    old = 1_600_000_000  # well before NOW
    inbox.append(_reading(old), _raw(old), "old", now=NOW)
    state = State(path=tmp_path / "state.json")
    since = NOW - timedelta(days=7)

    with caplog.at_level("WARNING"):
        assert inbox.prune(state, retention_days=30, now=NOW, since=since) == 1

    assert inbox.fetch_readings() == []
    assert "never confirmed" not in caplog.text


def test_prune_keeps_recent_undelivered_readings_inside_the_window(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    recent = int((NOW - timedelta(days=1)).timestamp())
    inbox.append(_reading(recent), _raw(recent), "recent", now=NOW)
    state = State(path=tmp_path / "state.json")

    kept = inbox.prune(
        state, retention_days=30, now=NOW, since=NOW - timedelta(days=7)
    )

    assert kept == 0
    assert len(inbox.fetch_readings()) == 1
