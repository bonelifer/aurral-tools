#!/usr/bin/env python3
"""Ask aurral to rescan its library, and optionally wait for it to finish.

- quick: checks every folder, reads new and changed files, and forgets
  files that are gone
- full: reads the tags of every file again (use after retagging files)

Inputs:  command-line options (run with no arguments for help) and the
         shared aurral-tools.cfg (aurral URL and API key).
Outputs: the scan job and, with --wait, its final status on stdout.
Exit codes: 0 scan queued (or finished with --wait), 1 aurral error,
the scan failed, or --wait timed out, 2 usage or config error.
"""

import argparse
import sys
import time
import urllib.error
from pathlib import Path

from aurral_api import (DEFAULT_CONFIG, Aurral, load_config, log,
                        positive_float)

POLL_SECONDS = 5
FINISHED = ("completed", "failed", "error", "cancelled")


def parse_args() -> argparse.Namespace:
    """Parse options; with no arguments, print help and exit."""
    parser = argparse.ArgumentParser(
        description="Ask aurral to rescan its library.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-q", "--quick", action="store_true",
                      help="quick scan: new, changed and removed files")
    mode.add_argument("-f", "--full", action="store_true",
                      help="full scan: read every file's tags again")
    parser.add_argument("-w", "--wait", action="store_true",
                        help="wait until the scan finishes")
    parser.add_argument("-t", "--timeout", type=positive_float, default=30.0,
                        metavar="MIN",
                        help="give up waiting after this many minutes "
                             "(default: 30)")
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG,
                        metavar="FILE",
                        help=f"config file with [aurral] url and api_key "
                             f"(default: {DEFAULT_CONFIG})")
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(2)
    args = parser.parse_args()
    if not (args.quick or args.full):
        parser.error("choose a scan: --quick or --full")
    return args


def main() -> int:
    """Queue the scan and optionally wait for it."""
    args = parse_args()
    aurral = Aurral(*load_config(args.config))
    mode = "full" if args.full else "quick"
    try:
        status_code, body = aurral.call_json("POST", "/api/library/refresh",
                                             {"mode": mode})
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        log(f"cannot start the scan: {error}", error=True)
        return 1
    job_id = body.get("jobId")
    log(f"{mode} scan queued (job {job_id}, HTTP {status_code})")
    if not args.wait:
        return 0

    deadline = time.monotonic() + args.timeout * 60
    status = (body.get("status") or {}).get("status")
    while status not in FINISHED:
        if time.monotonic() > deadline:
            log(f"scan still {status} after {args.timeout:g} min; not "
                f"waiting longer", error=True)
            return 1
        time.sleep(POLL_SECONDS)
        try:
            _, reply = aurral.call("GET", f"/api/library/refresh/{job_id}")
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            log(f"cannot read scan status: {error}", error=True)
            continue
        status = reply.get("status")
        if reply.get("error"):
            log(f"scan error: {reply['error']}", error=True)
    log(f"scan {status}")
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
