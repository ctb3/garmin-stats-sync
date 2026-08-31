#!/usr/bin/env python3
"""Log in to VeSync and print session values for use in Insomnia or curl.

Prints the account id, session token, region base URL and the scale's device
identifiers, formatted as an Insomnia environment plus an equivalent curl
command, so the API can be explored by hand.

    docker compose run --rm dump-session

The token is a live credential for the VeSync account. Keep the output local -
do not paste it into a bug report, issue or chat.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from pyvesync import VeSync
from pyvesync.utils.helpers import Helpers

from garmin_stats_sync.vesync_client import DEVICE_LIST_ENDPOINT, _looks_like_scale

VALIDATING_ENDPOINT = "/cloud/v1/deviceManaged/fatScale/getWeighData"


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

        getter = getattr(manager, "_api_base_url_for_current_region", None)
        base_url = getter() if getter else "https://smartapi.vesync.com"

        devices_response, _ = await manager.async_call_api(
            DEVICE_LIST_ENDPOINT,
            "post",
            json_object=Helpers.req_body(manager, "devicelist"),
            headers=Helpers.req_header_bypass(),
        )
        devices = ((devices_response or {}).get("result") or {}).get("list") or []
        scale = next((d for d in devices if _looks_like_scale(d)), {})

        environment = {
            "baseUrl": base_url,
            "token": manager.token,
            "accountId": manager.account_id,
            "timeZone": tz,
            "uuid": scale.get("uuid", ""),
            "configModule": scale.get("configModule", ""),
            "deviceName": scale.get("deviceName", ""),
            "deviceType": scale.get("deviceType", ""),
        }

        print("=== session values - the token is a live credential ===\n")
        print("Insomnia: Environment > Manage Environments, paste as JSON:\n")
        print(json.dumps(environment, indent=2))

        body = Helpers.req_body(manager, "devicedetail")
        body |= {
            "method": "getWeighData",
            "uuid": scale.get("uuid"),
            "configModule": scale.get("configModule"),
            "subUserID": 0,
            "page": 1,
            "pageSize": 10,
        }

        print("\n\ncurl for the endpoint that validates arguments:\n")
        print(
            f"curl -s -X POST '{base_url}{VALIDATING_ENDPOINT}' \\\n"
            "  -H 'content-type: application/json' \\\n"
            "  -H 'accept-language: en' \\\n"
            "  -H 'appVersion: 5.6.60' \\\n"
            f"  -H 'accountId: {manager.account_id}' \\\n"
            f"  -H 'tk: {manager.token}' \\\n"
            f"  -H 'tz: {tz}' \\\n"
            f"  -d '{json.dumps(body)}' | python3 -m json.tool"
        )
        print("\nWhat has already been tried: docs/vesync-api.md")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
