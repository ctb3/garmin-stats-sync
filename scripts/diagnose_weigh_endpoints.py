#!/usr/bin/env python3
"""Search for the VeSync API call that returns this scale's weigh-in history.

No public client implements weigh-in retrieval for BT scales, so this probes
candidate endpoints and request bodies and prints the raw server reply for
each. It posts through pyvesync's authenticated session directly, bypassing the
library's error-code raising, so every real `code`/`msg` is visible.

Read the codes, not just the failures - a *different* error code means a
different rejection reason, which is the signal worth following:

    -11105079  MySQL error       server-side query failed (wrong endpoint shape)
    -11000079  illegal argument  endpoint exists, arguments rejected
    0          success           this is the one

Usage:

    docker compose run --rm diagnose
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from pyvesync import VeSync
from pyvesync.utils.helpers import Helpers

from garmin_stats_sync.vesync_client import DEVICE_LIST_ENDPOINT, _looks_like_scale

FALLBACK_BASE_URL = "https://smartapi.vesync.com"
REQUEST_DELAY_SECONDS = 0.3

SUBUSER_ENDPOINTS = [
    "/cloud/v1/deviceManaged/fatScale/getSubUserList",
    "/cloud/v1/deviceManaged/fatScale/getAllSubUser",
    "/cloud/v1/user/getSubUserList",
    "/cloud/v2/user/getSubUserList",
]

WEIGH_ENDPOINTS = [
    "/cloud/v1/deviceManaged/fatScale/getWeighData",
    "/cloud/v1/deviceManaged/fatScale/getAllWeighData",
    "/cloud/v1/deviceManaged/fatScale/getWeighDataV2",
    "/cloud/v1/deviceManaged/fatScale/getWeighingData",
    "/cloud/v2/deviceManaged/fatScale/getWeighingDataV2",
    "/cloud/v2/deviceManaged/getWeighingDataV2",
    "/cloud/v2/deviceManaged/getWeighingData",
    "/cloud/v1/deviceManaged/getWeighingData",
]

BYPASS_METHODS = ["getWeighingData", "getWeighData", "getWeightData", "getBodyData"]


def _base_url(manager: VeSync) -> str:
    getter = getattr(manager, "_api_base_url_for_current_region", None)
    return getter() if getter else FALLBACK_BASE_URL


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if key.lower() in {"token", "tk"} else _redact(val))
            for key, val in value.items()
        }
    return value


async def _post(manager: VeSync, endpoint: str, body: dict) -> dict:
    """POST without pyvesync's error-code raising."""
    async with manager.session.post(
        _base_url(manager) + endpoint,
        json=body,
        headers=Helpers.req_legacy_headers(manager),
    ) as response:
        text = await response.text()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"code": None, "msg": f"unparsed: {text[:200]}"}


def _rows(payload: dict) -> list:
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        return []
    for key in ("weightDatas", "weightData", "weighDatas", "list", "records"):
        rows = result.get(key)
        if isinstance(rows, list) and rows:
            return rows
    return []


def _label(payload: dict) -> str:
    return f"code={payload.get('code')} msg={payload.get('msg')!r}"


