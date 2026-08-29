# age_gate.py
# -----------
# Detects age-restricted videos and manages cookie auth.
# Dynamic browser detection via Windows registry — no hardcoded Firefox.
# Cookie file lives in Downloads so the path doesn't break when app moves.

import sys
from datetime import datetime
from pathlib import Path

# Cookie file in user's Downloads — easy to find, path-independent
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

# Verified working extension URLs (2026)
# "Get cookies.txt LOCALLY" is the safe open-source version
_BROWSER_EXTENSIONS = {
    "chrome": {
        "name": "Get cookies.txt LOCALLY",
        "store": "https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc",
    },
    "edge": {
        "name": "Get cookies.txt LOCALLY",
        "store": "https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc",
    },
    "firefox": {
        "name": "cookies.txt",
        "store": "https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/",
    },
    "brave": {
        "name": "Get cookies.txt LOCALLY",
        "store": "https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc",
    },
    "opera": {
        "name": "Get cookies.txt LOCALLY",
        "store": "https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc",
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
        if len(parts) >= 7 and "youtube" in parts[0].lower().lstrip("."):
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
    """Detect the user's default browser from Windows registry first,
    then fall back to checking installed browser directories."""
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations"
                r"\UrlAssociations\https\UserChoice"
            )
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")
            winreg.CloseKey(key)
            prog_id = prog_id.lower()
            for name in ("chrome", "edge", "firefox", "brave", "opera"):
                if name in prog_id or (name == "edge" and "msedge" in prog_id):
                    return name
        except Exception:
            pass

        # Registry failed — check which browsers are installed
        for browser in ("chrome", "edge", "firefox", "brave", "opera"):
            if _BROWSER_PATHS[browser].exists():
                return browser

    # Non-Windows or nothing found — default to Chrome
    return "chrome"


def get_cookie_args() -> list:
    if COOKIE_FILE.exists():
        return ["--cookies", str(COOKIE_FILE)]
    return []


def get_setup_instructions(browser: str = None) -> dict:
    if not browser:
        browser = _detect_default_browser()
    ext = _BROWSER_EXTENSIONS.get(browser, _BROWSER_EXTENSIONS["chrome"])
    cookie_path = str(COOKIE_FILE)

    steps = [
        f"Open {browser.title()} and sign into YouTube.",
        f'Install the "{ext["name"]}" extension:\n{ext["store"]}',
        "Go to https://www.youtube.com (homepage, not a video).",
        'Click the extension icon → "Export" or "Download as cookies.txt".',
        f"Save the file with this exact name and location:\n{cookie_path}",
        "Try the download again — it will work automatically from now on.",
    ]

    return {
        "title": "Age-Restricted Video",
        "subtitle": "One-time setup. Cookies stay valid for weeks.",
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
    instructions = get_setup_instructions(browser)
    return {
        "type": "age_restricted",
        "has_cookies": False,
        "cookies_may_be_expired": False,
        "days_old": None,
        "instructions": instructions,
        "message": "This video is age-restricted. One-time setup required.",
    }