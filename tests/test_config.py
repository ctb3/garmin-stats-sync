"""Config loading and process-timezone pinning."""

import time

import pytest

from garmin_stats_sync.config import ConfigError, load_config

REQUIRED = {
    "VESYNC_EMAIL": "scale@example.com",
    "VESYNC_PASSWORD": "vesync-secret",
    "GARMIN_EMAIL": "runner@example.com",
    "GARMIN_PASSWORD": "garmin-secret",
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
    assert config.vesync_credentials.as_posix() == "/data/vesync.json"
    assert config.state_file.as_posix() == "/data/state.json"


def test_missing_credential_fails_fast(env):
    env.delenv("GARMIN_PASSWORD")
    with pytest.raises(ConfigError, match="GARMIN_PASSWORD"):
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
