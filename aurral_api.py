"""Shared helpers for the aurral tools in this folder.

Used by cancel-stuck-downloads.py and approve-reviews.py: config loading
(aurral-tools.cfg, mode 600), a minimal API client using the X-Api-Key
header, timestamped logging, and the shared status labels.
"""

import argparse
import configparser
import datetime
import json
import stat
import sys
import urllib.request
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent / "aurral-tools.cfg"
TIMEOUT_SECONDS = 60
# Status labels that mean aurral still has download work to do
ACTIVE_LABELS = ("Queued", "Searching", "Downloading")
# --watch stops after this many checks in a row with nothing left to do
IDLE_CHECKS_TO_FINISH = 2


def log(message: str, error: bool = False) -> None:
    """Print a timestamped line (local time, 12-hour clock)."""
    stamp = datetime.datetime.now().strftime("%m/%d/%Y %I:%M:%S %p")
    print(f"{stamp} {message}", file=sys.stderr if error else sys.stdout,
          flush=True)


def positive_float(text: str) -> float:
    """argparse type: a number greater than zero."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}")
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def config_error(path: Path, message: str) -> None:
    """Exit with a usage-error code for a config problem."""
    print(f"{path}: {message}", file=sys.stderr)
    sys.exit(2)


def load_config(path: Path) -> tuple[str, str]:
    """Return (url, api_key); the file must be readable only by its owner."""
    if not path.exists():
        config_error(path, "config file not found; copy "
                           "aurral-tools.cfg.example")
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        config_error(path, f"holds an API key but other users can read it; "
                           f"run: chmod 600 {path}")
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(path, encoding="utf-8")
    except configparser.Error as error:
        config_error(path, f"cannot parse: {error}")
    url = config.get("aurral", "url", fallback="").strip().rstrip("/")
    api_key = config.get("aurral", "api_key", fallback="").strip()
    if not url.startswith(("http://", "https://")):
        config_error(path, "[aurral] url must start with http:// or https://")
    if not api_key:
        config_error(path, "[aurral] api_key is empty; copy it from aurral "
                           "Settings > General > API access")
    return url, api_key


class Aurral:
    """Minimal aurral API client using the X-Api-Key header."""

    def __init__(self, url: str, api_key: str) -> None:
        self.url = url
        self.api_key = api_key

    def call(self, method: str, path: str):
        """Send one request and return (status, decoded JSON).

        POST requests send an empty JSON object as the body.
        """
        return self.call_json(method, path, {} if method == "POST" else None)

    def call_json(self, method: str, path: str, body: dict | None):
        """Send one request with a JSON body; return (status, decoded JSON)."""
        request = urllib.request.Request(
            self.url + path, method=method,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={"X-Api-Key": self.api_key,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(request,
                                    timeout=TIMEOUT_SECONDS) as reply:
            return reply.status, json.load(reply)
