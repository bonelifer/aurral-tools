#!/usr/bin/env python3
"""Get a Deezer ARL by signing in to Deezer in a real browser window.

The script opens Deezer's login page in a visible browser. If the config
file has an email and password, it fills them in and clicks Log in;
otherwise you sign in there yourself. It never solves a CAPTCHA: if
Deezer shows one, sign in by hand or the run times out. Once Deezer sets
the ``arl`` cookie, the script closes the browser and either prints the
ARL to stdout or, with a deemix URL, sends it to deemix's /api/loginArl.
deemix then checks it with Deezer, signs in, and saves it in its config
folder (login.json).

Deezer shows no login form to headless browsers, so a cron job must give
the browser a display, for example with ``xvfb-run -a``.

Inputs:  command-line options (run with no arguments for help) and an
         optional INI config file (see get-deezer-arl.cfg.example).
         Command-line options override the config file.
Outputs: the ARL on stdout (without a deemix URL); status on stderr.
Exit codes: 0 success, 1 timed out, browser closed, deemix login failed,
or another run is in progress, 2 usage or config error.

On first run the script creates its own virtual environment in
~/.cache/get-deezer-arl/venv, installs Playwright into it, and re-runs
itself from there.
"""

import argparse
import configparser
import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "get-deezer-arl"
VENV_DIR = CACHE_DIR / "venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
LOCK_FILE = CACHE_DIR / "lock"
DEFAULT_CONFIG = Path(__file__).resolve().with_suffix(".cfg")
DEFAULT_TIMEOUT_SECONDS = 300
LOGIN_URL = "https://www.deezer.com/login"
POLL_SECONDS = 1.0
DEEMIX_TIMEOUT_SECONDS = 30
# How long to wait for each login-page element before giving up on it
FORM_WAIT_MS = 20000
# Deezer login page elements (data-testid values, checked 10/31/2026)
COOKIE_REFUSE = "[data-testid=gdpr-btn-refuse-all]"
SWITCH_TO_EMAIL = "[data-testid=btn-switch-to-email]"
EMAIL_FIELD = "[data-testid=email-field]"
PASSWORD_FIELD = "[data-testid=password-field]"
LOGIN_BUTTON = "[data-testid=login-button]"
# deemix /api/loginArl status codes
DEEMIX_LOGIN_STATUS = {
    -1: "Deezer is not available from deemix",
    0: "deemix rejected the ARL",
    1: "deemix signed in and saved the ARL",
    2: "deemix was already signed in",
    3: "deemix signed in and saved the ARL",
}


def status(message: str) -> None:
    """Print a status message to stderr, keeping stdout for the ARL."""
    print(message, file=sys.stderr, flush=True)


def create_venv() -> None:
    """Create the virtual environment, using uv if venv has no pip."""
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(VENV_DIR)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        return
    except subprocess.CalledProcessError:
        # Some Python builds ship without ensurepip; fall back to uv.
        shutil.rmtree(VENV_DIR, ignore_errors=True)
    uv = shutil.which("uv")
    if uv is None:
        sys.exit("Could not create a virtual environment: this Python has "
                 "no ensurepip and uv is not installed.")
    subprocess.run([uv, "venv", "--seed", "--python", sys.executable,
                    str(VENV_DIR)], check=True, stdout=subprocess.DEVNULL)


def ensure_venv() -> None:
    """Re-run this script inside its venv, creating it if needed."""
    if Path(sys.prefix).resolve() == VENV_DIR.resolve():
        return
    if not VENV_PYTHON.exists():
        status(f"Creating virtual environment in {VENV_DIR} ...")
        VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
        create_venv()
    check = subprocess.run([str(VENV_PYTHON), "-c", "import playwright"],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    if check.returncode != 0:
        status("Installing Playwright ...")
        subprocess.run([str(VENV_PYTHON), "-m", "pip", "install", "--quiet",
                        "playwright"], check=True)
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])


def parse_args() -> argparse.Namespace:
    """Parse options; with no arguments, print help and exit."""
    parser = argparse.ArgumentParser(
        description="Open Deezer in a browser, sign in (by hand, or with "
                    "the email and password from the config file), and "
                    "print your ARL cookie or send it to deemix.")
    parser.add_argument(
        "-l", "--login", action="store_true",
        help="open the Deezer login page and wait for the sign-in")
    parser.add_argument(
        "-c", "--config", type=Path, default=DEFAULT_CONFIG, metavar="FILE",
        help=f"INI config file with Deezer credentials and settings "
             f"(default: {DEFAULT_CONFIG}; a missing default file is "
             f"ignored)")
    parser.add_argument(
        "-t", "--timeout", type=int, metavar="SECONDS",
        help=f"give up if there is no sign-in after this many seconds "
             f"(overrides [browser] timeout; default: "
             f"{DEFAULT_TIMEOUT_SECONDS})")
    parser.add_argument(
        "-d", "--deemix-url", metavar="URL",
        help="send the ARL to the deemix server at URL (for example "
             "http://localhost:6595) instead of printing it (overrides "
             "[deemix] url)")
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(2)
    args = parser.parse_args()
    if not args.login:
        parser.error("nothing to do; use --login")
    if args.timeout is not None and args.timeout <= 0:
        parser.error("--timeout must be a positive number of seconds")
    return args


def config_error(path: Path, message: str) -> None:
    """Exit with a usage-error code for a problem in the config file."""
    status(f"{path}: {message}")
    sys.exit(2)


