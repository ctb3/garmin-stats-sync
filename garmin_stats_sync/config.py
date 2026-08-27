"""Environment-driven configuration."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Config:
    vesync_email: str
    vesync_password: str
    garmin_email: str
    garmin_password: str
    local_tz: ZoneInfo
    data_dir: Path
    sync_interval_seconds: int
    dry_run: bool
    cold_start_days: int

    @property
    def garth_dir(self) -> Path:
        return self.data_dir / "garth"

    @property
    def vesync_credentials(self) -> Path:
        return self.data_dir / "vesync.json"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set")
    return value


def load_config() -> Config:
    """Build a Config from the environment, failing fast on missing values."""
    tz_name = os.environ.get("LOCAL_TZ", "UTC").strip() or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:  # noqa: BLE001 - surfaced as a config error
        raise ConfigError(f"LOCAL_TZ {tz_name!r} is not a valid timezone") from exc

    # garminconnect builds its local timestamp with datetime.astimezone(), which
    # reads the process timezone - so pin the process to LOCAL_TZ.
    os.environ["TZ"] = tz_name
    if hasattr(time, "tzset"):
        time.tzset()

    return Config(
        vesync_email=_required("VESYNC_EMAIL"),
        vesync_password=_required("VESYNC_PASSWORD"),
        garmin_email=_required("GARMIN_EMAIL"),
        garmin_password=_required("GARMIN_PASSWORD"),
        local_tz=tz,
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        sync_interval_seconds=int(os.environ.get("SYNC_INTERVAL_SECONDS", "1800")),
        dry_run=os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"},
        cold_start_days=int(os.environ.get("COLD_START_DAYS", "7")),
    )
