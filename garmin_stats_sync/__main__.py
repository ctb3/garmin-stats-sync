"""CLI entry point: sync, loop, and the one-time credential bootstraps."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import UTC, datetime

from garmin_stats_sync.config import Config, ConfigError, load_config
from garmin_stats_sync.garmin_client import GarminUploader
from garmin_stats_sync.state import State
from garmin_stats_sync.sync import default_since, parse_since, run_once
from garmin_stats_sync.vesync_client import VeSyncScaleClient

logger = logging.getLogger("garmin_stats_sync")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def _resolve_since(config: Config, state: State, since: str | None) -> datetime:
    now = datetime.now(UTC)
    if since:
        return parse_since(since, now=now)
    return default_since(state, now, config.cold_start_days)


def cmd_sync(config: Config, args) -> int:
    state = State.load(config.state_file)
    since = _resolve_since(config, state, args.since)
    dry_run = args.dry_run or config.dry_run

    logger.info("syncing weigh-ins taken since %s", since.isoformat())
    result = run_once(
        VeSyncScaleClient(config),
        GarminUploader(config),
        state,
        since=since,
        dry_run=dry_run,
    )
    logger.info(result.summary())
    return 1 if result.failed else 0


def cmd_loop(config: Config, args) -> int:
    logger.info("starting sync loop every %ss", config.sync_interval_seconds)
    while True:
        try:
            cmd_sync(config, args)
        except Exception:
            logger.exception("sync cycle failed, retrying next interval")
        # Only the first cycle honours an explicit --since.
        args.since = None
        time.sleep(config.sync_interval_seconds)


def cmd_bootstrap_garmin(config: Config, _args) -> int:
    from garminconnect import Garmin

    config.garth_dir.mkdir(parents=True, exist_ok=True)
    client = Garmin(
        email=config.garmin_email,
        password=config.garmin_password,
        prompt_mfa=lambda: input("Garmin MFA code: ").strip(),
    )
    client.login(tokenstore=str(config.garth_dir))
    logger.info("garmin tokens stored in %s", config.garth_dir)
    return 0


def cmd_bootstrap_vesync(config: Config, _args) -> int:
    from pyvesync import VeSync

    async def _run() -> int:
        async with VeSync(
            username=config.vesync_email,
            password=config.vesync_password,
            time_zone=str(config.local_tz),
        ) as manager:
            if not await manager.login():
                logger.error("VeSync login failed")
                return 1
            config.vesync_credentials.parent.mkdir(parents=True, exist_ok=True)
            manager.save_credentials(config.vesync_credentials)
            logger.info("vesync credentials stored in %s", config.vesync_credentials)
            return 0

    return asyncio.run(_run())


COMMANDS = {
    "sync": cmd_sync,
    "loop": cmd_loop,
    "bootstrap-garmin": cmd_bootstrap_garmin,
    "bootstrap-vesync": cmd_bootstrap_vesync,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="garmin-stats-sync")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument(
        "--since",
        help="only sync weigh-ins newer than this: 7d, 12h, YYYY-MM-DD, or all",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be uploaded without touching Garmin",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("configuration error: %s", exc)
        return 2
    return COMMANDS[args.command](config, args)


if __name__ == "__main__":
    raise SystemExit(main())
