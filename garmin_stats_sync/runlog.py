"""A capped record of recent sync runs, for the status page and /health."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def local(when: datetime, tz: ZoneInfo) -> str:
    """A timestamp a human reads at a glance: local, to the second, one line.

    Entries are stored as UTC ISO so they stay unambiguous; only the rendering
    is localised.
    """
    return when.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")

MAX_ENTRIES = 200


@dataclass(frozen=True, slots=True)
class RunEntry:
    at: str
    trigger: str
    uploaded: int
    skipped: int
    failed: int
    fetched: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.failed == 0

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.at)

    def local_at(self, tz: ZoneInfo) -> str:
        return local(self.when, tz)


@dataclass(frozen=True, slots=True)
class Snapshot:
    entries: list[RunEntry]
    last_success: datetime | None
    consecutive_failures: int


class RunLog:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def append(self, entry: RunEntry) -> None:
        entries = [*self.recent(MAX_ENTRIES), entry][-MAX_ENTRIES:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for item in entries:
                handle.write(json.dumps(asdict(item)) + "\n")
        tmp.replace(self.path)

    def recent(self, limit: int = 50) -> list[RunEntry]:
        """Most recent last. A malformed line is skipped, never fatal."""
        if not self.path.exists():
            return []
        entries: list[RunEntry] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            logger.warning("unreadable run log %s", self.path)
            return []
        for line in lines[-limit:]:
            if not line.strip():
                continue
            try:
                entries.append(RunEntry(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return entries

    def last_success(self) -> datetime | None:
        return last_success(self.recent(MAX_ENTRIES))

    def consecutive_failures(self) -> int:
        return consecutive_failures(self.recent(MAX_ENTRIES))

    def snapshot(self, limit: int = 50) -> Snapshot:
        """Everything a status view needs, from a single read of the file.

        The convenience methods above each re-read it, which is fine for one
        call and wasteful for a page that wants all three at once.
        """
        entries = self.recent(MAX_ENTRIES)
        return Snapshot(
            entries=entries[-limit:],
            last_success=last_success(entries),
            consecutive_failures=consecutive_failures(entries),
        )


def last_success(entries: list[RunEntry]) -> datetime | None:
    for entry in reversed(entries):
        if entry.ok:
            return entry.when
    return None


def consecutive_failures(entries: list[RunEntry]) -> int:
    count = 0
    for entry in reversed(entries):
        if entry.ok:
            break
        count += 1
    return count


def entry_from_result(result, trigger: str, error: str | None = None) -> RunEntry:
    """Build an entry from a SyncResult."""
    return RunEntry(
        at=datetime.now(UTC).isoformat(),
        trigger=trigger,
        uploaded=result.uploaded,
        skipped=result.skipped,
        failed=result.failed,
        fetched=result.fetched,
        error=error or result.last_error,
    )
