"""Garmin Connect side: authenticate once, upload weigh-ins."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, tzinfo

from garminconnect import Garmin

from garmin_stats_sync.models import Reading

logger = logging.getLogger(__name__)

REAUTH_MESSAGE = (
    "GARMIN REAUTH REQUIRED - run `docker compose run --rm sync bootstrap-garmin`"
)


def format_timestamps(taken_at: datetime, local_tz: tzinfo) -> tuple[str, str]:
    """Return (local, gmt) naive ISO strings for a weigh-in.

    Garmin records the weigh-in against the *local* date, so an evening weigh-in
    that is already tomorrow in UTC must keep today's local date.
    """
    if taken_at.tzinfo is None:
        taken_at = taken_at.replace(tzinfo=UTC)
    local = taken_at.astimezone(local_tz).replace(tzinfo=None)
    gmt = taken_at.astimezone(UTC).replace(tzinfo=None)
    return local.isoformat(timespec="seconds"), gmt.isoformat(timespec="seconds")


class GarminUploader:
    """Uploads weight to Garmin Connect using stored OAuth tokens."""

    def __init__(self, config) -> None:
        self._config = config
        self._client: Garmin | None = None

    def _login(self) -> Garmin:
        tokenstore = str(self._config.garth_dir)
        client = Garmin(
            email=self._config.garmin_email,
            password=self._config.garmin_password,
            prompt_mfa=_no_interactive_mfa,
        )
        client.login(tokenstore=tokenstore)
        return client

    @property
    def client(self) -> Garmin:
        if self._client is None:
            self._client = self._login()
        return self._client

    def upload_weight(self, reading: Reading) -> None:
        local, gmt = format_timestamps(reading.taken_at, self._config.local_tz)
        try:
            self._post(reading.weight_kg, local, gmt)
        except Exception:
            logger.info("garmin upload failed, retrying once with a fresh login")
            self._client = None
            self._post(reading.weight_kg, local, gmt)

    def _post(self, weight_kg: float, local: str, gmt: str) -> None:
        self.client.add_weigh_in_with_timestamps(
            weight=weight_kg, unitKey="kg", dateTimestamp=local, gmtTimestamp=gmt
        )


def _no_interactive_mfa() -> str:
    raise RuntimeError(REAUTH_MESSAGE)
