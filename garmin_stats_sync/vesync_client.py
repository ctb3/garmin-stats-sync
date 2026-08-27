"""VeSync side: authenticate, find the scale, pull its weigh-in history.

pyvesync never merged smart-scale support, so the device list and the weigh-in
history are fetched as raw API calls through the library's authenticated
session (`async_call_api`). pyvesync still owns login, tokens and regions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pyvesync import VeSync
from pyvesync.utils.helpers import Helpers

from garmin_stats_sync.mapping import parse_readings
from garmin_stats_sync.models import Reading

logger = logging.getLogger(__name__)

DEVICE_LIST_ENDPOINT = "/cloud/v1/deviceManaged/devices"
WEIGH_DATA_ENDPOINT = "/cloud/v2/deviceManaged/getWeighingDataV2"
LEGACY_WEIGH_DATA_ENDPOINT = "/cloud/v1/deviceManaged/fatScale/getWeighData"

# Etekcity body scales report device types like "ESF00", "ESF93", "ESF-551".
SCALE_MARKERS = ("esf", "scale")

PAGE_SIZE = 100


class VeSyncError(RuntimeError):
    """Raised when VeSync login, discovery, or fetching fails."""


def _looks_like_scale(device: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(device.get(key, ""))
        for key in ("deviceType", "configModule", "deviceName", "type")
    ).lower()
    return any(marker in haystack for marker in SCALE_MARKERS)


class VeSyncScaleClient:
    """Synchronous facade over the async pyvesync manager."""

    def __init__(self, config) -> None:
        self._config = config
        self._config_module: str | None = None
        self._endpoint = WEIGH_DATA_ENDPOINT

    def fetch_readings(self) -> list[Reading]:
        """Return every weigh-in VeSync currently holds, oldest first."""
        return asyncio.run(self._fetch())

    async def _fetch(self) -> list[Reading]:
        async with VeSync(
            username=self._config.vesync_email,
            password=self._config.vesync_password,
            time_zone=str(self._config.local_tz),
        ) as manager:
            await self._authenticate(manager)
            config_module = await self._find_scale(manager)
            payload = await self._get_weigh_data(manager, config_module)
            return parse_readings(payload)

    async def _authenticate(self, manager: VeSync) -> None:
        """Reuse cached credentials when possible, otherwise log in and cache.

        pyvesync's credential methods are coroutines - awaiting them is not
        optional, since an un-awaited call is a truthy coroutine object that
        would leave the session unauthenticated.
        """
        credentials = self._config.vesync_credentials
        if credentials.exists() and await manager.load_credentials_from_file(
            credentials
        ):
            logger.debug("loaded cached VeSync credentials")
            return

        if not await manager.login():
            raise VeSyncError("VeSync login failed - check VESYNC_EMAIL/PASSWORD")
        credentials.parent.mkdir(parents=True, exist_ok=True)
        await manager.save_credentials(credentials)

    async def _find_scale(self, manager: VeSync) -> str:
        if self._config_module:
            return self._config_module

        body = Helpers.req_body(manager, "devicelist")
        response, status = await manager.async_call_api(
            DEVICE_LIST_ENDPOINT, "post", json_object=body
        )
        if not response or status != 200:
            raise VeSyncError(f"device list request failed (HTTP {status})")

        devices = (response.get("result") or {}).get("list") or []
        scales = [d for d in devices if _looks_like_scale(d)]
        if not scales:
            names = [d.get("deviceName") for d in devices]
            raise VeSyncError(f"no scale found on the account; devices seen: {names}")
        if len(scales) > 1:
            logger.warning("multiple scales found, using the first: %s", scales)

        scale = scales[0]
        self._config_module = scale.get("configModule")
        if not self._config_module:
            raise VeSyncError(f"scale has no configModule: {scale}")
        logger.info(
            "using scale %s (%s)", scale.get("deviceName"), scale.get("deviceType")
        )
        return self._config_module

    def _weigh_body(self, manager: VeSync, endpoint: str, config_module: str) -> dict:
        body = Helpers.req_body(manager, "devicedetail")
        body |= {
            "page": 1,
            "pageSize": PAGE_SIZE,
            "allData": True,
            "debugMode": False,
            "method": endpoint.rsplit("/", 1)[-1],
            "configModule": config_module,
        }
        return body

    async def _get_weigh_data(
        self, manager: VeSync, config_module: str
    ) -> dict[str, Any]:
        """Call getWeighingDataV2, falling back to the older fatScale endpoint.

        pyvesync raises on non-zero API codes rather than returning them, so
        each attempt is guarded - otherwise the first failure escapes and the
        fallback endpoint is never tried.
        """
        endpoints = [self._endpoint]
        if LEGACY_WEIGH_DATA_ENDPOINT not in endpoints:
            endpoints.append(LEGACY_WEIGH_DATA_ENDPOINT)

        errors = []
        for endpoint in endpoints:
            body = self._weigh_body(manager, endpoint, config_module)
            try:
                response, status = await manager.async_call_api(
                    endpoint,
                    "post",
                    json_object=body,
                    headers=Helpers.req_legacy_headers(manager),
                )
            except Exception as exc:
                logger.warning("%s failed: %s", endpoint, exc)
                errors.append(f"{endpoint}: {exc}")
                continue

            if response and status == 200:
                self._endpoint = endpoint
                return response

            code = (response or {}).get("code")
            logger.warning("%s returned HTTP %s code %s", endpoint, status, code)
            errors.append(f"{endpoint}: HTTP {status} code {code}")

        raise VeSyncError(
            "no weigh-in endpoint accepted the request - " + "; ".join(errors)
        )
