# age_gate.py
# -----------
# Detects age-restricted YouTube videos and manages cookie authentication.
# Dynamic browser detection — uses whatever browser the user has installed.

import sys
import winreg
from datetime import datetime
from pathlib import Path

# Cookie file lives in user's Downloads so it's easy to find and
# stays valid after the app moves from development to final location
COOKIE_FILE = Path.home() / "Downloads" / "dynamic_cookies.txt"

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

_BROWSER_EXTENSIONS = {
    "chrome": {
        "name": "Get cookies.txt LOCALLY",
        "store": "https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc",
    },
    "edge": {
        "name": "Get cookies.txt LOCALLY",
        "store": "https://microsoftedge.microsoft.com/addons/detail/get-cookiestxt-locally/helipeccjnbmbhmenmfgjlknfkjihiaf",
    },
    "firefox": {
        "name": "cookies.txt",
        "store": "https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/",
    },
    "brave": {
        "name": "Get cookies.txt LOCALLY",
        "store": "https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc",
    },
    "opera": {
        "name": "Get cookies.txt LOCALLY",
        "store": "https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc",
    },
}

_BROWSER_PATHS = {
    "chrome":   Path.home() / "AppData/Local/Google/Chrome",
    "edge":     Path.home() / "AppData/Local/Microsoft/Edge",
    "firefox":  Path.home() / "AppData/Roaming/Mozilla/Firefox",
    "brave":    Path.home() / "AppData/Local/BraveSoftware/Brave-Browser",
    "opera":    Path.home() / "AppData/Roaming/Opera Software",
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


def cookie_file_age_days():
    if not COOKIE_FILE.exists():
        return None
    return (datetime.now() - datetime.fromtimestamp(COOKIE_FILE.stat().st_mtime)).days


def _detect_default_browser() -> str:
    """Try to detect the user's default browser from Windows registry.
    Falls back to checking installed browser paths."""
    if sys.platform == "win32":
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
            )
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")
            winreg.CloseKey(key)
            prog_id = prog_id.lower()
            if "chrome" in prog_id:
                return "chrome"
            if "edge" in prog_id or "msedge" in prog_id:
                return "edge"
            if "firefox" in prog_id:
                return "firefox"
            if "brave" in prog_id:
                return "brave"
            if "opera" in prog_id:
                return "opera"
        except Exception:
            pass

    # Fallback: check which browsers are installed, prefer Chrome
    for browser in ("chrome", "edge", "firefox", "brave", "opera"):
        if _BROWSER_PATHS[browser].exists():
            return browser

    return "chrome"  # last resort — tell them to install Chrome


def get_cookie_args() -> list:
    if COOKIE_FILE.exists():
        return ["--cookies", str(COOKIE_FILE)]
    return []


def get_setup_instructions(browser: str = None) -> dict:
    if browser is None:
        browser = _detect_default_browser()
    ext = _BROWSER_EXTENSIONS.get(browser, _BROWSER_EXTENSIONS["chrome"])
    cookie_path = str(COOKIE_FILE)

    steps = [
        f"Open {browser.title()} and make sure you're logged into YouTube.",
        f'Install the "{ext["name"]}" extension:\n{ext["store"]}',
        "Go to https://www.youtube.com (the homepage, not a specific video).",
        'Click the extension icon and choose "Export" or "Download cookies.txt".',
        f"Save the file here (exact name matters):\n{cookie_path}",
        "Come back and try the download again — it will work automatically.",
    ]

    return {
        "title": "Age-Restricted Video",
        "subtitle": "One-time setup needed. Cookies stay valid for weeks.",
        "steps": steps,
        "cookie_path": cookie_path,
        "extension_url": ext["store"],
        "browser": browser.title(),
    }


def check_and_handle(stdout: str, stderr: str) -> dict:
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
                f"Cookies may be expired ({days_old}d old). Re-export {COOKIE_FILE.name}."
                if expired else "Age-restricted. Retrying with cookies..."
            ),
        }

    browser = _detect_default_browser()
    return {
        "type": "age_restricted",
        "has_cookies": False,
        "cookies_may_be_expired": False,
        "days_old": None,
        "instructions": get_setup_instructions(browser),
        "message": "This video is age-restricted. One-time setup required.",
    }