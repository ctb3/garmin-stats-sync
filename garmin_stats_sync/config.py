"""Environment-driven configuration."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

# Short enough to brute-force is short enough to reject at startup rather than
# discover in production.
MIN_TOKEN_LENGTH = 32


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Config:
    garmin_email: str
    garmin_password: str
    local_tz: ZoneInfo
    data_dir: Path
    sync_interval_seconds: int
    dry_run: bool
    cold_start_days: int
    ingest_token: str
    ingest_host: str
    ingest_port: int
    ingest_max_body_bytes: int
    ingest_max_records: int
    inbox_retention_days: int
    public_url: str

    @property
    def garth_dir(self) -> Path:
        return self.data_dir / "garth"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def runlog_file(self) -> Path:
        return self.data_dir / "runlog.jsonl"

    @property
    def has_stored_credentials(self) -> bool:
        """Whether an unattended re-login is possible without the login page."""
        return bool(self.garmin_email and self.garmin_password)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set")
    return value


def _optional(name: str) -> str:
    return os.environ.get(name, "").strip()


def _flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip() or str(default)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


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

    ingest_token = _required("INGEST_TOKEN")
    if len(ingest_token) < MIN_TOKEN_LENGTH:
        raise ConfigError(
            f"INGEST_TOKEN must be at least {MIN_TOKEN_LENGTH} characters; "
            'generate one with: python -c "import secrets; '
            'print(secrets.token_urlsafe(32))"'
        )

    return Config(
        # Optional: without them the service runs on cached tokens and surfaces
        # the login page when they expire, so no password need ever be stored.
        garmin_email=_optional("GARMIN_EMAIL"),
        garmin_password=_optional("GARMIN_PASSWORD"),
        local_tz=tz,
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        sync_interval_seconds=_int("SYNC_INTERVAL_SECONDS", 1800),
        dry_run=_flag("DRY_RUN"),
        cold_start_days=_int("COLD_START_DAYS", 7),
        ingest_token=ingest_token,
        ingest_host=os.environ.get("INGEST_HOST", "0.0.0.0").strip() or "0.0.0.0",
        ingest_port=_int("INGEST_PORT", 8080),
        ingest_max_body_bytes=_int("INGEST_MAX_BODY_BYTES", 65536),
        ingest_max_records=_int("INGEST_MAX_RECORDS", 200),
        inbox_retention_days=_int("INBOX_RETENTION_DAYS", 30),
        public_url=_optional("PUBLIC_URL"),
    )
