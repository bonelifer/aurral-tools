#!/usr/bin/env python3
"""Remove MusicBrainz tags from music files.

Removes only MusicBrainz tags and keeps everything else, including
aurral's own AURRAL_IDS tag:
- MP3 (ID3): TXXX frames whose description starts with "MusicBrainz", and
  the UFID frame owned by http://musicbrainz.org (the recording ID)
- M4A/MP4: "----:com.apple.iTunes:MusicBrainz ..." freeform tags
- FLAC/Ogg/Opus (Vorbis comments): keys starting with "musicbrainz_"

Every removed value is written to a JSON restore file before the file is
saved, so the tags can be put back.

Note: aurral matches music by MusicBrainz tags first and by artist and
album names second, and its "Clean up Library" and downloads write the
tags again.

Inputs:  command-line options (run with no arguments for help).
Outputs: a summary on stdout; the restore file (JSON) with every removed
         tag per file.
Exit codes: 0 success, 1 some files could not be changed, 2 usage error.

Uses the mutagen library. If it is missing, the script creates its own
virtual environment in ~/.cache/strip-musicbrainz-tags/venv, installs
mutagen there and re-runs itself.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

VENV_DIR = Path.home() / ".cache" / "strip-musicbrainz-tags" / "venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
EXTENSIONS = {".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".opus"}
MP4_PREFIX = "----:com.apple.iTunes:musicbrainz"
UFID_OWNER = "http://musicbrainz.org"


def ensure_mutagen() -> None:
    """Re-run inside a venv with mutagen if this Python lacks it."""
    try:
        # pylint: disable-next=import-outside-toplevel,unused-import
        import mutagen  # noqa: F401
        return
    except ImportError:
        pass
    if Path(sys.prefix).resolve() == VENV_DIR.resolve():
        sys.exit("mutagen is still missing inside the venv")
    if not VENV_PYTHON.exists():
        print(f"Creating virtual environment in {VENV_DIR} ...",
              file=sys.stderr)
        VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)],
                       check=True)
    subprocess.run([str(VENV_PYTHON), "-m", "pip", "install", "--quiet",
                    "mutagen"], check=True)
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])


def parse_args() -> argparse.Namespace:
    """Parse options; with no arguments, print help and exit."""
    parser = argparse.ArgumentParser(
        description="Remove MusicBrainz tags from music files, saving the "
                    "removed values to a restore file.")
    parser.add_argument(
        "paths", nargs="*", type=Path, metavar="PATH",
        help="files or folders to process (folders are searched "
             "recursively)")
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="report what would be removed without changing any file")
    parser.add_argument(
        "-r", "--restore-file", type=Path, metavar="FILE",
        help="where to save the removed tags (default: "
             "~/.cache/strip-musicbrainz-tags/removed-<date-time>.json)")
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(2)
    args = parser.parse_args()
    if not args.paths:
        parser.error("give at least one file or folder")
    for path in args.paths:
        if not path.exists():
            parser.error(f"not found: {path}")
    return args


def music_files(paths: list[Path]):
    """Yield music files under the given files and folders, sorted."""
    for path in paths:
        if path.is_file():
            if path.suffix.lower() in EXTENSIONS:
                yield path
            continue
        for found in sorted(path.rglob("*")):
            if found.is_file() and found.suffix.lower() in EXTENSIONS:
                yield found


def strip_file(path: Path, dry_run: bool) -> dict:
    """Remove MusicBrainz tags from one file; return {tag: value} removed."""
    # pylint: disable=import-outside-toplevel
    import mutagen
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4

    removed = {}
    audio = mutagen.File(path)
    if audio is None or audio.tags is None:
        return removed
    tags = audio.tags
    if isinstance(tags, ID3):
        for key in list(tags.keys()):
            frame = tags[key]
            if (key.startswith("TXXX:")
                    and frame.desc.lower().startswith("musicbrainz")):
                removed[key] = [str(v) for v in frame.text]
                del tags[key]
            elif key.startswith("UFID:") and frame.owner == UFID_OWNER:
                removed[key] = frame.data.decode("utf-8", "replace")
                del tags[key]
    elif isinstance(audio, MP4):
        for key in list(tags.keys()):
            if key.lower().startswith(MP4_PREFIX):
                removed[key] = [bytes(v).decode("utf-8", "replace")
                                for v in tags[key]]
                del tags[key]
    else:
        for key in list(tags.keys()):
            if key.lower().startswith("musicbrainz_"):
                removed[key] = list(tags[key])
                del tags[key]
    if removed and not dry_run:
        audio.save()
    return removed


def main() -> int:
    """Strip MusicBrainz tags and write the restore file."""
    args = parse_args()
    ensure_mutagen()
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    restore_file = args.restore_file or (
        VENV_DIR.parent / f"removed-{stamp}.json")
    # Pass 1: read only, so the restore file exists before any file changes
    changed, errors, total = {}, [], 0
    for path in music_files(args.paths):
        total += 1
        try:
            removed = strip_file(path, dry_run=True)
        except Exception as error:  # pylint: disable=broad-except
            errors.append(f"{path}: {error}")
            continue
        if removed:
            changed[str(path)] = removed
    tag_count = sum(len(tags) for tags in changed.values())
    if args.dry_run or not changed:
        print(f"{total} music files checked; would remove {tag_count} "
              f"MusicBrainz tags from {len(changed)} files")
    else:
        restore_file.parent.mkdir(parents=True, exist_ok=True)
        restore_file.write_text(json.dumps(changed, indent=1,
                                           ensure_ascii=False),
                                encoding="utf-8")
        print(f"removed values saved to {restore_file}")
        # Pass 2: change the files
        done = 0
        for name in changed:
            try:
                strip_file(Path(name), dry_run=False)
                done += 1
            except Exception as error:  # pylint: disable=broad-except
                errors.append(f"{name}: {error}")
        print(f"{total} music files checked; removed {tag_count} "
              f"MusicBrainz tags from {done} of {len(changed)} files")
    for line in errors:
        print(f"error: {line}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
