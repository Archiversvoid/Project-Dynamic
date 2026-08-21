# age_gate.py
# -----------
# Detects age-restricted YouTube videos and manages cookie authentication.
#
# How it works:
#   1. Detects age-restriction signals in yt-dlp output
#   2. Checks if session_cookies.txt is present and valid
#   3. If not, returns clear setup instructions for the UI to display
#   4. Once cookies exist, all age-restricted downloads work automatically
#
# Usage:
#   from age_gate import check_and_handle, get_cookie_args, cookie_file_valid

import sys
from datetime import datetime
from pathlib import Path

COOKIE_FILE = Path(__file__).resolve().parent / "session_cookies.txt"

_AGE_SIGNALS = (
    "sign in to confirm your age",
    "this video may be inappropriate",
    "age-restricted",
    "age restricted",
    "confirm your age",
    "inappropriate for some users",
    "you must be logged in",
    "login_required",
)

_COOKIE_EXTENSIONS = {
    "firefox": {
        "name": "cookies.txt",
        "store": "https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/",
    },
    "chrome": {
        "name": "Get cookies.txt LOCALLY",
        "store": "https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc",
    },
    "edge": {
        "name": "Get cookies.txt LOCALLY",
        "store": "https://microsoftedge.microsoft.com/addons/detail/get-cookiestxt-locally/helipeccjnbmbhmenmfgjlknfkjihiaf",
    },
}


def is_age_restricted(stdout: str, stderr: str) -> bool:
    combined = (stdout + stderr).lower()
    return any(sig in combined for sig in _AGE_SIGNALS)


def cookie_file_valid() -> bool:
    if not COOKIE_FILE.exists():
        return False
    content = COOKIE_FILE.read_text(encoding="utf-8", errors="ignore")
    if len(content.strip()) < 100:
        return False
    now = datetime.utcnow().timestamp()
    for line in content.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.strip().split("\t")
        if len(parts) >= 7 and "youtube" in parts[0].lower():
            try:
                expiry = int(parts[4])
                if expiry == 0 or expiry > now:
                    return True
            except (ValueError, IndexError):
                continue
    return False


def cookie_file_age_days() -> int | None:
    if not COOKIE_FILE.exists():
        return None
    mtime = datetime.fromtimestamp(COOKIE_FILE.stat().st_mtime)
    return (datetime.now() - mtime).days


def _detect_available_browser() -> str:
    if sys.platform == "win32":
        checks = [
            ("firefox", Path.home() / "AppData" / "Roaming" / "Mozilla" / "Firefox"),
            ("chrome",  Path.home() / "AppData" / "Local"   / "Google"  / "Chrome"),
            ("edge",    Path.home() / "AppData" / "Local"   / "Microsoft" / "Edge"),
        ]
        for browser, path in checks:
            if path.exists():
                return browser
    return "edge"


def get_setup_instructions(browser: str = "edge") -> dict:
    ext = _COOKIE_EXTENSIONS.get(browser, _COOKIE_EXTENSIONS["edge"])
    cookie_path = str(COOKIE_FILE)
    steps = [
        f"Open {browser.title()} and make sure you're logged into YouTube.",
        f'Install the "{ext["name"]}" extension:\n{ext["store"]}',
        "Go to https://www.youtube.com (the homepage, not a video).",
        'Click the extension icon and choose "Export" or "Download cookies.txt".',
        f"Save the file here:\n{cookie_path}",
        "Come back and try the download again — it will work automatically.",
    ]
    return {
        "title": "One-time setup for age-restricted videos",
        "subtitle": "Only needs to be done once. Cookies stay valid for weeks.",
        "steps": steps,
        "cookie_path": cookie_path,
        "extension_url": ext["store"],
        "browser": browser.title(),
    }


def check_and_handle(stdout: str, stderr: str) -> dict:
    """
    Call when a yt-dlp run fails. Returns:
      {"type": "age_restricted", "has_cookies": bool, "instructions": dict | None, "message": str}
      {"type": "other_error"}
    """
    if not is_age_restricted(stdout, stderr):
        return {"type": "other_error"}

    has_cookies = cookie_file_valid()
    days_old = cookie_file_age_days()

    if has_cookies:
        expired = days_old is not None and days_old > 14
        return {
            "type": "age_restricted",
            "has_cookies": True,
            "cookies_may_be_expired": expired,
            "days_old": days_old,
            "instructions": None,
            "message": (
                f"Cookies may be expired ({days_old}d old). Re-export session_cookies.txt."
                if expired else
                "Age-restricted. Retrying with cookies..."
            ),
        }

    browser = _detect_available_browser()
    return {
        "type": "age_restricted",
        "has_cookies": False,
        "cookies_may_be_expired": False,
        "days_old": None,
        "instructions": get_setup_instructions(browser),
        "message": "This video is age-restricted. One-time setup required.",
    }


def get_cookie_args() -> list[str]:
    """Return --cookies flag if session_cookies.txt exists."""
    if COOKIE_FILE.exists():
        return ["--cookies", str(COOKIE_FILE)]
    return []