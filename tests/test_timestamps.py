"""Garmin timestamp pair formatting."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from garmin_stats_sync.garmin_client import format_timestamps

NY = ZoneInfo("America/New_York")


def test_same_day_pair():
    taken_at = datetime(2025, 8, 25, 13, 30, tzinfo=UTC)
    local, gmt = format_timestamps(taken_at, NY)
    assert local == "2025-08-25T09:30:00"
    assert gmt == "2025-08-25T13:30:00"


def test_evening_local_is_next_day_utc():
    """A 21:30 local weigh-in must land on the local day, not the UTC day."""
    taken_at = datetime(2025, 8, 26, 1, 30, tzinfo=UTC)
    local, gmt = format_timestamps(taken_at, NY)
    assert local == "2025-08-25T21:30:00"
    assert gmt == "2025-08-26T01:30:00"


def test_naive_input_is_treated_as_utc():
    local, gmt = format_timestamps(datetime(2025, 8, 26, 1, 30), NY)
    assert local == "2025-08-25T21:30:00"
    assert gmt == "2025-08-26T01:30:00"
