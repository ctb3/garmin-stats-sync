"""Dedup state: which weigh-ins have already reached Garmin."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Epoch seconds stay below this well past the year 5000; anything larger is a
# millisecond value that leaked in from a source that reports them.
_MILLISECOND_MAGNITUDE = 1e11


@dataclass
class State:
    """Tracks synced weigh-ins so re-runs are no-ops.

    `last_timestamp` fast-rejects anything at or below the newest synced
    reading; `synced` catches rows that arrive out of order.
    """

    path: Path
    last_timestamp: int | None = None
    synced: list[int] = field(default_factory=list)

    MAX_SYNCED = 200

    @classmethod
    def load(cls, path: str | Path) -> State:
        path = Path(path)
        if not path.exists():
            return cls(path=path)
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("unreadable state file %s, starting cold", path)
            return cls(path=path)
        return cls(
            path=path,
            last_timestamp=data.get("last_timestamp"),
            synced=list(data.get("synced") or []),
        )

    def is_new(self, timestamp: int) -> bool:
        if timestamp in self.synced:
            return False
        return self.last_timestamp is None or timestamp > self.last_timestamp

    def record(self, timestamp: int) -> None:
        """Mark a weigh-in as delivered. Only call after a successful upload."""
        if timestamp > _MILLISECOND_MAGNITUDE:
            # `default_since` calls datetime.fromtimestamp(last_timestamp), which
            # raises on a millisecond value - and cmd_loop swallows the exception,
            # so the container would log "sync cycle failed" forever while looking
            # healthy. Fail loudly at the point the bad value enters instead.
            raise ValueError(
                f"timestamp {timestamp} looks like milliseconds; "
                "source_timestamp must be epoch seconds"
            )
        if timestamp not in self.synced:
            self.synced.append(timestamp)
        del self.synced[: -self.MAX_SYNCED]
        if self.last_timestamp is None or timestamp > self.last_timestamp:
            self.last_timestamp = timestamp

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"last_timestamp": self.last_timestamp, "synced": self.synced}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.path)
