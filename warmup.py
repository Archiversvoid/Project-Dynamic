"""
warmup.py - optional background pre-warm for Dynamic (Windows).

WHAT IT DOES
    Reads the files Dynamic needs to start (Qt DLLs, the Qt platform/style/
    image plugins, the app's own files, fonts, icons) once, so Windows keeps
    them in its file cache. When you open the app later, those reads come
    from RAM instead of the disk. Then it EXITS. Nothing stays running, no
    window is ever shown, and the app itself is never started.

WHAT IT DELIBERATELY DOES NOT DO
    It does not keep a hidden copy of the app alive in memory. That would
    launch instantly, but it would cost real RAM all day long, which is the
    opposite of "don't eat resources". The honest trade: this is a cache
    warmer, so the gain is biggest on a hard drive or a freshly booted
    machine and smaller on a fast NVMe SSD.

HOW IT STAYS LIGHT
    * Runs in Windows "background mode" (low CPU, low disk, low memory
      priority) so it yields to anything you are actually doing.
    * Waits before starting (45 s when installed) so it never competes with
      the login rush.
    * Skips itself if battery saver is on, the battery is low and unplugged,
      or the app window is already open.
    * Hard cap on how much it reads (250 MB, or 450 MB with --deep), paced
      with tiny sleeps, so its footprint is bounded.

USAGE
    python warmup.py --install        register to run at Windows login
    python warmup.py --uninstall      remove it again
    python warmup.py --status         show whether it is registered
    python warmup.py                  run once right now (for testing)
    python warmup.py --dry-run        list what it WOULD read, read nothing

    --deep       also warm QtMultimedia (TrimLoad) and yt-dlp
    --app-dir P  folder containing the app, if warmup lives somewhere else
    --delay N    seconds to wait before starting (install uses 45)

    Installing adds one value under HKCU\\...\\Run (no admin needed). You can
    also see/disable it in Task Manager > Startup apps.
"""
import argparse
import sys
import time
from pathlib import Path

RUN_NAME = "DynamicWarmup"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOW_TITLE = "Dynamic"
BOOT_DELAY_S = 45
CHUNK = 1024 * 1024
BYTE_CAP = 250 * 1024 * 1024
DEEP_BYTE_CAP = 450 * 1024 * 1024
IS_WIN = sys.platform == "win32"


# ----------------------------------------------------------------- locating
def _default_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _find_package_dir(name, roots):
    """Locate a package folder WITHOUT importing it (importing PySide6 here
    would defeat the point of a light script)."""
    for r in roots:
        d = Path(r) / name
        if d.is_dir():
            return d
    try:
        import importlib.util
        spec = importlib.util.find_spec(name)
    except Exception:
        spec = None
    if spec and spec.submodule_search_locations:
        locs = list(spec.submodule_search_locations)
        if locs:
            return Path(locs[0])
    return None


def collect_files(app_dir, deep=False):
    """Ordered list of files to warm, most startup-critical first."""
    app_dir = Path(app_dir)
    roots = [app_dir, app_dir / "_internal"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))

    files, seen = [], set()

    def add(p):
        try:
            p = Path(p)
            if p.is_file():
                rp = p.resolve()
                if rp not in seen:
                    seen.add(rp)
                    files.append(rp)
        except OSError:
            pass

    def add_glob(base, patterns):
        if base is None:
            return
        for pat in patterns:
            try:
                for p in sorted(Path(base).glob(pat)):
                    add(p)
            except OSError:
                pass

    # 1. The app itself: scripts (dev) or the exe (compiled), fonts, icons.
    add_glob(app_dir, ["launcher.py", "main.py", "TrimLoad.py", "universal_scraper.py", "Fetcher.py"])
    if not (app_dir / "main.py").exists():
        add_glob(app_dir, ["*.exe"])
    add_glob(app_dir, ["assets/*", "icons/*"])

    # 2. Qt core + the plugins a window needs to appear.
    pyside = _find_package_dir("PySide6", roots)
    add_glob(pyside, [
        "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Svg*.dll",
        "pyside6*.dll", "QtCore*.pyd", "QtGui*.pyd", "QtWidgets*.pyd", "QtSvg*.pyd",
        "plugins/platforms/qwindows*.dll", "plugins/styles/*.dll",
        "plugins/imageformats/*.dll", "plugins/iconengines/*.dll",
    ])
    add_glob(_find_package_dir("shiboken6", roots), ["*.pyd", "*.dll"])

    # 3. Optional: things only needed once you start using the app.
    if deep:
        add_glob(pyside, [
            "Qt6Network.dll", "Qt6Multimedia*.dll", "Qt6MultimediaWidgets*.dll",
            "QtMultimedia*.pyd", "QtNetwork*.pyd",
            "plugins/multimedia/*.dll", "plugins/tls/*.dll",
        ])
        ytd = _find_package_dir("yt_dlp", roots)
        if ytd is not None:
            pycs = sorted(ytd.rglob("*.pyc"))
            for p in (pycs if pycs else sorted(ytd.rglob("*.py"))):
                add(p)

    return files


