"""Turn a raw getWeighingDataV2 response into Reading objects.

Pure functions only - no network, no pyvesync import - so the mapping can be
tested against recorded payloads.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from garmin_stats_sync.models import Reading

logger = logging.getLogger(__name__)

# VeSync sends epoch seconds; anything past this magnitude is milliseconds.
_MILLISECOND_THRESHOLD = 1e11


def parse_timestamp(raw: int | float) -> datetime:
    """Convert a VeSync timestamp to an aware UTC datetime."""
    seconds = raw / 1000 if raw > _MILLISECOND_THRESHOLD else raw
    return datetime.fromtimestamp(seconds, UTC)


def grams_to_kg(grams: int | float) -> float:
    """Convert the weightG field to kilograms."""
    return round(grams / 1000.0, 1)


def parse_readings(payload: Any) -> list[Reading]:
    """Extract readings from a getWeighingDataV2 response, oldest first.

    Rows without a usable weight or timestamp are skipped with a warning rather
    than failing the whole sync.
    """
    if not isinstance(payload, dict):
        raise TypeError(f"expected a response dict, got {type(payload).__name__}")

    result = payload.get("result") or {}
    rows = result.get("weightDatas") or []

    readings = []
    for row in rows:
        timestamp = row.get("timestamp")
        weight_g = row.get("weightG")
        if timestamp is None or not weight_g:
            logger.warning("skipping malformed weigh-in row: %s", row)
            continue
        readings.append(
            Reading(
                taken_at=parse_timestamp(timestamp),
                weight_kg=grams_to_kg(weight_g),
                sub_user_id=int(row.get("subUserID") or 0),
                source_timestamp=int(timestamp),
            )
        )

    readings.sort(key=lambda r: r.source_timestamp)
    return readings
