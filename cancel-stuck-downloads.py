#!/usr/bin/env python3
"""Cancel aurral downloads that stay "Downloading" past a time limit.

aurral sometimes leaves a deemix download shown as Downloading after deemix
has finished it (lklynet/aurral#1447). This tool finds those jobs through
aurral's API and cancels them.

A job is cancelled only when both are true:
- aurral's start time for it (requestedAt) is more than --max-minutes ago;
- it was already Downloading on the previous check (kept in a state file),
  so one odd reading never cancels anything.

Modes:
  --once    check one time (for cron, e.g. every 4 minutes)
  --watch   check every --interval minutes; exits by itself once aurral has
            nothing Queued, Searching or Downloading on two checks in a row

Inputs:  command-line options (run with no arguments for help) and an INI
         config file with aurral's URL and API key, shared with
         approve-reviews.py (see aurral-tools.cfg.example).
Outputs: one line per cancelled job, plus errors, on stdout/stderr.
Exit codes: 0 success, 1 aurral error or another run in progress,
2 usage or config error.
"""

import argparse
import datetime
import fcntl
import json
import os
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

from aurral_api import (ACTIVE_LABELS, DEFAULT_CONFIG, IDLE_CHECKS_TO_FINISH,
                        Aurral, load_config, log, positive_float)

STATE_DIR = Path.home() / ".cache" / "aurral-stuck-downloads"
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "lock"


def parse_args() -> argparse.Namespace:
    """Parse options; with no arguments, print help and exit."""
    parser = argparse.ArgumentParser(
        description="Cancel aurral downloads that stay Downloading past a "
                    "time limit (workaround for lklynet/aurral#1447).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-o", "--once", action="store_true",
                      help="check one time and exit (for cron)")
    mode.add_argument("-w", "--watch", action="store_true",
                      help="check every --interval minutes until aurral "
                           "has nothing left to download")
    parser.add_argument("-m", "--max-minutes", type=positive_float,
                        default=10.0, metavar="MIN",
                        help="cancel jobs Downloading longer than this "
                             "(default: 10)")
    parser.add_argument("-i", "--interval", type=positive_float, default=4.0,
                        metavar="MIN",
                        help="minutes between checks with --watch "
                             "(default: 4)")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="report stuck jobs without cancelling them")
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG,
                        metavar="FILE",
                        help=f"config file with [aurral] url and api_key "
                             f"(default: {DEFAULT_CONFIG})")
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(2)
    args = parser.parse_args()
    if not (args.once or args.watch):
        parser.error("choose a mode: --once or --watch")
    return args


def load_state() -> set[str]:
    """Job IDs that were Downloading on the previous check."""
    try:
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def save_state(job_ids: set[str]) -> None:
    """Write the state file atomically."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=STATE_DIR, prefix=".state.")
    with os.fdopen(handle, "w", encoding="utf-8") as temp:
        json.dump(sorted(job_ids), temp)
    os.replace(temp_name, STATE_FILE)


def check(aurral: Aurral, max_minutes: float, dry_run: bool) -> bool | None:
    """Run one check.

    Returns None if aurral could not be read, otherwise whether aurral still
    has work (anything Queued, Searching or Downloading).
    """
    try:
        _, items = aurral.call("GET", "/api/requests")
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        log(f"cannot read aurral requests: {error}", error=True)
        return None
    busy = any(i.get("statusLabel") in ACTIVE_LABELS for i in items)
    now = datetime.datetime.now(datetime.timezone.utc)
    previous = load_state()
    downloading = [i for i in items if i.get("statusLabel") == "Downloading"
                   and i.get("jobId")]
    still_active = set()
    for item in downloading:
        job_id = item["jobId"]
        label = f"{item.get('trackName')} ({item.get('albumName')})"
        try:
            started = datetime.datetime.fromisoformat(
                str(item.get("requestedAt")).replace("Z", "+00:00"))
        except ValueError:
            still_active.add(job_id)
            continue
        minutes = (now - started).total_seconds() / 60
        if dry_run and minutes > max_minutes:
            when = ("now" if job_id in previous
                    else "on the next check (first sighting)")
            log(f"would cancel {when}, {minutes:.0f} min: {label}")
        if dry_run or minutes <= max_minutes or job_id not in previous:
            still_active.add(job_id)
            continue
        try:
            _, body = aurral.call("POST",
                                  f"/api/playlists/jobs/{job_id}/cancel")
            log(f"cancelled after {minutes:.0f} min: {label} "
                f"({body.get('status')})")
        except urllib.error.HTTPError as error:
            # aurral can time out but still cancel; the next check shows it
            log(f"cancel failed ({error.code}) after {minutes:.0f} min: "
                f"{label}", error=True)
            still_active.add(job_id)
        except (urllib.error.URLError, TimeoutError) as error:
            log(f"cancel failed after {minutes:.0f} min: {label}: {error}",
                error=True)
            still_active.add(job_id)
    if not dry_run:
        save_state(still_active)
    return busy


def main() -> int:
    """Run in the chosen mode."""
    args = parse_args()
    aurral = Aurral(*load_config(args.config))
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Kept open for the whole run so the lock holds
    lock = open(LOCK_FILE, "w", encoding="utf-8")  # noqa: SIM115
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another cancel-stuck-downloads run is in progress; exiting",
            error=True)
        return 1
    if args.once:
        busy = check(aurral, args.max_minutes, args.dry_run)
        return 1 if busy is None else 0
    log(f"watching every {args.interval:g} min; cancelling jobs Downloading "
        f"over {args.max_minutes:g} min")
    idle_checks = 0
    try:
        while True:
            busy = check(aurral, args.max_minutes, args.dry_run)
            # An unreadable aurral (None) is not proof the queue is done
            idle_checks = idle_checks + 1 if busy is False else 0
            if idle_checks >= IDLE_CHECKS_TO_FINISH:
                log("nothing Queued, Searching or Downloading; done")
                return 0
            time.sleep(args.interval * 60)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