def load_config(args: argparse.Namespace) -> dict:
    """Merge the config file into the settings; command-line options win.

    Returns a dict with email, password, deemix_url and timeout. The
    password is read only from a file that no other user can read.
    """
    path = args.config
    parser = configparser.ConfigParser(interpolation=None)
    if path.exists():
        try:
            parser.read(path, encoding="utf-8")
        except configparser.Error as error:
            config_error(path, f"cannot parse: {error}")
    elif path != DEFAULT_CONFIG:
        config_error(path, "config file not found")

    email = parser.get("deezer", "email", fallback="").strip()
    password = parser.get("deezer", "password", fallback="")
    if password and path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        config_error(path, "holds a password but other users can read it; "
                           f"run: chmod 600 {path}")
    if bool(email) != bool(password):
        config_error(path, "[deezer] needs both email and password, or "
                           "neither")

    deemix_url = args.deemix_url or parser.get("deemix", "url",
                                               fallback="").strip()
    if deemix_url and not deemix_url.startswith(("http://", "https://")):
        config_error(path, "deemix URL must start with http:// or https://")

    timeout = args.timeout
    if timeout is None:
        try:
            timeout = parser.getint("browser", "timeout",
                                    fallback=DEFAULT_TIMEOUT_SECONDS)
        except ValueError:
            config_error(path, "[browser] timeout must be a whole number "
                               "of seconds")
        if timeout <= 0:
            config_error(path, "[browser] timeout must be positive")

    return {"email": email, "password": password,
            "deemix_url": deemix_url, "timeout": timeout}


def acquire_lock():
    """Take a non-blocking lock so cron runs never overlap.

    Returns the open lock file (keep it open), or None if another run
    holds the lock.
    """
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    # pylint: disable-next=consider-using-with
    handle = open(LOCK_FILE, "w", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def send_to_deemix(deemix_url: str, arl: str) -> bool:
    """Sign deemix in with the ARL through its web API.

    deemix (in its default single-user mode) saves the ARL to login.json
    when Deezer accepts it. Returns True on success.
    """
    request = urllib.request.Request(
        deemix_url.rstrip("/") + "/api/loginArl",
        data=json.dumps({"arl": arl}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request,
                                    timeout=DEEMIX_TIMEOUT_SECONDS) as reply:
            result = json.load(reply)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        status(f"Could not reach deemix at {deemix_url}: {error}")
        return False
    code = result.get("status") if isinstance(result, dict) else None
    status(DEEMIX_LOGIN_STATUS.get(code, f"Unexpected reply from deemix: "
                                         f"status {code!r}"))
    return code in (1, 2, 3)


def launch_browser(playwright):
    """Launch installed Google Chrome, or Playwright's Chromium if absent."""
    try:
        return playwright.chromium.launch(channel="chrome", headless=False)
    except Exception:  # pylint: disable=broad-except
        status("Google Chrome not found; installing Playwright's Chromium ...")
        subprocess.run([sys.executable, "-m", "playwright", "install",
                        "chromium"], check=True)
        return playwright.chromium.launch(headless=False)


def fill_login_form(page, email: str, password: str) -> bool:
    """Refuse optional cookies, fill in the email login form, click Log in.

    Returns False if the form did not appear, in which case the user can
    still sign in by hand in the open window.
    """
    # pylint: disable=import-outside-toplevel
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    try:
        # The cookie banner and the phone/email switch do not always appear.
        for optional in (COOKIE_REFUSE, SWITCH_TO_EMAIL):
            try:
                page.click(optional, timeout=FORM_WAIT_MS // 4)
            except PlaywrightTimeout:
                pass
        page.fill(EMAIL_FIELD, email, timeout=FORM_WAIT_MS)
        page.fill(PASSWORD_FIELD, password, timeout=FORM_WAIT_MS)
        page.click(LOGIN_BUTTON, timeout=FORM_WAIT_MS)
    except PlaywrightTimeout:
        status("The login form did not appear; sign in by hand in the "
               "browser window.")
        return False
    status("Submitted the login form. If Deezer asks for a CAPTCHA or a "
           "code, finish signing in by hand.")
    return True


def wait_for_arl(context, timeout: int) -> str | None:
    """Poll the browser's cookies until Deezer sets ``arl``.

    Returns the ARL, or None if the timeout passes or the browser closes.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not context.pages:
            status("The browser was closed before the sign-in finished.")
            return None
        for cookie in context.cookies("https://www.deezer.com"):
            if cookie["name"] == "arl" and cookie["value"]:
                return cookie["value"]
        time.sleep(POLL_SECONDS)
    status(f"No sign-in after {timeout} seconds; giving up. If Deezer "
           f"asked for a CAPTCHA, run the script by hand and solve it.")
    return None


def main() -> int:
    """Run the sign-in flow and print or deliver the ARL."""
    args = parse_args()
    settings = load_config(args)
    ensure_venv()
    lock = acquire_lock()
    if lock is None:
        status("Another get-deezer-arl run is in progress; exiting.")
        return 1
    # Imported here because it only exists inside the venv.
    # pylint: disable-next=import-outside-toplevel
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        # A fresh context each run: no cookies are kept after it closes.
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL)
        if settings["email"]:
            fill_login_form(page, settings["email"], settings["password"])
        else:
            status("Sign in to Deezer in the browser window ...")
        arl = wait_for_arl(context, settings["timeout"])
        browser.close()

    if arl is None:
        return 1
    if settings["deemix_url"]:
        status("Signed in. Sending the ARL to deemix ...")
        return 0 if send_to_deemix(settings["deemix_url"], arl) else 1
    status("Signed in. Your ARL (keep it private, like a password):")
    print(arl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
