#!/usr/bin/env python3
"""Discovery tool: dump the raw VeSync device list and weigh-in history.

Confirms which endpoint answers for this scale, what units weightG uses, and
whether timestamps are epoch seconds or milliseconds. Run before trusting the
mapping:

    uv run python scripts/probe_vesync.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime

from pyvesync import VeSync
from pyvesync.utils.helpers import Helpers

from garmin_stats_sync.vesync_client import (
    DEVICE_LIST_ENDPOINT,
    LEGACY_WEIGH_DATA_ENDPOINT,
    WEIGH_DATA_ENDPOINT,
    _looks_like_scale,
)


async def main() -> int:
    email = os.environ.get("VESYNC_EMAIL")
    password = os.environ.get("VESYNC_PASSWORD")
    if not email or not password:
        print("set VESYNC_EMAIL and VESYNC_PASSWORD", file=sys.stderr)
        return 2

    tz = os.environ.get("LOCAL_TZ", "America/New_York")
    async with VeSync(username=email, password=password, time_zone=tz) as manager:
        if not await manager.login():
            print("login failed", file=sys.stderr)
            return 1

        body = Helpers.req_body(manager, "devicelist")
        response, status = await manager.async_call_api(
            DEVICE_LIST_ENDPOINT, "post", json_object=body
        )
        devices = ((response or {}).get("result") or {}).get("list") or []
        print(f"=== device list (HTTP {status}, {len(devices)} devices) ===")
        for device in devices:
            mark = "SCALE ->" if _looks_like_scale(device) else "        "
            print(
                f"{mark} name={device.get('deviceName')!r} "
                f"type={device.get('deviceType')!r} "
                f"configModule={device.get('configModule')!r}"
            )

        scales = [d for d in devices if _looks_like_scale(d)]
        if not scales:
            print("\nno scale matched; full device list follows")
            print(json.dumps(devices, indent=2))
            return 1

        config_module = scales[0].get("configModule")
        for endpoint in (WEIGH_DATA_ENDPOINT, LEGACY_WEIGH_DATA_ENDPOINT):
            weigh_body = Helpers.req_body(manager, "devicedetail")
            weigh_body |= {
                "page": 1,
                "pageSize": 10,
                "allData": True,
                "debugMode": False,
                "method": endpoint.rsplit("/", 1)[-1],
                "configModule": config_module,
            }
            payload, status = await manager.async_call_api(
                endpoint, "post", json_object=weigh_body
            )
            code = (payload or {}).get("code")
            print(f"\n=== {endpoint} (HTTP {status}, code {code}) ===")
            print(json.dumps(payload, indent=2)[:4000])

            rows = ((payload or {}).get("result") or {}).get("weightDatas") or []
            for row in rows[:5]:
                ts = row.get("timestamp")
                grams = row.get("weightG")
                if ts is None:
                    continue
                seconds = ts / 1000 if ts > 1e11 else ts
                when = datetime.fromtimestamp(seconds, UTC).isoformat()
                print(
                    f"  interpreted: {when} UTC  "
                    f"{(grams or 0) / 1000:.1f} kg / {(grams or 0) / 453.59237:.1f} lb"
                )
            if code == 0 and rows:
                print("\nSanity-check those kg/lb values against the VeSync app.")
                return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
