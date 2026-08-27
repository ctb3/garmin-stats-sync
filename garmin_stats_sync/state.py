"""Dedup state: which weigh-ins have already reached Garmin."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


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
