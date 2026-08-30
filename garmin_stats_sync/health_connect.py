"""Turn a posted Health Connect payload into Reading objects.

Pure functions only - no network, no filesystem - so the mapping can be tested
against recorded payloads.

Unlike a cloud poller, which can only log and skip a bad row, this parser has a
caller on the other end of a socket who can be told 400. So it raises: silently
dropping a weigh-in the phone believes it delivered is exactly the failure the
spool exists to prevent.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from garmin_stats_sync.models import Reading

logger = logging.getLogger(__name__)

MILLIS = 1000

# A human on a bathroom scale. Anything outside this is a malformed record or a
# unit confusion, not a weigh-in.
MIN_WEIGHT_KG = 2.0
MAX_WEIGHT_KG = 500.0

# 2000-01-01. Rejects zero, negatives and epoch-adjacent garbage.
MIN_EPOCH_SECONDS = 946_684_800

# A far-future timestamp would set State.last_timestamp beyond every real reading
# and, via the is_new fast-reject, silently disable syncing for decades. Bound it.
FUTURE_TOLERANCE_SECONDS = 86_400


class HealthConnectError(ValueError):
    """Raised when a posted payload is unusable. Maps to HTTP 400."""


def _weight_kg(record: dict[str, Any]) -> float:
    weight = record.get("weight")
    if not isinstance(weight, dict):
        raise HealthConnectError("record has no weight object")

    # kilograms is authoritative; pounds is ignored entirely. Two representations
    # of one quantity have no principled tiebreak when they disagree.
    raw = weight.get("kilograms")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        raise HealthConnectError("weight.kilograms is missing or not a number")

    kg = round(float(raw), 1)
    if not MIN_WEIGHT_KG <= kg <= MAX_WEIGHT_KG:
        raise HealthConnectError(
            f"weight {kg} kg is outside {MIN_WEIGHT_KG}-{MAX_WEIGHT_KG} kg"
        )
    return kg


def _epoch_seconds(record: dict[str, Any], now: datetime) -> int:
    raw = record.get("time")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        raise HealthConnectError("record has no numeric time")

    seconds = int(raw) // MILLIS
    if seconds < MIN_EPOCH_SECONDS:
        raise HealthConnectError(f"time {raw} predates 2000-01-01")

    horizon = int(now.timestamp()) + FUTURE_TOLERANCE_SECONDS
    if seconds > horizon:
        raise HealthConnectError(f"time {raw} is more than 24h in the future")
    return seconds


def record_key(record: dict[str, Any]) -> str:
    """A stable spool identity for a record.

    Hashed, never interpolated: metadata.id is client-supplied and must never
    reach a filesystem path. Falls back to the record's content when the id is
    missing, so two distinct records cannot collide onto one filename.
    """
    metadata = record.get("metadata")
    identifier = metadata.get("id") if isinstance(metadata, dict) else None
    if not isinstance(identifier, str) or not identifier:
        identifier = json.dumps(record, sort_keys=True, default=str)
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]


def parse_record(record: Any, now: datetime | None = None) -> Reading:
    """Map one Health Connect WeightRecord to a Reading."""
    if not isinstance(record, dict):
        raise HealthConnectError(
            f"expected a record object, got {type(record).__name__}"
        )
    now = now or datetime.now(UTC)

    seconds = _epoch_seconds(record, now)
    return Reading(
        taken_at=datetime.fromtimestamp(seconds, UTC),
        weight_kg=_weight_kg(record),
        source_timestamp=seconds,
    )


def parse_payload(payload: Any, now: datetime | None = None) -> list[Reading]:
    """Map a posted payload to Readings, oldest first.

    Raises on a structurally invalid payload. Individual bad records are the
    caller's problem to report per-record, so use parse_record directly when you
    want to accept a partial batch.
    """
    if not isinstance(payload, dict):
        raise HealthConnectError(
            f"expected a payload object, got {type(payload).__name__}"
        )

    records = payload.get("records")
    if not isinstance(records, list):
        raise HealthConnectError("payload has no records list")

    readings = [parse_record(record, now) for record in records]
    readings.sort(key=lambda r: r.source_timestamp)
    return readings
