"""CLI entry point: sync, loop, and the one-time Garmin bootstrap."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from datetime import UTC, datetime

from garmin_stats_sync.config import Config, ConfigError, load_config
from garmin_stats_sync.garmin_client import GarminUploader
from garmin_stats_sync.http_ingest import App, build_server, start_in_thread
from garmin_stats_sync.inbox import Inbox
from garmin_stats_sync.runlog import RunLog, entry_from_result
from garmin_stats_sync.state import State
from garmin_stats_sync.sync import default_since, parse_since, run_once

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


def cmd_sync(config: Config, args, trigger: str = "manual") -> int:
    state = State.load(config.state_file)
    since = _resolve_since(config, state, args.since)
    dry_run = args.dry_run or config.dry_run
    inbox = Inbox(config.inbox_dir)
    runlog = RunLog(config.runlog_file)

    logger.info("syncing weigh-ins taken since %s", since.isoformat())
    result = run_once(
        inbox,
        GarminUploader(config),
        state,
        since=since,
        dry_run=dry_run,
    )
    logger.info(result.summary())

    if not dry_run:
        # Only entries with positive proof of delivery are removed; see
        # Inbox.prune for why `not state.is_new(...)` would be wrong here.
        removed = inbox.prune(state, config.inbox_retention_days)
        if removed:
            logger.debug("pruned %s delivered weigh-in(s) from the spool", removed)

    # Recorded even for a dry run: it changes nothing, and the status page is
    # most useful precisely while you are testing.
    runlog.append(entry_from_result(result, trigger))
    return 1 if result.failed else 0


def cmd_loop(config: Config, args) -> int:
    wake = threading.Event()
    stop = threading.Event()

    def _stop(_signum, _frame) -> None:
        stop.set()
        wake.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    inbox = Inbox(config.inbox_dir)
    runlog = RunLog(config.runlog_file)
    app = App(config, inbox, runlog, wake)

    # Started before the first cycle: that cycle can take a minute against
    # Garmin, and the phone should not meet a refused connection during it. A
    # bind failure is fatal so the container restarts rather than running deaf.
    server = build_server(app)
    thread = start_in_thread(server)
    logger.info(
        "ingest listening on %s:%s", config.ingest_host, config.ingest_port
    )
    logger.info("sync loop every %ss, or immediately on ingest",
                config.sync_interval_seconds)

    try:
        while not stop.is_set():
            trigger = "event" if wake.is_set() else "interval"
            wake.clear()
            try:
                cmd_sync(config, args, trigger=trigger)
            except Exception:
                logger.exception("sync cycle failed, retrying next interval")
            # Only the first cycle honours an explicit --since.
            args.since = None
            if not thread.is_alive():
                logger.error("ingest listener died; restart the service")
            # Waking on the event turns the interval into a floor rather than
            # the only trigger, and lets SIGTERM exit promptly instead of
            # sitting out the full sleep and getting SIGKILLed.
            wake.wait(timeout=config.sync_interval_seconds)
    finally:
        server.shutdown()
        server.server_close()
    return 0


def cmd_bootstrap_garmin(config: Config, _args) -> int:
    from garminconnect import Garmin

    if not config.has_stored_credentials:
        logger.error(
            "GARMIN_EMAIL/GARMIN_PASSWORD are not set; use the /login page instead"
        )
        return 2

    config.garth_dir.mkdir(parents=True, exist_ok=True)
    client = Garmin(
        email=config.garmin_email,
        password=config.garmin_password,
        prompt_mfa=lambda: input("Garmin MFA code: ").strip(),
    )
    client.login(tokenstore=str(config.garth_dir))
    logger.info("garmin tokens stored in %s", config.garth_dir)
    return 0


COMMANDS = {
    "sync": cmd_sync,
    "loop": cmd_loop,
    "bootstrap-garmin": cmd_bootstrap_garmin,
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
