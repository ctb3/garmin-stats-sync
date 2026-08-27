#!/usr/bin/env python3
"""Probe VeSync weigh-in endpoints and print the raw server reply for each.

pyvesync raises on non-zero API codes, which hides the actual response. This
posts directly through its authenticated session so every variant's real
`code`/`msg` is visible, then prints a table of what worked.

    docker compose run --rm sync-diagnose
    # or, outside Docker:
    VESYNC_EMAIL=... VESYNC_PASSWORD=... uv run python scripts/diagnose_weigh_endpoints.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

from pyvesync import VeSync
from pyvesync.utils.helpers import Helpers

from garmin_stats_sync.vesync_client import (
    DEVICE_LIST_ENDPOINT,
    LEGACY_WEIGH_DATA_ENDPOINT,
    WEIGH_DATA_ENDPOINT,
    _looks_like_scale,
)

FALLBACK_BASE_URL = "https://smartapi.vesync.com"


def _base_url(manager: VeSync) -> str:
    getter = getattr(manager, "_api_base_url_for_current_region", None)
    return getter() if getter else FALLBACK_BASE_URL


def _redact(value: Any) -> Any:
    """Keep tokens out of pasted output."""
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if key.lower() in {"token", "tk"} else _redact(val))
            for key, val in value.items()
        }
    return value


async def _post_raw(manager: VeSync, endpoint: str, body: dict, headers: dict) -> dict:
    """POST without pyvesync's error-code raising, so the reply is visible."""
    async with manager.session.post(
        _base_url(manager) + endpoint, json=body, headers=headers
    ) as response:
        text = await response.text()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"_unparsed": text[:500]}
        return {"http": response.status, "payload": payload}


def _variants(manager: VeSync, scale: dict) -> list[tuple[str, str, dict]]:
    """(label, endpoint, body) combinations worth trying."""
    config_module = scale.get("configModule")
    uuid = scale.get("uuid")
    cid = scale.get("cid")

    def base(method: str) -> dict:
        body = Helpers.req_body(manager, "devicedetail")
        body |= {
            "page": 1,
            "pageSize": 10,
            "allData": True,
            "debugMode": False,
            "method": method,
            "configModule": config_module,
        }
        return body

    v2 = "getWeighingDataV2"
    legacy = "getWeighData"

    return [
        ("v2 / configModule only", WEIGH_DATA_ENDPOINT, base(v2)),
        ("v2 / + subUserID", WEIGH_DATA_ENDPOINT, base(v2) | {"subUserID": 0}),
        ("v2 / + uuid + cid", WEIGH_DATA_ENDPOINT, base(v2) | {"uuid": uuid, "cid": cid}),
        (
            "v2 / + uuid + subUserID",
            WEIGH_DATA_ENDPOINT,
            base(v2) | {"uuid": uuid, "subUserID": 0},
        ),
        ("legacy fatScale", LEGACY_WEIGH_DATA_ENDPOINT, base(legacy)),
        (
            "legacy fatScale / + uuid",
            LEGACY_WEIGH_DATA_ENDPOINT,
            base(legacy) | {"uuid": uuid, "subUserID": 0},
        ),
    ]


def _summarise(payload: dict) -> tuple[str, int]:
    result = payload.get("result") or {}
    rows = result.get("weightDatas") or result.get("weightData") or []
    code = payload.get("code")
    msg = payload.get("msg")
    return f"code={code} msg={msg!r} rows={len(rows)}", len(rows)


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

        devices_response, _ = await manager.async_call_api(
            DEVICE_LIST_ENDPOINT,
            "post",
            json_object=Helpers.req_body(manager, "devicelist"),
            headers=Helpers.req_header_bypass(),
        )
        devices = ((devices_response or {}).get("result") or {}).get("list") or []
        scales = [d for d in devices if _looks_like_scale(d)]
        if not scales:
            print("no scale in device list:")
            print(json.dumps(_redact(devices), indent=2))
            return 1

        scale = scales[0]
        print("=== scale device record ===")
        print(json.dumps(_redact(scale), indent=2))

        header_sets = {
            "none": None,
            "legacy": Helpers.req_legacy_headers(manager),
            "bypass": Helpers.req_header_bypass(),
        }

        print("\n=== endpoint x header matrix ===")
        winners = []
        for label, endpoint, body in _variants(manager, scale):
            for header_name, headers in header_sets.items():
                try:
                    outcome = await _post_raw(manager, endpoint, body, headers or {})
                except Exception as exc:  # noqa: BLE001 - diagnostic
                    print(f"{label:26} headers={header_name:7} EXCEPTION {exc}")
                    continue
                summary, rows = _summarise(outcome["payload"])
                print(
                    f"{label:26} headers={header_name:7} "
                    f"http={outcome['http']} {summary}"
                )
                if rows:
                    winners.append((label, header_name, endpoint, outcome["payload"]))

        if not winners:
            print("\nNothing returned rows. Full reply from the first variant:")
            label, endpoint, body = _variants(manager, scale)[0]
            outcome = await _post_raw(
                manager, endpoint, body, Helpers.req_legacy_headers(manager)
            )
            print(json.dumps(outcome["payload"], indent=2)[:3000])
            return 1

        label, header_name, endpoint, payload = winners[0]
        print(f"\n=== WORKS: {label} with {header_name} headers on {endpoint} ===")
        rows = (payload.get("result") or {}).get("weightDatas") or []
        print(json.dumps(rows[:3], indent=2))
        for row in rows[:3]:
            ts = row.get("timestamp")
            grams = row.get("weightG")
            if ts is None or grams is None:
                continue
            seconds = ts / 1000 if ts > 1e11 else ts
            when = datetime.fromtimestamp(seconds, UTC).isoformat()
            print(
                f"  interpreted: {when} UTC  "
                f"{grams / 1000:.1f} kg / {grams / 453.59237:.1f} lb"
            )
        print("\nCheck those weights and dates against the VeSync app.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
