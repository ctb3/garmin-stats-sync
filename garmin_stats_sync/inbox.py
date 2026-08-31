"""Durable spool of received weigh-ins awaiting confirmed delivery to Garmin.

This is the only source the sync loop reads from. It is also the cache that lets
a reading arrive while the Garmin token is expired: the phone gets its 200, the
reading sits on disk, and it uploads once you log in again.

Throughput is roughly one reading a day, so nothing here is clever. The one part
that matters is ordering: append() does not return until the record is durably on
disk, because the HTTP handler answers 200 immediately afterwards and the phone
stops retrying at that point.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from garmin_stats_sync.models import Reading
from garmin_stats_sync.state import State

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class SpooledReading:
    reading: Reading
    received_at: datetime
    path: Path


class Inbox:
    """One JSON file per weigh-in under `directory`."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.tmp_dir = self.directory / "tmp"

    def append(
        self,
        reading: Reading,
        raw: dict[str, Any],
        key: str,
        now: datetime | None = None,
    ) -> Path:
        """Write a reading to the spool atomically. Returns its path."""
        now = now or datetime.now(UTC)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "version": FORMAT_VERSION,
            "taken_at": reading.taken_at.isoformat(),
            "weight_kg": reading.weight_kg,
            "source_timestamp": reading.source_timestamp,
            "received_at": now.isoformat(),
            # Kept so a reading can be re-derived if the mapping is later fixed.
            "raw": raw,
        }

        target = self.directory / f"{reading.source_timestamp}-{key}.json"
        # Unique per writer: handler threads may spool concurrently.
        part = self.tmp_dir / f"{target.stem}.{os.getpid()}.{uuid.uuid4().hex}.part"

        with part.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(part, target)
        self._fsync_dir()
        return target

    def _fsync_dir(self) -> None:
        """Make the rename itself durable, not just the file contents.

        Best effort: this fails on some bind-mounted filesystems (notably Docker
        Desktop on Windows), and a spool write must not fail for it.
        """
        try:
            fd = os.open(self.directory, os.O_RDONLY)
        except OSError as exc:
            logger.debug("cannot open inbox dir for fsync: %s", exc)
            return
        try:
            os.fsync(fd)
        except OSError as exc:
            logger.debug("inbox dir fsync unsupported here: %s", exc)
        finally:
            os.close(fd)

    def _load(self, path: Path) -> SpooledReading | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            reading = Reading(
                taken_at=datetime.fromisoformat(data["taken_at"]),
                weight_kg=float(data["weight_kg"]),
                source_timestamp=int(data["source_timestamp"]),
            )
            received_at = datetime.fromisoformat(data["received_at"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Rename rather than delete: one bad file must not stop the drain,
            # and destroying the evidence of a bug that ate a weigh-in is the
            # wrong default.
            logger.warning("unreadable spool file %s (%s), setting aside", path, exc)
            with_suffix = path.with_suffix(".corrupt")
            try:
                os.replace(path, with_suffix)
            except OSError:
                logger.exception("could not set aside %s", path)
            return None
        return SpooledReading(reading=reading, received_at=received_at, path=path)

    def _spooled(self) -> list[SpooledReading]:
        if not self.directory.is_dir():
            return []
        entries = [self._load(p) for p in sorted(self.directory.glob("*.json"))]
        found = [entry for entry in entries if entry is not None]
        found.sort(key=lambda entry: entry.reading.source_timestamp)
        return found

    def fetch_readings(self) -> list[Reading]:
        """The run_once seam. Oldest first."""
        return [entry.reading for entry in self._spooled()]

    def pending(self) -> list[SpooledReading]:
        """Everything still spooled, for the status page."""
        return self._spooled()

    def prune(
        self,
        state: State,
        retention_days: int,
        now: datetime | None = None,
        since: datetime | None = None,
    ) -> int:
        """Delete spool files that are confirmed delivered or past retention.

        Deletion requires *positive proof* of delivery - membership in
        `state.synced`, which sync.py writes only after a successful upload.
        The tempting `not state.is_new(ts)` is wrong: it also returns False for a
        reading whose upload failed out of order, so it would delete weigh-ins
        still owed to Garmin.

        Entries older than `since` are a separate case: the service has decided
        it will never send them, so they are dropped quietly rather than lingering
        until the age sweep reports them as undelivered. The phone backfills
        everything it holds, so on a cold start this is the normal path, not an
        error.

        The age sweep remains the backstop for anything neither delivered nor
        declined - a reading that failed out of order, say.
        """
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(days=retention_days)
        removed = 0

        for entry in self._spooled():
            delivered = entry.reading.source_timestamp in state.synced
            declined = since is not None and entry.reading.taken_at < since
            if declined and not delivered:
                logger.info(
                    "dropping weigh-in older than the sync window: %.1f kg taken %s",
                    entry.reading.weight_kg,
                    entry.reading.taken_at.isoformat(),
                )
            elif not delivered and entry.received_at > cutoff:
                continue
            elif not delivered:
                logger.warning(
                    "dropping spooled weigh-in never confirmed to Garmin: "
                    "%.1f kg taken %s, received %s",
                    entry.reading.weight_kg,
                    entry.reading.taken_at.isoformat(),
                    entry.received_at.isoformat(),
                )
            try:
                entry.path.unlink()
            except OSError:
                logger.exception("could not remove spool file %s", entry.path)
                continue
            removed += 1

        # Staging files from a crash mid-write are invisible to the *.json glob,
        # so sweep them on the same schedule.
        if self.tmp_dir.is_dir():
            stale = now - timedelta(hours=1)
            for part in self.tmp_dir.glob("*.part"):
                try:
                    mtime = datetime.fromtimestamp(part.stat().st_mtime, UTC)
                    if mtime < stale:
                        part.unlink()
                except OSError:
                    continue
        return removed