def _weigh_bodies(manager: VeSync, scale: dict, sub_user_id: int | None) -> list:
    """Candidate request bodies, from the most likely shape outward."""
    uuid = scale.get("uuid")
    config_module = scale.get("configModule")
    now = int(datetime.now(UTC).timestamp())
    year_ago = now - 365 * 24 * 3600
    sub_user = 0 if sub_user_id is None else sub_user_id

    def base(method: str) -> dict:
        body = Helpers.req_body(manager, "devicedetail")
        body["method"] = method
        return body

    def variants(method: str) -> list[tuple[str, dict]]:
        return [
            ("configModule", base(method) | {"configModule": config_module}),
            ("uuid", base(method) | {"uuid": uuid}),
            (
                "uuid+configModule+subUser",
                base(method)
                | {
                    "uuid": uuid,
                    "configModule": config_module,
                    "subUserID": sub_user,
                },
            ),
            (
                "uuid+page",
                base(method)
                | {"uuid": uuid, "page": 1, "pageSize": 10, "allData": True},
            ),
            (
                "uuid+timerange",
                base(method)
                | {
                    "uuid": uuid,
                    "subUserID": sub_user,
                    "startTime": year_ago,
                    "endTime": now,
                    "page": 1,
                    "pageSize": 10,
                },
            ),
            (
                "deviceId+configModel",
                base(method)
                | {
                    "deviceId": uuid,
                    "configModel": config_module,
                    "subUserID": sub_user,
                    "page": 1,
                    "pageSize": 10,
                },
            ),
        ]

    candidates = []
    for endpoint in WEIGH_ENDPOINTS:
        method = endpoint.rsplit("/", 1)[-1]
        for name, body in variants(method):
            candidates.append((f"{endpoint} [{name}]", endpoint, body))

    # bypassV2 is how pyvesync reaches most modern devices.
    for inner in BYPASS_METHODS:
        body = Helpers.req_body(manager, "bypassV2")
        body |= {
            "cid": scale.get("cid") or uuid,
            "configModule": config_module,
            "deviceId": uuid,
            "configModel": config_module,
            "userCountryCode": "US",
            "payload": {
                "method": inner,
                "source": "APP",
                "data": {"page": 1, "pageSize": 10, "subUserID": sub_user},
            },
        }
        candidates.append(
            (f"bypassV2 [{inner}]", "/cloud/v2/deviceManaged/bypassV2", body)
        )

    return candidates


async def _discover_sub_user(manager: VeSync, scale: dict) -> int | None:
    print("=== sub-user discovery ===")
    for endpoint in SUBUSER_ENDPOINTS:
        body = Helpers.req_body(manager, "devicedetail")
        body |= {
            "method": endpoint.rsplit("/", 1)[-1],
            "uuid": scale.get("uuid"),
            "configModule": scale.get("configModule"),
        }
        payload = await _post(manager, endpoint, body)
        print(f"{endpoint:55} {_label(payload)}")
        if payload.get("code") == 0:
            print(json.dumps(_redact(payload), indent=2)[:1500])
            rows = _rows(payload)
            for row in rows:
                if isinstance(row, dict) and row.get("subUserID") is not None:
                    return int(row["subUserID"])
        await asyncio.sleep(REQUEST_DELAY_SECONDS)
    return None


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
        print()

        sub_user_id = await _discover_sub_user(manager, scale)
        print(f"\nsub-user id in use: {sub_user_id if sub_user_id is not None else 0}\n")

        print("=== endpoint / body search ===")
        by_code: dict[Any, list[str]] = defaultdict(list)
        winners = []
        for label, endpoint, body in _weigh_bodies(manager, scale, sub_user_id):
            try:
                payload = await _post(manager, endpoint, body)
            except Exception as exc:  # noqa: BLE001 - diagnostic
                print(f"{label:70} EXCEPTION {exc}")
                continue

            rows = _rows(payload)
            print(f"{label:70} {_label(payload)} rows={len(rows)}")
            by_code[(payload.get("code"), payload.get("msg"))].append(label)
            if payload.get("code") == 0:
                winners.append((label, endpoint, body, payload))
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

        print("\n=== responses grouped by code ===")
        for (code, msg), labels in sorted(by_code.items(), key=lambda kv: str(kv[0])):
            print(f"code={code} msg={msg!r}  ({len(labels)} variants)")
            for label in labels[:3]:
                print(f"    {label}")
            if len(labels) > 3:
                print(f"    ... and {len(labels) - 3} more")

        if not winners:
            print("\nNo variant succeeded. Next step is capturing the VeSync app's")
            print("own request - see the troubleshooting section of the README.")
            return 1

        label, endpoint, body, payload = winners[0]
        print(f"\n=== SUCCESS: {label} ===")
        print("request body:")
        print(json.dumps(_redact(body), indent=2))
        print("response:")
        print(json.dumps(payload, indent=2)[:3000])

        for row in _rows(payload)[:3]:
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
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
