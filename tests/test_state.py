"""Tests for the dedup state file."""

from garmin_stats_sync.state import State


def test_cold_start_has_no_history(tmp_path):
    state = State.load(tmp_path / "state.json")
    assert state.last_timestamp is None
    assert state.is_new(1_756_150_200)


def test_recorded_timestamp_is_not_new(tmp_path):
    state = State.load(tmp_path / "state.json")
    state.record(1_756_150_200)
    assert not state.is_new(1_756_150_200)


def test_older_than_last_is_not_new(tmp_path):
    state = State.load(tmp_path / "state.json")
    state.record(1_756_150_200)
    assert not state.is_new(1_756_063_800)
    assert state.is_new(1_756_236_600)


def test_state_survives_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = State.load(path)
    state.record(1_756_150_200)
    state.save()

    reloaded = State.load(path)
    assert reloaded.last_timestamp == 1_756_150_200
    assert not reloaded.is_new(1_756_150_200)


def test_unrecorded_reading_stays_eligible(tmp_path):
    """A failed upload must not be marked synced."""
    path = tmp_path / "state.json"
    state = State.load(path)
    state.record(1_756_063_800)
    state.save()

    reloaded = State.load(path)
    assert reloaded.is_new(1_756_150_200)


def test_synced_list_is_capped(tmp_path):
    state = State.load(tmp_path / "state.json")
    for ts in range(1_700_000_000, 1_700_000_000 + State.MAX_SYNCED + 50):
        state.record(ts)
    assert len(state.synced) == State.MAX_SYNCED
    assert state.last_timestamp == 1_700_000_000 + State.MAX_SYNCED + 49


def test_corrupt_state_file_falls_back_to_cold_start(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    state = State.load(path)
    assert state.last_timestamp is None


def test_millisecond_timestamp_is_rejected(tmp_path):
    """A ms value reaches default_since, where fromtimestamp raises - and
    cmd_loop swallows that, so the service would fail silently forever."""
    import pytest

    state = State.load(tmp_path / "state.json")
    with pytest.raises(ValueError, match="milliseconds"):
        state.record(1_756_150_200_000)