# ------------------------------------------------------------------ warming
def warm(files, byte_cap):
    """Read each file through once so the OS caches it. Paced and capped."""
    done = count = 0
    for f in files:
        if done >= byte_cap:
            break
        try:
            with open(f, "rb", buffering=0) as fh:
                while True:
                    buf = fh.read(CHUNK)
                    if not buf:
                        break
                    done += len(buf)
                    if done >= byte_cap:
                        break
                    time.sleep(0.002)
            count += 1
        except OSError:
            continue
        time.sleep(0.003)
    return count, done


# --------------------------------------------------------- polite-ness checks
def _enter_background_mode():
    """Lowest CPU, disk and memory priority Windows offers a process."""
    if not IS_WIN:
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetCurrentProcess()
        if not k32.SetPriorityClass(h, 0x00100000):   # PROCESS_MODE_BACKGROUND_BEGIN
            k32.SetPriorityClass(h, 0x00000040)       # IDLE_PRIORITY_CLASS
    except Exception:
        pass


def _should_skip():
    """Return a reason string if we should not run right now, else None."""
    if not IS_WIN:
        return None
    try:
        import ctypes

        class _PowerStatus(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

        st = _PowerStatus()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            if st.SystemStatusFlag == 1:
                return "battery saver is on"
            if st.ACLineStatus == 0 and st.BatteryLifePercent <= 20:
                return "on battery and below 20%"
        if ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE):
            return "the app is already open"
    except Exception:
        pass
    return None


# ------------------------------------------------------------ run-at-login
def _run_command(deep, app_dir_arg):
    if getattr(sys, "frozen", False):
        parts = [f'"{sys.executable}"']
    else:
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")     # no console window
        if pyw.exists():
            exe = pyw
        parts = [f'"{exe}"', f'"{Path(__file__).resolve()}"']
    parts += ["--delay", str(BOOT_DELAY_S)]
    if deep:
        parts.append("--deep")
    if app_dir_arg:
        parts += ["--app-dir", f'"{Path(app_dir_arg).resolve()}"']
    return " ".join(parts)


def install(deep=False, app_dir_arg=None):
    if not IS_WIN:
        print("Run-at-login registration is Windows only.")
        return 1
    import winreg
    cmd = _run_command(deep, app_dir_arg)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, cmd)
    print("Installed. Will run at login (after a %ds delay):\n  %s" % (BOOT_DELAY_S, cmd))
    return 0


def uninstall():
    if not IS_WIN:
        print("Nothing to do (Windows only).")
        return 0
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_NAME)
        print("Removed.")
    except FileNotFoundError:
        print("It was not installed.")
    return 0


def status():
    if not IS_WIN:
        print("Windows only.")
        return 0
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, RUN_NAME)
        print("Installed:\n  %s" % val)
    except FileNotFoundError:
        print("Not installed.")
    return 0


# --------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="Optional boot-time cache warm-up for Dynamic.")
    ap.add_argument("--install", action="store_true", help="register to run at Windows login")
    ap.add_argument("--uninstall", action="store_true", help="remove the login registration")
    ap.add_argument("--status", action="store_true", help="show whether it is registered")
    ap.add_argument("--deep", action="store_true", help="also warm QtMultimedia and yt-dlp")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to wait before starting")
    ap.add_argument("--app-dir", default=None, help="folder containing the app")
    ap.add_argument("--dry-run", action="store_true", help="list files, read nothing")
    args = ap.parse_args(argv)

    if args.install:
        return install(args.deep, args.app_dir)
    if args.uninstall:
        return uninstall()
    if args.status:
        return status()

    app_dir = Path(args.app_dir) if args.app_dir else _default_app_dir()
    files = collect_files(app_dir, deep=args.deep)

    if args.dry_run:
        print("%d files would be read from %s:" % (len(files), app_dir))
        for f in files:
            print("  ", f)
        return 0

    if args.delay > 0:
        time.sleep(args.delay)

    reason = _should_skip()
    if reason:
        print("Skipped: %s." % reason)
        return 0

    _enter_background_mode()
    cap = DEEP_BYTE_CAP if args.deep else BYTE_CAP
    n, total = warm(files, cap)
    print("Warmed %d files (%.1f MB)." % (n, total / (1024 * 1024)))
    return 0


if __name__ == "__main__":
    sys.exit(main())