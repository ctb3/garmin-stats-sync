"""A capped record of recent sync runs, for the status page and /health."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

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
        for entry in reversed(self.recent(MAX_ENTRIES)):
            if entry.ok:
                return entry.when
        return None

    def consecutive_failures(self) -> int:
        count = 0
        for entry in reversed(self.recent(MAX_ENTRIES)):
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
