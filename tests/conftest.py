"""Shared fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def health_connect_payload() -> dict:
    """A recorded Health Connect read response, newest row first."""
    return json.loads((FIXTURES / "health_connect_weight.json").read_text())


@pytest.fixture
def config(tmp_path):
    """A duck-typed config carrying only what the code under test touches."""
    from types import SimpleNamespace

    return SimpleNamespace(
        garmin_email="",
        garmin_password="",
        data_dir=tmp_path,
        garth_dir=tmp_path / "garth",
        state_file=tmp_path / "state.json",
        inbox_dir=tmp_path / "inbox",
        runlog_file=tmp_path / "runlog.jsonl",
        ingest_token="t" * 32,
        ingest_host="127.0.0.1",
        ingest_port=0,
        ingest_max_body_bytes=65536,
        ingest_max_records=200,
        inbox_retention_days=30,
        public_url="",
    )
