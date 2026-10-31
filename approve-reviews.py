#!/usr/bin/env python3
"""Approve aurral downloads held for review while few downloads are running.

aurral holds a download for review when its length or artist credit is a
little off from the requested track. This tool approves them in the order
the Activity queue lists them, but only while aurral has at most
--max-active downloads running, so approvals never compete with a full
download queue.

When an item cannot be approved because deemix's file is gone (404
"Staging file no longer exists"), it is denied instead, so aurral searches
for the track again. Items that can be neither approved nor denied (for
example 409, the job was removed) are remembered and skipped on later
checks, so they do not use up the --count approvals each time.

Modes:
  --once    check one time
  --watch   check every --interval minutes; exits by itself once aurral has
            nothing Queued, Searching or Downloading and nothing approvable
            left in review, on two checks in a row

Inputs:  command-line options (run with no arguments for help) and the INI
         config shared with cancel-stuck-downloads.py
         (see aurral-tools.cfg.example).
Outputs: one line per approval or failure, plus errors, on stdout/stderr.
Exit codes: 0 success, 1 aurral error or another run in progress,
2 usage or config error.
"""

import argparse
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

STATE_DIR = Path.home() / ".cache" / "aurral-approve-reviews"
SKIP_FILE = STATE_DIR / "skip.json"
LOCK_FILE = STATE_DIR / "lock"
# HTTP codes meaning a review item can never be approved
UNAPPROVABLE_CODES = (404, 409)
# Error aurral returns when the downloaded file to approve is gone
MISSING_FILE_ERROR = "Staging file no longer exists"


def positive_int(text: str) -> int:
    """argparse type: a whole number of at least 1."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}")
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def non_negative_int(text: str) -> int:
    """argparse type: a whole number of at least 0."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}")
    if value < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return value


def parse_args() -> argparse.Namespace:
    """Parse options; with no arguments, print help and exit."""
    parser = argparse.ArgumentParser(
        description="Approve aurral downloads held for review while few "
                    "downloads are running.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-o", "--once", action="store_true",
                      help="check one time and exit")
    mode.add_argument("-w", "--watch", action="store_true",
                      help="check every --interval minutes until aurral "
                           "has nothing left to download or approve")
    parser.add_argument("-a", "--max-active", type=non_negative_int,
                        default=3, metavar="N",
                        help="approve only while at most N downloads are "
                             "running (default: 3)")
    parser.add_argument("-k", "--count", type=positive_int, default=4,
                        metavar="N",
                        help="approve up to N items per check (default: 4)")
    parser.add_argument("-i", "--interval", type=positive_float, default=5.0,
                        metavar="MIN",
                        help="minutes between checks with --watch "
                             "(default: 5)")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="report what would be approved without "
                             "approving anything")
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


def attempt_key(item: dict) -> str:
    """Identify one download attempt.

    aurral keeps a job's ID when it searches again after a deny, but gives
    the new attempt a new requestedAt, so the pair names one attempt.
    """
    return f"{item.get('jobId')}@{item.get('requestedAt')}"


def load_skip() -> set[str]:
    """Download attempts already found to be unapprovable."""
    try:
        keys = json.loads(SKIP_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    # Older skip files held bare job IDs, which would hide later attempts
    return {key for key in keys if "@" in key}


def save_skip(keys: set[str]) -> None:
    """Write the skip list atomically."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=STATE_DIR, prefix=".skip.")
    with os.fdopen(handle, "w", encoding="utf-8") as temp:
        json.dump(sorted(keys), temp)
    os.replace(temp_name, SKIP_FILE)


def deny(aurral: Aurral, job_id: str, label: str) -> bool:
    """Deny a review item whose file is gone, so aurral searches again.

    Returns True if aurral accepted the deny.
    """
    try:
        aurral.call("POST", f"/api/playlists/jobs/{job_id}/deny")
    except (urllib.error.URLError, TimeoutError) as error:
        log(f"file gone and deny failed ({error}): {label}", error=True)
        return False
    log(f"denied, file gone (aurral will search again): {label}")
    return True


def check(aurral: Aurral, max_active: int, count: int,
          dry_run: bool) -> bool | None:
    """Run one check.

    Returns None if aurral could not be read, otherwise whether work is
    left (anything Queued, Searching or Downloading, or approvable reviews).
    """
    try:
        _, items = aurral.call("GET", "/api/requests")
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        log(f"cannot read aurral requests: {error}", error=True)
        return None
    skip = load_skip()
    busy = any(i.get("statusLabel") in ACTIVE_LABELS for i in items)
    active = sum(1 for i in items if i.get("statusLabel") == "Downloading")
    reviews = [i for i in items if i.get("statusLabel") == "Review"
               and i.get("jobId") and attempt_key(i) not in skip]
    if not reviews or active > max_active:
        return busy or bool(reviews)

    approved = 0
    for item in reviews:
        if approved >= count:
            break
        job_id = item["jobId"]
        label = (f"{item.get('trackName')} ({item.get('albumName')}): "
                 f"{item.get('subtitle')}")
        if dry_run:
            log(f"would approve: {label}")
            approved += 1
            continue
        try:
            aurral.call("POST", f"/api/playlists/jobs/{job_id}/approve")
            log(f"approved: {label}")
            approved += 1
        except urllib.error.HTTPError as error:
            reason = error.read()[:120].decode("utf-8", "replace")
            if error.code == 404 and MISSING_FILE_ERROR in reason:
                if not deny(aurral, job_id, label):
                    skip.add(attempt_key(item))
                continue
            log(f"cannot approve ({error.code} {reason}): {label}",
                error=True)
            if error.code in UNAPPROVABLE_CODES:
                skip.add(attempt_key(item))
        except (urllib.error.URLError, TimeoutError) as error:
            log(f"approve failed: {label}: {error}", error=True)
    if not dry_run:
        save_skip(skip)
    still_reviewable = any(attempt_key(i) not in skip for i in reviews)
    return busy or still_reviewable


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
        log("another approve-reviews run is in progress; exiting",
            error=True)
        return 1
    if args.once:
        work = check(aurral, args.max_active, args.count, args.dry_run)
        return 1 if work is None else 0
    log(f"watching every {args.interval:g} min; approving up to "
        f"{args.count} while at most {args.max_active} downloads run")
    idle_checks = 0
    try:
        while True:
            work = check(aurral, args.max_active, args.count, args.dry_run)
            # An unreadable aurral (None) is not proof everything is done
            idle_checks = idle_checks + 1 if work is False else 0
            if idle_checks >= IDLE_CHECKS_TO_FINISH:
                log("nothing left to download or approve; done")
                return 0
            time.sleep(args.interval * 60)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
