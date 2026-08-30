"""The single value type shared between the ingest and Garmin sides."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Reading:
    """One weigh-in.

    Attributes:
        taken_at: When the reading was taken, timezone-aware UTC.
        weight_kg: Weight in kilograms.
        source_timestamp: Epoch *seconds*, used as the dedup key. Health Connect
            reports milliseconds; they are divided down on the way in, because
            `default_since` feeds this value straight to `datetime.fromtimestamp`
            and a millisecond magnitude raises there.
    """

    taken_at: datetime
    weight_kg: float
    source_timestamp: int
