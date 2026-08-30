"""Config loading and process-timezone pinning."""

import time

import pytest

from garmin_stats_sync.config import ConfigError, load_config

REQUIRED = {
    "GARMIN_EMAIL": "runner@example.com",
    "GARMIN_PASSWORD": "garmin-secret",
    "INGEST_TOKEN": "x" * 32,
}


@pytest.fixture
def env(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LOCAL_TZ", "America/New_York")
    monkeypatch.setenv("DATA_DIR", "/data")
    yield monkeypatch
    time.tzset()


def test_paths_derive_from_data_dir(env):
    config = load_config()
    assert config.garth_dir.as_posix() == "/data/garth"
    assert config.inbox_dir.as_posix() == "/data/inbox"
    assert config.runlog_file.as_posix() == "/data/runlog.jsonl"
    assert config.state_file.as_posix() == "/data/state.json"


def test_garmin_credentials_are_optional(env):
    """Without them the service runs on cached tokens and the /login page."""
    env.delenv("GARMIN_EMAIL")
    env.delenv("GARMIN_PASSWORD")

    config = load_config()

    assert config.garmin_email == ""
    assert config.has_stored_credentials is False


def test_stored_credentials_are_detected(env):
    assert load_config().has_stored_credentials is True


def test_missing_ingest_token_fails_fast(env):
    env.delenv("INGEST_TOKEN")
    with pytest.raises(ConfigError, match="INGEST_TOKEN"):
        load_config()


def test_short_ingest_token_fails_fast(env):
    env.setenv("INGEST_TOKEN", "tooshort")
    with pytest.raises(ConfigError, match="at least 32 characters"):
        load_config()


def test_non_numeric_interval_is_a_config_error(env):
    env.setenv("SYNC_INTERVAL_SECONDS", "half an hour")
    with pytest.raises(ConfigError, match="SYNC_INTERVAL_SECONDS"):
        load_config()


def test_invalid_timezone_fails_fast(env):
    env.setenv("LOCAL_TZ", "Mars/Olympus_Mons")
    with pytest.raises(ConfigError, match="not a valid timezone"):
        load_config()


def test_process_timezone_is_pinned_to_local_tz(env):
    """garminconnect formats the local stamp via the process timezone."""
    load_config()
    assert time.tzname[0] in {"EST", "EDT"}


def test_dry_run_flag_parsing(env):
    env.setenv("DRY_RUN", "true")
    assert load_config().dry_run is True
    env.setenv("DRY_RUN", "")
    assert load_config().dry_run is False
