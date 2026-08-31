"""Mapping a posted Health Connect payload to Readings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from garmin_stats_sync.health_connect import (
    HealthConnectError,
    parse_payload,
    parse_record,
    record_key,
)

NOW = datetime(2025, 8, 26, tzinfo=UTC)


def _record(**overrides) -> dict:
    record = {
        "metadata": {"id": "abc", "dataOrigin": {"packageName": "com.example"}},
        "time": 1_756_150_200_000,
        "weight": {"kilograms": 82.1, "pounds": 181.0},
    }
    record.update(overrides)
    return record


def test_milliseconds_become_epoch_seconds():
    reading = parse_record(_record(), now=NOW)
    assert reading.source_timestamp == 1_756_150_200
    assert reading.taken_at == datetime.fromtimestamp(1_756_150_200, UTC)


def test_source_timestamp_stays_in_seconds_magnitude():
    # A millisecond key reaches State.last_timestamp and makes
    # datetime.fromtimestamp raise on every later run.
    reading = parse_record(_record(), now=NOW)
    datetime.fromtimestamp(reading.source_timestamp, UTC)  # must not raise


def test_kilograms_wins_and_pounds_is_ignored():
    record = _record(weight={"kilograms": 80.0, "pounds": 999.0})
    assert parse_record(record, now=NOW).weight_kg == 80.0


def test_weight_is_rounded_to_one_decimal():
    record = _record(weight={"kilograms": 81.92})
    assert parse_record(record, now=NOW).weight_kg == 81.9


def test_payload_is_sorted_oldest_first(health_connect_payload):
    readings = parse_payload(health_connect_payload, now=NOW)
    stamps = [r.source_timestamp for r in readings]
    assert stamps == sorted(stamps)
    assert [r.weight_kg for r in readings] == [81.7, 81.9, 82.1]


@pytest.mark.parametrize(
    "overrides",
    [
        {"time": None},
        {"time": "yesterday"},
        {"weight": {}},
        {"weight": {"pounds": 180.0}},
        {"weight": {"kilograms": "heavy"}},
    ],
    ids=["no-time", "text-time", "no-weight", "pounds-only", "text-weight"],
)
def test_malformed_records_raise(overrides):
    with pytest.raises(HealthConnectError):
        parse_record(_record(**overrides), now=NOW)


@pytest.mark.parametrize("kg", [0.5, 900.0], ids=["too-light", "too-heavy"])
def test_implausible_weights_raise(kg):
    with pytest.raises(HealthConnectError):
        parse_record(_record(weight={"kilograms": kg}), now=NOW)


def test_prehistoric_timestamp_raises():
    with pytest.raises(HealthConnectError):
        parse_record(_record(time=1_000_000), now=NOW)


def test_far_future_timestamp_raises():
    # Would poison State.last_timestamp and silently stop syncing for decades.
    future = int((NOW + timedelta(days=2)).timestamp()) * 1000
    with pytest.raises(HealthConnectError):
        parse_record(_record(time=future), now=NOW)


def test_slight_clock_skew_is_tolerated():
    soon = int((NOW + timedelta(hours=1)).timestamp()) * 1000
    assert parse_record(_record(time=soon), now=NOW).weight_kg == 82.1


def test_payload_without_records_raises():
    with pytest.raises(HealthConnectError):
        parse_payload({"pageToken": None}, now=NOW)


def test_non_dict_payload_raises():
    with pytest.raises(HealthConnectError):
        parse_payload([], now=NOW)


def test_record_key_is_stable_and_id_derived():
    assert record_key(_record()) == record_key(_record())
    other = _record(metadata={"id": "different"})
    assert record_key(other) != record_key(_record())


def test_record_key_without_id_falls_back_to_content():
    a = {"time": 1_756_150_200_000, "weight": {"kilograms": 80.0}}
    b = {"time": 1_756_150_200_000, "weight": {"kilograms": 81.0}}
    assert record_key(a) != record_key(b)


def test_record_key_never_yields_path_characters():
    key = record_key(_record(metadata={"id": "../../etc/passwd"}))
    assert key.isalnum()
    assert "/" not in key
