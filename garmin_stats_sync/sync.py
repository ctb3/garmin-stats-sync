"""Sync policy: what gets uploaded, what gets skipped, what gets retried."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from garmin_stats_sync.models import Reading
from garmin_stats_sync.state import State

logger = logging.getLogger(__name__)

_RELATIVE = re.compile(r"^(\d+)([dh])$")


@dataclass(frozen=True, slots=True)
class SyncResult:
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    fetched: int = 0
    # Why the last failure happened, for the status page. Garmin's errors are
    # often actionable ("upload consent is not yet granted"), and a bare count
    # sends you to the container logs to find that out.
    last_error: str | None = None

    def summary(self) -> str:
        return (
            f"{self.fetched} fetched, {self.uploaded} uploaded, "
            f"{self.skipped} already synced, {self.failed} failed"
        )


def parse_since(value: str, now: datetime | None = None) -> datetime:
    """Parse a --since value: `7d`, `12h`, `2025-08-01`, or `all`."""
    now = now or datetime.now(UTC)
    value = value.strip().lower()

    if value == "all":
        return datetime(1970, 1, 1, tzinfo=UTC)

    match = _RELATIVE.match(value)
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        delta = timedelta(days=amount) if unit == "d" else timedelta(hours=amount)
        return now - delta

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"cannot parse --since {value!r}; use 7d, 12h, YYYY-MM-DD, or all"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def default_since(state: State, now: datetime, cold_start_days: int) -> datetime:
    """Cold starts only look back a window, so a fresh deploy is not a backfill."""
    if state.last_timestamp is None:
        return now - timedelta(days=cold_start_days)
    return datetime.fromtimestamp(state.last_timestamp, UTC)


def run_once(
    source,
    garmin,
    state: State,
    since: datetime,
    now: datetime | None = None,
    dry_run: bool = False,
) -> SyncResult:
    """Fetch weigh-ins, upload the new ones, record only what succeeded."""
    now = now or datetime.now(UTC)
    readings: list[Reading] = source.fetch_readings()

    uploaded = skipped = failed = 0
    last_error: str | None = None
    for reading in readings:
        if reading.taken_at < since:
            skipped += 1
            continue
        if not state.is_new(reading.source_timestamp):
            skipped += 1
            continue

        if dry_run:
            logger.info(
                "DRY RUN would upload %.1f kg taken %s",
                reading.weight_kg,
                reading.taken_at.isoformat(),
            )
            uploaded += 1
            continue

        try:
            garmin.upload_weight(reading)
        except Exception as exc:
            logger.exception(
                "upload failed for weigh-in at %s, will retry next run",
                reading.taken_at.isoformat(),
            )
            last_error = str(exc)
            failed += 1
            continue

        logger.info(
            "uploaded %.1f kg taken %s", reading.weight_kg, reading.taken_at.isoformat()
        )
        state.record(reading.source_timestamp)
        uploaded += 1

    if uploaded and not dry_run:
        state.save()

    return SyncResult(
        uploaded=uploaded,
        skipped=skipped,
        failed=failed,
        fetched=len(readings),
        last_error=last_error,
    )
