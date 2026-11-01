#!/usr/bin/env python3
"""Add ReplayGain tags to music folders with rsgain.

Runs "rsgain easy" (https://github.com/complexlogic/rsgain) on each folder
with settings suited to a library that a player such as MPD reads:
- track and album gain (album gain is worked out per folder, so keep each
  album in its own folder)
- ReplayGain 2.0 loudness (-18 LUFS), rsgain's default
- each file's modified time is kept, so players that sort by date added
  and library scanners that look for changed files are not disturbed
- each MP3 keeps its ID3 version (converting to ID3v2.3 would join
  multiple artists into one "A/B" name)
- files that already have ReplayGain tags are skipped

Only ReplayGain tags are written; audio and other tags are not changed.

Inputs:  command-line options (run with no arguments for help).
Outputs: rsgain's progress and summary on stdout.
Exit codes: 0 success, 1 rsgain reported an error for a folder, 2 usage
error or rsgain not found.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_THREADS = 4


def positive_int(text: str) -> int:
    """argparse type: a whole number of at least 1."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}")
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def parse_args() -> argparse.Namespace:
    """Parse options; with no arguments, print help and exit."""
    parser = argparse.ArgumentParser(
        description="Add ReplayGain track and album tags to music folders "
                    "with rsgain, keeping file times.")
    parser.add_argument(
        "folders", nargs="*", type=Path, metavar="FOLDER",
        help="music folders to tag (searched recursively)")
    parser.add_argument(
        "-m", "--threads", type=positive_int, default=DEFAULT_THREADS,
        metavar="N",
        help=f"files to scan at the same time (default: {DEFAULT_THREADS})")
    parser.add_argument(
        "-t", "--track-only", action="store_true",
        help="write track gain only, no album gain")
    parser.add_argument(
        "-a", "--all-files", action="store_true",
        help="also re-tag files that already have ReplayGain tags")
    parser.add_argument(
        "-T", "--update-times", action="store_true",
        help="let tagging update each file's modified time")
    parser.add_argument(
        "-r", "--rsgain", metavar="PATH",
        help="rsgain program to run (default: rsgain on PATH)")
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(2)
    args = parser.parse_args()
    if not args.folders:
        parser.error("give at least one FOLDER")
    for folder in args.folders:
        if not folder.is_dir():
            parser.error(f"not a folder: {folder}")
    return args


def preset_text(args: argparse.Namespace) -> str:
    """Return an rsgain preset with this run's settings."""
    return "\n".join([
        "[Global]",
        "TagMode=i",
        f"Album={'false' if args.track_only else 'true'}",
        "TargetLoudness=-18",
        "ClipMode=p",
        "MaxPeakLevel=0.0",
        "TruePeak=false",
        "Lowercase=false",
        "ID3v2Version=keep",
        "OpusMode=d",
        f"PreserveMtimes={'false' if args.update_times else 'true'}",
        "",
    ])


def main() -> int:
    """Run rsgain easy on each folder with a temporary preset."""
    args = parse_args()
    rsgain = args.rsgain or shutil.which("rsgain")
    if not rsgain or not Path(rsgain).is_file():
        print("rsgain not found; install it from "
              "https://github.com/complexlogic/rsgain/releases or pass "
              "--rsgain PATH", file=sys.stderr)
        return 2

    status = 0
    with tempfile.TemporaryDirectory(prefix="tag-replaygain-") as tmp:
        preset = Path(tmp) / "preset.ini"
        preset.write_text(preset_text(args), encoding="utf-8")
        for folder in args.folders:
            command = [rsgain, "easy", "-m", str(args.threads),
                       "-p", str(preset)]
            if not args.all_files:
                command.append("-S")
            command.append(str(folder))
            print(f"=== {folder}", flush=True)
            if subprocess.run(command, check=False).returncode != 0:
                print(f"rsgain reported an error for {folder}",
                      file=sys.stderr)
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
