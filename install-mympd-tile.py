#!/usr/bin/env python3
"""Add a myMPD Home tile that opens aurral, using aurral's own icon.

Downloads aurral's app icon (the 512 px PNG aurral serves for its web app,
or a file given with --icon), saves it in myMPD's tile-image folder as
aurral.png, and adds a Home tile that opens aurral in a new browser tab.
Running it again updates the existing aurral tile and its image in place
instead of adding a second tile.

myMPD reads tile images from the pics/thumbs folder inside its working
directory, so --thumbs-dir must be that folder as seen from this machine
(for a Docker myMPD, the host side of its workdir bind mount). Set
MYMPD_SESSION when myMPD is protected by a PIN.

Inputs:  command-line options (run with no arguments for help).
Outputs: aurral.png in --thumbs-dir and one myMPD Home tile; a summary
         line on stdout.
Exit codes: 0 tile added or updated, 1 aurral or myMPD could not be
reached, refused the change, or the icon is not a PNG, 2 usage error.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

TILE_NAME = "aurral"
TILE_COMMAND = "openExternalLink"
IMAGE_NAME = "aurral.png"
# Where aurral serves its 512 px web-app icon, relative to its address
ICON_PATH = "/icons/aurral-icon-512.png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_ICON_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = 10


def rpc(endpoint: str, method: str, params: dict) -> dict:
    """Call one myMPD JSON-RPC method and return its result object."""
    headers = {"Content-Type": "application/json"}
    if session := os.environ.get("MYMPD_SESSION"):
        headers["X-myMPD-Session"] = session
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
    }).encode()
    request = Request(endpoint, body, headers)
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        data = json.load(response)
    if "error" in data or "result" not in data:
        message = (data.get("error") or {}).get("message", "no result")
        raise ValueError(f"myMPD rejected {method}: {message}")
    return data["result"]


def read_icon(icon: str) -> bytes:
    """Return the icon's bytes from a URL or a local file; must be a PNG."""
    if icon.startswith(("http://", "https://")):
        with urlopen(icon, timeout=TIMEOUT_SECONDS) as response:
            data = response.read(MAX_ICON_BYTES + 1)
    else:
        data = Path(icon).read_bytes()
    if len(data) > MAX_ICON_BYTES:
        raise ValueError(f"{icon} is larger than {MAX_ICON_BYTES} bytes")
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError(f"{icon} is not a PNG image")
    return data


def save_image(thumbs_dir: Path, data: bytes) -> Path:
    """Write the icon into myMPD's tile-image folder atomically."""
    target = thumbs_dir / IMAGE_NAME
    handle, temp = tempfile.mkstemp(dir=thumbs_dir, prefix=".aurral-",
                                    suffix=".png")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.chmod(temp, 0o644)
        os.replace(temp, target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return target


def tile_params(page_url: str) -> dict:
    """Build the MYMPD_API_HOME_ICON_SAVE parameters for a new tile."""
    return {
        "replace": False, "oldpos": 0, "name": TILE_NAME,
        "ligature": "", "bgcolor": "#000000", "color": "#ffffff",
        "image": IMAGE_NAME, "cmd": TILE_COMMAND,
        # The second option tells myMPD to open the link in a new tab.
        "options": [page_url, "true"],
    }


def install(endpoint: str, page_url: str) -> bool:
    """Create or update the tile; True when an existing one was replaced."""
    tiles = rpc(endpoint, "MYMPD_API_HOME_ICON_LIST", {}).get("data") or []
    params = tile_params(page_url)
    for position, tile in enumerate(tiles):
        if tile.get("name") == TILE_NAME and tile.get("cmd") == TILE_COMMAND:
            params.update(replace=True, oldpos=position)
            break
    rpc(endpoint, "MYMPD_API_HOME_ICON_SAVE", params)
    return params["replace"]


def valid_http_url(url: str) -> bool:
    """Accept only plain HTTP(S) URLs without credentials or spaces."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (parts.scheme in {"http", "https"} and bool(parts.hostname)
            and parts.username is None and not parts.query
            and not parts.fragment and url.isascii()
            and not any(ord(char) <= 32 for char in url))


def parse_args() -> argparse.Namespace:
    """Parse options; with no arguments, print help and exit."""
    parser = argparse.ArgumentParser(
        description="Add or update a myMPD Home tile that opens aurral, "
                    "with aurral's own icon.")
    parser.add_argument("-m", "--mympd", required=True,
                        help="myMPD base URL, for example "
                             "http://127.0.0.1:8081")
    parser.add_argument("-U", "--url", required=True,
                        help="aurral address the tile opens, as the browser "
                             "that shows myMPD can reach it, for example "
                             "http://127.0.0.1:3002")
    parser.add_argument("-t", "--thumbs-dir", required=True, type=Path,
                        metavar="DIR",
                        help="myMPD's pics/thumbs folder on this machine, "
                             "where tile images live")
    parser.add_argument("-i", "--icon", metavar="URL_OR_FILE",
                        help="PNG to use instead of aurral's own icon "
                             f"(default: the aurral URL + {ICON_PATH})")
    parser.add_argument("-p", "--partition", default="default",
                        help="myMPD partition whose Home screen gets the "
                             "tile (default: default)")
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(2)
    args = parser.parse_args()
    for option, value in (("--mympd", args.mympd), ("--url", args.url)):
        if not valid_http_url(value):
            parser.error(f"{option} must be an HTTP(S) URL without "
                         "credentials or queries")
    if not args.thumbs_dir.is_dir():
        parser.error(f"--thumbs-dir {args.thumbs_dir} is not a folder")
    return args


def main() -> int:
    """Save the icon and install the Home tile."""
    args = parse_args()
    icon = args.icon or args.url.rstrip("/") + ICON_PATH
    endpoint = (f"{args.mympd.rstrip('/')}/api/"
                f"{quote(args.partition, safe='')}")
    try:
        image = save_image(args.thumbs_dir, read_icon(icon))
        replaced = install(endpoint, args.url)
    except (OSError, URLError, ValueError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        return 1
    action = "updated" if replaced else "added"
    print(f"aurral Home tile {action}; it opens {args.url} and shows {image}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
