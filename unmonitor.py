#!/usr/bin/env python3
"""Find aurral library artists by name and stop monitoring them or their
albums.

Searches the library's artists for NAME (case-insensitive; part of the name
is enough) and lists each match with its monitoring state and how many of
its albums are monitored. Then, either:
- in a terminal, asks which artist and what to unmonitor, or
- with --select and --artist and/or --albums, does it without prompts.

Unmonitoring the artist stops aurral from adding new albums for it.
Unmonitoring its albums also unmonitors their tracks, so aurral's weekly
"search again for missing tracks" and upgrades leave them alone. Files and
library entries are kept.

Inputs:  command-line options (run with no arguments for help) and the
         shared aurral-tools.cfg (aurral URL and API key).
Outputs: the list of matches and one line per change on stdout.
Exit codes: 0 success (or nothing to do), 1 aurral error or some changes
failed, 2 usage or config error.
"""

import argparse
import sys
import urllib.error
from pathlib import Path

from aurral_api import DEFAULT_CONFIG, Aurral, load_config, log


def parse_args() -> argparse.Namespace:
    """Parse options; with no arguments, print help and exit."""
    parser = argparse.ArgumentParser(
        description="Search aurral's library artists by name and stop "
                    "monitoring an artist and/or its albums.")
    parser.add_argument("name", nargs="?", metavar="NAME",
                        help="artist name or part of it")
    parser.add_argument("-l", "--list", action="store_true",
                        help="only list the matching artists")
    parser.add_argument("-s", "--select", type=int, metavar="N",
                        help="pick match number N from the list (no prompt)")
    parser.add_argument("-a", "--artist", action="store_true",
                        help="unmonitor the selected artist")
    parser.add_argument("-b", "--albums", action="store_true",
                        help="unmonitor the selected artist's albums")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="show what would change without changing it")
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG,
                        metavar="FILE",
                        help=f"config file with [aurral] url and api_key "
                             f"(default: {DEFAULT_CONFIG})")
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(2)
    args = parser.parse_args()
    if not args.name or not args.name.strip():
        parser.error("give an artist name to search for")
    if args.list and (args.select or args.artist or args.albums):
        parser.error("--list cannot be combined with --select, --artist "
                     "or --albums")
    if args.select is not None and not (args.artist or args.albums):
        parser.error("with --select, choose --artist and/or --albums")
    if (args.artist or args.albums) and args.select is None \
            and not sys.stdin.isatty():
        parser.error("--select is required when not running in a terminal")
    return args


def find_artists(aurral: Aurral, name: str) -> list[dict]:
    """Library artists whose name contains NAME, with their albums."""
    _, artists = aurral.call("GET", "/api/library/artists?limit=10000")
    needle = name.strip().lower()
    matches = [a for a in artists
               if needle in (a.get("artistName") or "").lower()]
    matches.sort(key=lambda a: (a.get("artistName") or "").lower())
    for artist in matches:
        _, albums = aurral.call(
            "GET", f"/api/library/albums?artistId={artist['id']}")
        artist["_albums"] = albums if isinstance(albums, list) else []
    return matches


def describe(number: int, artist: dict) -> str:
    """One list line for an artist."""
    albums = artist["_albums"]
    monitored_albums = sum(1 for a in albums if a.get("monitored"))
    stats = artist.get("statistics") or {}
    state = "MONITORED" if artist.get("monitored") else "not monitored"
    return (f"{number:3d}. {artist.get('artistName')}  [{state}; "
            f"{monitored_albums} of {len(albums)} albums monitored; "
            f"{stats.get('trackCount', 0)} tracks]")


def ask_choice(matches: list[dict]) -> tuple[dict, bool, bool] | None:
    """Prompt for an artist and what to unmonitor; None to cancel."""
    try:
        answer = input("Artist number (Enter to cancel): ").strip()
        if not answer:
            return None
        index = int(answer)
        if not 1 <= index <= len(matches):
            print("No such number.")
            return None
        what = input("Unmonitor [a]rtist, al[b]ums, or [both]? ").strip()
    except (EOFError, KeyboardInterrupt, ValueError):
        print()
        return None
    what = what.lower()
    artist = what in ("a", "artist", "both")
    albums = what in ("b", "albums", "both")
    if not (artist or albums):
        print("Nothing chosen.")
        return None
    return matches[index - 1], artist, albums


def unmonitor_artist(aurral: Aurral, artist: dict, dry_run: bool) -> bool:
    """Stop monitoring one artist. Returns False on failure."""
    label = artist.get("artistName")
    if not artist.get("monitored"):
        log(f"artist already not monitored: {label}")
        return True
    if not artist.get("mbid"):
        log(f"cannot unmonitor {label}: it has no MusicBrainz ID; use "
            f"aurral's artist page", error=True)
        return False
    if dry_run:
        log(f"would unmonitor artist: {label}")
        return True
    try:
        aurral.call_json("PUT", f"/api/library/artists/{artist['mbid']}",
                         {"monitoring": {"monitored": False}})
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        log(f"failed to unmonitor artist {label}: {error}", error=True)
        return False
    log(f"unmonitored artist: {label}")
    return True


def unmonitor_albums(aurral: Aurral, artist: dict, dry_run: bool) -> bool:
    """Stop monitoring every monitored album of one artist."""
    monitored = [a for a in artist["_albums"] if a.get("monitored")]
    if not monitored:
        log(f"no monitored albums for {artist.get('artistName')}")
        return True
    ok = True
    for album in monitored:
        title = album.get("title") or album.get("albumName")
        if dry_run:
            log(f"would unmonitor album: {title}")
            continue
        try:
            aurral.call_json(
                "PUT", f"/api/library/albums/aurral/{album['canonicalId']}",
                {"monitored": False})
            log(f"unmonitored album: {title}")
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            log(f"failed to unmonitor album {title}: {error}", error=True)
            ok = False
    return ok


def main() -> int:
    """Search, list, and unmonitor what was chosen."""
    args = parse_args()
    aurral = Aurral(*load_config(args.config))
    try:
        matches = find_artists(aurral, args.name)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        log(f"cannot read aurral's artists: {error}", error=True)
        return 1
    if not matches:
        print(f"No library artists match {args.name!r}.")
        return 0
    for number, artist in enumerate(matches, start=1):
        print(describe(number, artist))
    if args.list:
        return 0

    if args.select is not None:
        if not 1 <= args.select <= len(matches):
            print(f"--select must be between 1 and {len(matches)}",
                  file=sys.stderr)
            return 2
        chosen, do_artist, do_albums = (matches[args.select - 1],
                                        args.artist, args.albums)
    elif sys.stdin.isatty():
        picked = ask_choice(matches)
        if picked is None:
            print("Nothing changed.")
            return 0
        chosen, do_artist, do_albums = picked
    else:
        return 0

    ok = True
    if do_artist:
        ok = unmonitor_artist(aurral, chosen, args.dry_run) and ok
    if do_albums:
        ok = unmonitor_albums(aurral, chosen, args.dry_run) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
