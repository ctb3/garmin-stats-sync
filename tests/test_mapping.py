"""Tests for VeSync payload -> Reading mapping."""

from datetime import UTC, datetime

import pytest

from garmin_stats_sync.mapping import (
    grams_to_kg,
    parse_readings,
    parse_timestamp,
)


def test_grams_to_kg_rounds_to_one_decimal():
    assert grams_to_kg(81_650) == 81.7
    assert grams_to_kg(100_000) == 100.0


def test_parse_timestamp_seconds():
    assert parse_timestamp(1_756_150_200) == datetime(2025, 8, 25, 19, 30, tzinfo=UTC)


def test_parse_timestamp_milliseconds():
    assert parse_timestamp(1_756_150_200_000) == datetime(
        2025, 8, 25, 19, 30, tzinfo=UTC
    )


def test_parse_readings_maps_all_fields(weigh_data_payload):
    readings = parse_readings(weigh_data_payload)

    assert len(readings) == 3
    newest = readings[-1]
    assert newest.weight_kg == 81.7
    assert newest.sub_user_id == 0
    assert newest.source_timestamp == 1_756_150_200
    assert newest.taken_at == datetime(2025, 8, 25, 19, 30, tzinfo=UTC)


def test_parse_readings_sorted_oldest_first(weigh_data_payload):
    readings = parse_readings(weigh_data_payload)
    assert [r.source_timestamp for r in readings] == sorted(
        r.source_timestamp for r in readings
    )


def test_parse_readings_skips_rows_missing_weight(weigh_data_payload):
    weigh_data_payload["result"]["weightDatas"].append(
        {"timestamp": 1_756_500_000, "subUserID": 0}
    )
    assert len(parse_readings(weigh_data_payload)) == 3


def test_parse_readings_handles_empty_result():
    assert parse_readings({"result": {"weightDatas": []}}) == []
    assert parse_readings({"result": None}) == []
    assert parse_readings({}) == []


def test_parse_readings_rejects_non_dict():
    with pytest.raises(TypeError):
        parse_readings("not a payload")
