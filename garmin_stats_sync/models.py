"""The single value type shared between the VeSync and Garmin sides."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Reading:
    """One weigh-in.

    Attributes:
        taken_at: When the reading was taken, timezone-aware UTC.
        weight_kg: Weight in kilograms.
        sub_user_id: VeSync sub-user the reading belongs to.
        source_timestamp: Raw VeSync epoch-seconds value, used as the dedup key.
    """

    taken_at: datetime
    weight_kg: float
    sub_user_id: int
    source_timestamp: int
