"""
launcher.py - light startup stub for Dynamic.

Run (or compile) THIS file instead of main.py. It imports only the bare
minimum Qt modules, puts a splash screen on screen immediately, and only
then pays for the heavy import of main.py (which pulls in the whole UI).
The user sees something within a fraction of a second instead of staring
at nothing while everything loads.

Notes:
  * The splash is a static image on purpose. Importing main.py blocks the
    main thread, so an animated splash would just freeze mid-animation.
    Status text is updated between the loading stages instead.
  * yt-dlp and TrimLoad (QtMultimedia) are NOT loaded here at all - they are
    imported lazily the first time they are actually needed.
  * When compiling (PyInstaller etc.), use this file as the entry script and
    prefer a one-folder build over one-file: a one-file exe re-extracts
    itself to a temp folder on every launch, which costs far more than
    anything this stub can save.
"""
import os
import sys
import time
import traceback

_T0 = time.perf_counter()
PROFILE = "--profile" in sys.argv or os.environ.get("DYNAMIC_PROFILE") == "1"
sys.argv = [a for a in sys.argv if a != "--profile"]
_marks = []


def _mark(label):
    if PROFILE:
        _marks.append((label, time.perf_counter() - _T0))


def _write_profile_report(prof):
    """startup_profile.txt next to this file (works under pythonw too, which
    has no console). Stage times are real; the function table comes from
    cProfile, which slows the profiled code down, so read it for relative
    shares, not absolute seconds."""
    import io
    import pstats
    out = io.StringIO()
    out.write("STARTUP STAGES (seconds since launcher.py began running)\n")
    prev = 0.0
    for label, t in _marks:
        out.write("  %7.3f   (+%6.3f)  %s\n" % (t, t - prev, label))
        prev = t
    if prof is not None:
        out.write("\nWINDOW CONSTRUCTION - top functions by cumulative time (cProfile)\n")
        pstats.Stats(prof, stream=out).sort_stats("cumulative").print_stats(25)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "startup_profile.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out.getvalue())


def _set_app_id():
    # Same AppUserModelID main.py sets, so the taskbar groups the splash and
    # the real window as one app. Harmless no-op off Windows.
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('mycompany.myapp.1.0')
    except Exception:
        pass


def _make_splash(app):
    from PySide6.QtWidgets import QSplashScreen
    from PySide6.QtGui import QPixmap, QPainter, QColor, QFont, QPen
    from PySide6.QtCore import Qt, QRectF

    w, h = 440, 250
    dpr = max(1.0, app.devicePixelRatio())
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor("#121212"))

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    p.setPen(QPen(QColor("#2A2A2A"), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 14, 14)

    title = QFont("Segoe UI", 30)
    title.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6)
    p.setFont(title)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRectF(0, 62, w, 60), Qt.AlignmentFlag.AlignCenter, "DYNAMIC")

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#00BFA5"))
    p.drawRoundedRect(QRectF(w / 2 - 24, 128, 48, 3), 1.5, 1.5)
    p.end()

    splash = QSplashScreen(pm, Qt.WindowType.WindowStaysOnTopHint)
    msg_font = QFont("Segoe UI", 10)
    splash.setFont(msg_font)
    return splash


def _status(splash, app, text):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    splash.showMessage(text, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, QColor("#8A8A8A"))
    app.processEvents()


def main():
    _set_app_id()

    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    _mark("QApplication created (PySide6 imported)")
    splash = _make_splash(app)
    splash.show()
    _status(splash, app, "Loading...")
    _mark("splash on screen")
    prof = None

    try:
        import main as dynamic_main          # the heavy import happens here
        missing = [n for n in ("configure_app", "DynamicPC") if not hasattr(dynamic_main, n)]
        if missing:
            raise RuntimeError(
                "main.py is out of date - it is missing: %s.\n\n"
                "launcher.py only works with the main.py delivered alongside it. "
                "Replace main.py and TrimLoad.py in this folder with the new "
                "versions.\n\nThe main.py that was actually loaded:\n%s"
                % (", ".join(missing), getattr(dynamic_main, "__file__", "unknown")))
        _mark("import main finished")
        _status(splash, app, "Starting...")
        dynamic_main.configure_app(app)
        _mark("configure_app finished")
        if PROFILE:
            import cProfile
            prof = cProfile.Profile()
            prof.enable()
        window = dynamic_main.DynamicPC()
        if prof is not None:
            prof.disable()
        _mark("DynamicPC() constructed")
        window.show()
        _mark("window.show() returned")
        splash.finish(window)
        if PROFILE:
            from PySide6.QtCore import QTimer
            def _first_tick():
                _mark("event loop running (window usable)")
                _write_profile_report(prof)
            QTimer.singleShot(0, _first_tick)
    except Exception:
        splash.close()
        QMessageBox.critical(None, "Dynamic failed to start", traceback.format_exc()[-1500:])
        sys.exit(1)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()