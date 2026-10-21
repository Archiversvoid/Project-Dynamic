import sys
import ctypes
import os
import json
import threading
import time
import queue
import subprocess
import shutil
import urllib.request
import webbrowser
import hashlib
import math
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QStackedWidget, 
                             QScrollArea, QFrame, QLineEdit, QProgressBar, 
                             QSpacerItem, QSizePolicy, QComboBox, QGraphicsDropShadowEffect,
                             QSystemTrayIcon, QStyle, QGraphicsOpacityEffect, QMenu, QFileDialog,
                             QPlainTextEdit, QProxyStyle, QScrollBar)
from PySide6.QtCore import (Qt, QThread, Signal, QSize, QObject, 
                          QTimer, QUrl, QVariantAnimation, QPropertyAnimation, QEasingCurve,
                          QPoint, QParallelAnimationGroup, QSequentialAnimationGroup, QEvent,
                          QElapsedTimer, QRect, QRectF, QPointF)
from PySide6.QtGui import (QFont, QFontDatabase, QIcon, QPixmap, QImage, QColor, 
                         QPainter, QPainterPath, QCursor, QDesktopServices, QAction,
                         QPen, QBrush, QLinearGradient, QRadialGradient, QFontMetrics, QPalette, QTransform)
import theme
from theme import ss as _ss, qc as _qc

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('mycompany.myapp.1.0')
except Exception:
    pass
yt_dlp = None  # imported lazily - see _get_yt_dlp(). Importing it eagerly at
               # startup costs real, measurable time (it pulls in every
               # extractor module it ships with) for something nothing
               # needs until a download actually starts.

def _get_yt_dlp():
    global yt_dlp
    if yt_dlp is None:
        try:
            import yt_dlp as _yt_dlp
            yt_dlp = _yt_dlp
        except Exception:
            yt_dlp = False
    return yt_dlp or None


repo_root = Path(__file__).resolve().parent
BASE_DIR = Path(__file__).resolve().parent
ICON_PATH = str(BASE_DIR / "icons/icon.ico")
src_dir = repo_root / "src"
cache_dir = repo_root / "cache"
thumb_dir = cache_dir / "thumbnails"
favicon_dir = cache_dir / "favicons"
thumb_dir.mkdir(parents=True, exist_ok=True)
favicon_dir.mkdir(parents=True, exist_ok=True)

if str(repo_root) not in sys.path: sys.path.insert(0, str(repo_root))
if src_dir.exists() and str(src_dir) not in sys.path: sys.path.insert(0, str(src_dir))

TEAL_ACCENT   = "#00BFA5"
BG_DARK       = "#121212"
SIDEBAR_DARK  = "#181818"
CARD_BG       = "#1E1E1E"
CARD_INNER_BG = "#282828"
INPUT_BG      = "#333333"
TEXT_MAIN     = "#FFFFFF"
TEXT_MUTED    = "#8A8A8A"
ERROR_RED     = "#FF5555"
ERROR_BG      = "#2A1515"

_AUDIO_ONLY_DOMAINS = (
    "music.youtube.com", "soundcloud.com", "spotify.com",
    "tidal.com", "deezer.com", "music.apple.com", "apple.com",
    "bandcamp.com", "audiomack.com", "reverbnation.com",
    "mixcloud.com",
)

def _is_audio_only_url(url):
    return any(d in url.lower() for d in _AUDIO_ONLY_DOMAINS)

def _crisp_font(family, size, weight=None):
    """Roboto-Regular.ttf is the only weight actually embedded (see
    _setup_ui's QFontDatabase.addApplicationFont call) - every other
    weight Qt renders for "Roboto" is synthesized from it. On Windows,
    Qt/FreeType's default hinting combined with that synthesis is what
    produces the stray-pixel artifacts above letters like e/m/n/a (this
    never showed up in CustomTkinter, which uses native Windows text
    rendering, not FreeType). Disabling hinting and ClearType-style
    subpixel antialiasing - plain grayscale antialiasing instead - avoids
    the glyph distortion that triggers it."""
    f = QFont(family, size)
    if weight is not None: f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.NoSubpixelAntialias)
    return f


APP_VERSION = "1.0.0"   # shown on Settings > Update. Edit when you cut a release.
FEEDBACK_BUG_URL = ""       # e.g. a GitHub "new issue" link. Empty = shows "not set up yet".
FEEDBACK_FEATURE_URL = ""   # e.g. a GitHub "feature request" link.

COMBO_QSS = """
    QComboBox { background-color: #1A1A1A; color: #FFFFFF; font-size: 14px; border-radius: 6px; padding: 5px 12px; border: 1px solid #2A2A2A; }
    QComboBox::drop-down { border: none; width: 0px; }
    QComboBox::down-arrow { image: none; border: none; }
    QComboBox QAbstractItemView { background-color: #181818; color: #FFFFFF; selection-background-color: #B5B5B5; selection-color: #000000; border: 1px solid #FFFFFF; border-radius: 4px; outline: none; padding: 2px 0px; }
    QComboBox QAbstractItemView::item { min-height: 26px; padding-left: 10px; color: #FFFFFF; }
    QComboBox QAbstractItemView::item:selected { background-color: #B5B5B5; color: #000000; }
"""

_MONO_FAMILY = None
def _mono_family():
    """A fixed-width font family that is actually installed. "monospace" and
    "sans-serif" are CSS generics, not real Windows fonts: when Qt is asked
    for a family that doesn't exist it searches by loading font files to
    resolve the name, which the startup profile measured at ~3.7 s for a
    single QPlainTextEdit. Always ask for a family that exists."""
    global _MONO_FAMILY
    if _MONO_FAMILY is None:
        for fam in ("Consolas", "Cascadia Mono", "Courier New"):
            if QFontDatabase.hasFamily(fam):
                _MONO_FAMILY = fam
                break
        else:
            _MONO_FAMILY = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    return _MONO_FAMILY


def _pkg_version(name):
    try:
        from importlib.metadata import version   # reads metadata only; does not import the package
        return version(name)
    except Exception:
        return None


def _ffmpeg_version_text():
    try:
        exe = None
        for n in ("ffmpeg.exe", "ffmpeg"):
            p = repo_root / n
            if p.exists():
                exe = str(p); break
        exe = exe or shutil.which("ffmpeg")
        if not exe: return "Not found"
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=5, creationflags=flags).stdout
        first = out.splitlines()[0] if out else ""
        parts = first.split()
        return parts[2] if len(parts) >= 3 and parts[1] == "version" else (first or "Unknown")
    except Exception:
        return "Unknown"


class SettingsRow(QFrame):
    """One row of a settings card: title + optional subtitle on the left, and
    on the right either a custom control (combo box, value label) or a
    navigation arrow. Rows with an arrow are clickable."""
    clicked = Signal()

    def __init__(self, title, subtitle="", position="middle", arrow_pixmap=None, control=None, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsRow")
        self._clickable = control is None and arrow_pixmap is not None
        self.setMinimumHeight(78 if subtitle else 68)
        radius = {"top": "border-top-left-radius: 13px; border-top-right-radius: 13px;",
                  "bottom": "border-bottom-left-radius: 13px; border-bottom-right-radius: 13px;",
                  "single": "border-radius: 13px;"}.get(position, "")
        hover = "QFrame#settingsRow:hover { background-color: #242424; }" if self._clickable else ""
        _ss(self, f"QFrame#settingsRow {{ background: transparent; border: none; {radius} }} {hover}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(26, 12, 22, 12)
        lay.setSpacing(16)
        col = QVBoxLayout(); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(4)
        t = QLabel(title)
        _ss(t, f"color: {TEXT_MAIN}; font-size: 19px; background: transparent; border: none;")
        col.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            _ss(s, f"color: {TEXT_MUTED}; font-size: 14px; background: transparent; border: none;")
            col.addWidget(s)
        lay.addLayout(col, 1)
        if control is not None:
            lay.addWidget(control)
        elif arrow_pixmap is not None:
            a = QLabel(); a.setFixedSize(22, 22)
            _ss(a, f"color: {TEXT_MUTED}; font-size: 20px; background: transparent; border: none;")
            if not arrow_pixmap.isNull(): a.setPixmap(arrow_pixmap); a.setProperty("_arrow", True)
            else: a.setText("→")
            lay.addWidget(a)
        if self._clickable:
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def mousePressEvent(self, e):
        if self._clickable and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


def _tint_overlay(alpha_dark, alpha_light):
    """Translucent fill used for hover/chip tints: white-on-dark, black-on-light."""
    return QColor(0, 0, 0, alpha_light) if theme.is_light() else QColor(255, 255, 255, alpha_dark)


def _hover_overlay():
    return _tint_overlay(18, 12)


def _white_sheen(alpha_dark, alpha_light):
    """The moving highlight on skeleton blocks: faint on dark, strong on light."""
    return QColor(255, 255, 255, alpha_light if theme.is_light() else alpha_dark)


class NoFocusRectStyle(QProxyStyle):
    """Suppresses the dotted keyboard-focus rectangle Windows draws on
    buttons/widgets after Alt (or Ctrl, in some apps) is pressed a couple
    times - toggling "keyboard cues" visibility is native Windows
    behavior, not something tied to one widget, so this is handled once
    at the style level rather than per-widget."""
    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_FrameFocusRect:
            return
        super().drawPrimitive(element, option, painter, widget)


class GlobalContextMenuBlocker(QObject):
    """Blocks the native right-click/Menu-key flyout on every QLineEdit
    (Undo/Redo/Cut/Copy/Paste/Select All) and QScrollBar (Scroll Here/
    Top/Bottom/...) in the app, including ones created after this filter
    is installed - an app-wide event filter instead of a per-widget
    setContextMenuPolicy call, so nothing added later needs remembering."""
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ContextMenu and isinstance(obj, (QLineEdit, QScrollBar)):
            return True
        return super().eventFilter(obj, event)


def _is_youtube_url(url):
    return any(d in url.lower() for d in
               ("youtube.com","youtu.be","youtube-nocookie.com","music.youtube.com","m.youtube.com"))

def _site_name(url):
    try:
        host = urlparse(url).netloc.lower()
        if "music.youtube.com" in host: return "YouTube Music"
        if "music.apple.com" in host or "apple.com" in host: return "Apple Music"
        if "spotify.com" in host: return "Spotify"
        if "soundcloud.com" in host: return "SoundCloud"
        if "deezer.com" in host: return "Deezer"
        if "tidal.com" in host: return "Tidal"
        if "bandcamp.com" in host: return "Bandcamp"
        if "audiomack.com" in host: return "Audiomack"
        if "youtu.be" in host or "youtube.com" in host: return "YouTube"
        host = host.replace("www.","").replace("m.","").replace("music.","")
        return host.split(".")[0].title()
    except Exception: return "Web"

def _fetch_favicon_sync(url):
    try:
        parsed = urlparse(url)
        host = parsed.netloc or "unknown"
        filepath = favicon_dir / f"{host}.png"
        if filepath.exists(): return str(filepath)
            
        furl = f"https://icons.duckduckgo.com/ip3/{parsed.netloc}.ico"
        req = urllib.request.Request(furl, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as r: data = r.read()
        with open(filepath, 'wb') as f: f.write(data)
        return str(filepath)
    except Exception:
        return None

def fetch_formats(url):
    try:
        import importlib
        module_name = "Fetcher" if _is_youtube_url(url) else "universal_scraper"
        module = importlib.import_module(module_name)
        for fn in ("fetch_formats","scrape_formats"):
            if hasattr(module, fn): return getattr(module, fn)(url)
        return {"ok": False, "error": f"{module_name} has no fetch function"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def fetcher_download(url, selected_fid=None, out_dir=None, is_audio=False, start_time=None, end_time=None):
    import queue
    import threading
    import os
    
    q = queue.Queue()

    def format_size(b):
        if not b: return "0 MB"
        if b >= 1024 * 1024 * 1024: return f"{b / (1024**3):.2f} GB"
        if b >= 1024 * 1024: return f"{b / (1024**2):.1f} MB"
        return f"{b / 1024:.1f} KB"

    def progress_hook(d):
        if d.get('status') == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            percent = (downloaded / total) if total > 0 else 0.0

            speed_bytes = d.get('speed') or 0
            if speed_bytes > 1024 * 1024:
                speed_str = f"{speed_bytes / (1024*1024):.1f} MB/s"
            elif speed_bytes > 1024:
                speed_str = f"{speed_bytes / 1024:.1f} KB/s"
            else:
                speed_str = f"{speed_bytes:.0f} B/s" if speed_bytes else "0 MB/s"

            eta_sec = d.get('eta')
            if eta_sec is not None:
                m, s = divmod(int(eta_sec), 60)
                h, m = divmod(m, 60)
                eta_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
            else:
                eta_str = "--:--"

            q.put({
                "type": "progress",
                "percent": percent,
                "speed": speed_str,
                "eta": eta_str,
                "downloaded": format_size(downloaded),
                "size": format_size(total)
            })
        elif d.get('status') == 'finished':
            q.put({
                "type": "progress",
                "percent": 1.0,
                "speed": "Processing...",
                "eta": "00:00",
                "downloaded": "",
                "size": ""
            })

    def run_dl():
        try:
            import glob
            ydl_mod = _get_yt_dlp()
            if ydl_mod is None:
                q.put({"type": "error", "message": "yt-dlp library missing"})
                return
            os.makedirs(out_dir, exist_ok=True)
            ydl_opts = {
                'outtmpl': os.path.join(out_dir, '%(title)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
                'progress_hooks': [progress_hook],
                'concurrent_fragment_downloads': 8,
                'merge_output_format': 'mp4',
                'postprocessor_args': {
                    'ffmpeg': ['-avoid_negative_ts', 'make_zero']
                }
            }

            if _is_youtube_url(url):
                ydl_opts['extractor_args'] = {'youtube': ['player_client=tv,web_safari']}
                ydl_opts['http_headers'] = {'Accept-Language': 'en-US,en;q=0.9'}

            final_path_holder = {"path": None}
            def _pp_hook(d):
                if d.get('status') == 'finished':
                    info_d = d.get('info_dict') or {}
                    fp = info_d.get('filepath') or info_d.get('_filename')
                    if fp:
                        final_path_holder['path'] = fp
            ydl_opts['postprocessor_hooks'] = [_pp_hook]

            if is_audio:
                ydl_opts['format'] = 'bestaudio/best'
                ydl_opts['writethumbnail'] = True
                ydl_opts['postprocessors'] = [
                    {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'},
                    {'key': 'FFmpegMetadata'},
                    {'key': 'EmbedThumbnail'},
                ]
            elif selected_fid:
                ydl_opts['format'] = f"{selected_fid}+bestaudio/best"
            else:
                ydl_opts['format'] = 'bestvideo+bestaudio/best'

            if start_time is not None and end_time is not None:
                def range_func(info_dict, ydl):
                    return [{'start_time': start_time, 'end_time': end_time}]
                ydl_opts['download_ranges'] = range_func
                # force_keyframes_at_cuts already gets accurate cuts cheaply:
                # yt-dlp re-encodes only the small slice right at each cut
                # point and stream-copies everything else. Forcing a full
                # '-c:v libx264' re-encode of the whole segment on top of
                # that (as this used to) throws that efficiency away and
                # makes ffmpeg software-encode the entire clip - that was
                # the actual source of the high CPU usage during trimming.
                ydl_opts['force_keyframes_at_cuts'] = True

            with ydl_mod.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info is None:
                    raise Exception("Could not extract video information")
                if info.get('_type') == 'playlist' and info.get('entries'):
                    info = next((e for e in info['entries'] if e), info)

                try:
                    from yt_dlp.utils import sanitize_filename
                except ImportError:
                    def sanitize_filename(s, **kwargs):
                        import re
                        return re.sub(r'(?u)[^-\w.]', '_', s)

                base_title = info.get('title', 'Download')
                current_title = base_title
                sanitized = sanitize_filename(current_title, restricted=False)

                counter = 1
                while glob.glob(os.path.join(glob.escape(out_dir), f"{glob.escape(sanitized)}.*")):
                    current_title = f"{base_title} ({counter})"
                    sanitized = sanitize_filename(current_title, restricted=False)
                    counter += 1

                info['title'] = current_title

                ydl.process_ie_result(info, download=True)

                resolved_path = final_path_holder["path"]
                if not resolved_path:
                    try:
                        candidate = ydl.prepare_filename(info)
                        if candidate and os.path.exists(candidate):
                            resolved_path = candidate
                    except Exception:
                        pass

            q.put({"type": "done", "filepath": resolved_path})
        except Exception as e:
            q.put({"type": "error", "message": str(e)})

    t = threading.Thread(target=run_dl, daemon=True)
    t.start()

    while True:
        try:
            item = q.get(timeout=0.1)
            yield item
            if item.get("type") in ("done", "error"):
                break
        except queue.Empty:
            if not t.is_alive():
                yield {"type": "error", "message": "Download thread terminated unexpectedly"}
                break

def get_rounded_pixmap(pixmap, radius):
    if pixmap.isNull(): return pixmap
    rounded = QPixmap(pixmap.size())
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, pixmap.width(), pixmap.height(), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return rounded

def jiggle_widget(widget):
    if hasattr(widget, "_jiggle_anim") and widget._jiggle_anim.state() == QSequentialAnimationGroup.State.Running: return
    orig_pos = widget.pos()
    group = QSequentialAnimationGroup(widget)
    for offset in [-12, 12, -8, 8, -4, 4, -2, 2, 0]:
        anim = QPropertyAnimation(widget, b"pos", widget)
        anim.setDuration(35)
        anim.setStartValue(widget.pos())
        anim.setEndValue(orig_pos + QPoint(offset, 0))
        group.addAnimation(anim)
    widget._jiggle_anim = group
    group.start()

class ToastWidget(QFrame):
    dismissed = Signal(object)
    def __init__(self, message, parent=None, icon_pixmap=None, rich_text=False):
        super().__init__(parent)
        self.setWindowIcon(QIcon(ICON_PATH))
        self.setFixedSize(290, 48)
        _ss(self, "QFrame { background-color: #242424; border: 1px solid #333333; border-radius: 12px; }")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(10)
        if icon_pixmap is not None and not icon_pixmap.isNull():
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(22, 22)
            icon_lbl.setPixmap(icon_pixmap)
            _ss(icon_lbl, "background: transparent; border: none;")
        else:
            icon_lbl = QLabel("✕")
            _ss(icon_lbl, "color: #EF4444; font-size: 15px; font-weight: bold; border: none; background: transparent;")
        layout.addWidget(icon_lbl)
        msg_lbl = QLabel(message)
        if rich_text:
            msg_lbl.setTextFormat(Qt.TextFormat.RichText)
        _ss(msg_lbl, "color: #FFFFFF; font-size: 13px; font-weight: 500; border: none; background: transparent;")
        layout.addWidget(msg_lbl, 1)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        _ss(close_btn, "QPushButton { color: #888888; font-size: 13px; font-weight: bold; border: none; background: transparent; } QPushButton:hover { color: #FFFFFF; }")
        close_btn.clicked.connect(self.close_toast)
        layout.addWidget(close_btn)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.close_toast)
        self.timer.start(4000)
        self._is_closing = False
    def close_toast(self):
        if self._is_closing: return
        self._is_closing = True
        self.timer.stop()
        self.dismissed.emit(self)

class ToastManager(QObject):
    def __init__(self, parent_window):
        super().__init__(parent_window)
        self.win = parent_window
        self.toasts = []
    def show_toast(self, message, icon_pixmap=None, rich_text=False):
        toast = ToastWidget(message, parent=self.win, icon_pixmap=icon_pixmap, rich_text=rich_text)
        toast.dismissed.connect(self._remove_toast)
        self.toasts.append(toast)
        toast.show()
        toast.raise_()
        self._reposition_toasts(animate_last=True)
    def _reposition_toasts(self, animate_last=False):
        if not self.win: return
        w_width, w_height = self.win.width(), self.win.height()
        for i, toast in enumerate(self.toasts):
            target_x = w_width - 24 - 290
            target_y = w_height - 24 - (i + 1) * 48 - i * 10
            if animate_last and i == len(self.toasts) - 1:
                toast.move(target_x, w_height + 20)
                anim = QPropertyAnimation(toast, b"pos", toast)
                anim.setDuration(250)
                anim.setStartValue(QPoint(target_x, w_height + 20))
                anim.setEndValue(QPoint(target_x, target_y))
                anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                anim.start()
                toast._pos_anim = anim
            else:
                anim = QPropertyAnimation(toast, b"pos", toast)
                anim.setDuration(200)
                anim.setStartValue(toast.pos())
                anim.setEndValue(QPoint(target_x, target_y))
                anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                anim.start()
                toast._pos_anim = anim
    def _remove_toast(self, toast):
        if toast in self.toasts:
            self.toasts.remove(toast)
            anim_group = QParallelAnimationGroup(toast)
            slide_anim = QPropertyAnimation(toast, b"pos", toast)
            slide_anim.setDuration(200)
            slide_anim.setStartValue(toast.pos())
            slide_anim.setEndValue(toast.pos() + QPoint(40, 0))
            slide_anim.setEasingCurve(QEasingCurve.Type.InCubic)
            anim_group.addAnimation(slide_anim)
            def _on_finish():
                toast.deleteLater()
                self._reposition_toasts(animate_last=False)
            anim_group.finished.connect(_on_finish)
            anim_group.start()
            toast._close_anim = anim_group
    def update_positions(self):
        self._reposition_toasts(animate_last=False)

class AnimatedComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup = None
        self._anim_group = None

    def showPopup(self):
        super().showPopup()
        popup = self.view().window()
        if popup and popup is not self._popup:
            self._popup = popup
            popup.installEventFilter(self)
        if popup:
            popup.setWindowOpacity(0.0)
            QTimer.singleShot(0, self._animate_popup)

    def eventFilter(self, obj, event):
        if obj is self._popup and event.type() == QEvent.Type.Show:
            obj.setWindowOpacity(0.0)
        return super().eventFilter(obj, event)

    def _animate_popup(self):
        popup = self._popup
        if not popup:
            return

        if self._anim_group and self._anim_group.state() == QParallelAnimationGroup.State.Running:
            self._anim_group.stop()

        self._anim_group = QParallelAnimationGroup(self)

        fade_anim = QPropertyAnimation(popup, b"windowOpacity")
        fade_anim.setDuration(200)
        fade_anim.setStartValue(0.0)
        fade_anim.setEndValue(1.0)
        fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        slide_anim = QPropertyAnimation(popup, b"pos")
        slide_anim.setDuration(200)

        target_pos = popup.pos()
        global_pos = self.mapToGlobal(QPoint(0, self.height()))

        if target_pos.y() < global_pos.y() - 10:
            slide_anim.setStartValue(target_pos + QPoint(0, 8))
        else:
            slide_anim.setStartValue(target_pos + QPoint(0, -8))

        slide_anim.setEndValue(target_pos)
        slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_group.addAnimation(fade_anim)
        self._anim_group.addAnimation(slide_anim)
        self._anim_group.start()

class ModernScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _ss(self, """
            QScrollArea { background-color: transparent; border: none; }
            QScrollBar:vertical { width: 8px; background: transparent; margin: 0px; }
            QScrollBar::handle:vertical { background-color: #444444; border-radius: 4px; min-height: 40px; }
            QScrollBar::handle:vertical:hover { background-color: #666666; }
            QScrollBar::handle:vertical:pressed { background-color: #00BFA5; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; border: none; background: none; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
        """)
        self._scroll_anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_anim.setDuration(180)
    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta != 0:
            vbar = self.verticalScrollBar()
            current = self._scroll_anim.endValue() if self._scroll_anim.state() == QPropertyAnimation.State.Running else vbar.value()
            step = -int(delta * 1.2)
            target = max(vbar.minimum(), min(vbar.maximum(), current + step))
            self._scroll_anim.stop()
            self._scroll_anim.setStartValue(vbar.value())
            self._scroll_anim.setEndValue(target)
            self._scroll_anim.start()
            event.accept()
        else:
            super().wheelEvent(event)

class SkeletonBox(QFrame):
    def __init__(self, width=None, height=12, radius=6, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        if width is not None:
            self.setFixedWidth(width)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        _ss(self, f"background-color: {CARD_INNER_BG}; border-radius: {radius}px; border: none;")
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(950)
        anim.setStartValue(0.35)
        anim.setKeyValueAt(0.5, 0.9)
        anim.setEndValue(0.35)
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        anim.setLoopCount(-1)
        anim.start()
        self._anim = anim


class ShimmerBox(QFrame):
    """A skeleton placeholder with a soft light band continuously sweeping
    across it - the "flowing" shimmer look, rather than SkeletonBox's flat
    pulse. Every instance shares one QTimer/clock (like the processing
    overlay's racing line), so a whole card full of these costs one timer,
    not one per block - and since they all read the same shared clock and
    map it through their own width, the bands all sweep past together as
    if gliding across one continuous surface."""
    _instances = []
    _timer = None
    _clock = None

    def __init__(self, width=None, height=12, radius=6, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        if width is not None:
            self.setFixedWidth(width)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._radius = radius
        _ss(self, "background: transparent; border: none;")
        ShimmerBox._instances.append(self)
        if ShimmerBox._clock is None:
            ShimmerBox._clock = QElapsedTimer()
            ShimmerBox._clock.start()
        if ShimmerBox._timer is None:
            ShimmerBox._timer = QTimer()
            ShimmerBox._timer.setInterval(33)
            ShimmerBox._timer.timeout.connect(ShimmerBox._tick)
        if not ShimmerBox._timer.isActive():
            ShimmerBox._timer.start()

    @classmethod
    def _tick(cls):
        # Belt-and-suspenders pruning for anything that got destroyed
        # without going through unregister() - normal cleanup is
        # deterministic (see unregister/_clear_home_slot), this is just
        # a safety net so a missed case can't silently accumulate.
        alive = []
        for w in cls._instances:
            try:
                win = w.window()
                if w.isVisible() and not (win is not None and win.isMinimized()):
                    w.update()
                alive.append(w)
            except RuntimeError:
                pass
        cls._instances = alive
        if not cls._instances and cls._timer is not None:
            cls._timer.stop()

    @classmethod
    def unregister(cls, box):
        """Explicit, immediate removal - called the moment a skeleton card
        is discarded, rather than waiting on Qt's deferred deleteLater()
        (which only runs once the event loop is idle) or on isVisible()
        happening to already read False by then. Without this, a
        ShimmerBox whose parent widget was queued for deletion but hadn't
        actually been destroyed yet stayed in _instances - still visible,
        still getting update()+repainted every tick - and every fresh
        link pasted in the same session added more of these on top,
        so the paint cost (and CPU/power draw) only ever grew across a
        session instead of being released when a card was replaced."""
        if box in cls._instances:
            cls._instances.remove(box)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), self._radius, self._radius)
        p.setClipPath(path)
        p.fillRect(self.rect(), _qc(CARD_INNER_BG))
        now = ShimmerBox._clock.elapsed()
        period = 1400.0
        t = (now % period) / period
        w = float(self.width())
        band_w = max(60.0, w * 0.5)
        x = -band_w + (w + band_w) * t
        g = QLinearGradient(x, 0, x + band_w, 0)
        c0 = QColor(255, 255, 255, 0)
        c1 = _white_sheen(26, 150)
        g.setColorAt(0.0, c0); g.setColorAt(0.5, c1); g.setColorAt(1.0, c0)
        p.fillRect(self.rect(), QBrush(g))
        p.end()


class FilterTab(QFrame):
    clicked = Signal(str)
    def __init__(self, name, count, is_active=False, parent=None):
        super().__init__(parent)
        self.name, self._is_active, self._count = name, is_active, count
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedHeight(32)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(16, 0, 16, 0)
        self.layout.setSpacing(8)
        self.text_lbl = QLabel(name)
        self.text_lbl.setFont(_crisp_font("Roboto", 10, QFont.Weight.Medium))
        self.badge_lbl = QLabel(str(count if count < 100 else "99+"))
        self.badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge_lbl.setFixedHeight(20)
        self.badge_lbl.setMinimumWidth(16)
        self.layout.addWidget(self.text_lbl)
        self.layout.addWidget(self.badge_lbl)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(250)
        self._anim.valueChanged.connect(self._on_anim_step)
        self._current_text, self._current_badge_bg, self._current_bg = self._colors(is_active)
        self._update_stylesheet()
    def update_count(self, count):
        if self._count != count:
            self._count = count
            self.badge_lbl.setText(str(count if count < 100 else "99+"))
    def set_active(self, active):
        if self._is_active == active: return
        self._is_active = active
        self._animate_colors(*self._colors(active), 250)
    def _animate_colors(self, t_text, t_badge, t_bg, duration):
        self._anim.stop()
        self._anim.setDuration(duration)
        self._start_text, self._start_badge_bg, self._start_bg = self._current_text, self._current_badge_bg, self._current_bg
        self._target_text, self._target_badge_bg, self._target_bg = t_text, t_badge, t_bg
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
    def _on_anim_step(self, progress):
        self._current_text = self._interpolate_color(self._start_text, self._target_text, progress)
        self._current_badge_bg = self._interpolate_color(self._start_badge_bg, self._target_badge_bg, progress)
        self._current_bg = self._interpolate_color(self._start_bg, self._target_bg, progress)
        self._update_stylesheet()
    def _interpolate_color(self, c1, c2, factor):
        return QColor(int(c1.red() + (c2.red() - c1.red()) * factor), 
                      int(c1.green() + (c2.green() - c1.green()) * factor), 
                      int(c1.blue() + (c2.blue() - c1.blue()) * factor))
    def _colors(self, active, hover=False):
        """(text, badge bg, tab bg) for the current theme. These are already
        themed QColors, which is why _update_stylesheet below applies its
        stylesheets raw instead of through _ss (it would map them twice)."""
        if active:
            return QColor(theme.accent_text()), _qc("#1B3D37", "bg"), _qc("#142C28", "bg")
        if hover:
            return _qc("#FFFFFF", "text"), _qc("#555555", "bg"), _qc("#333333", "bg")
        return _qc("#E0E0E0", "text"), _qc("#3D3D3D", "bg"), _qc("#2A2A2A", "bg")

    def apply_theme(self):
        self._anim.stop()
        self._current_text, self._current_badge_bg, self._current_bg = self._colors(self._is_active)
        self._update_stylesheet()

    def _update_stylesheet(self):
        text, badge_bg, bg = self._current_text.name(), self._current_badge_bg.name(), self._current_bg.name()
        self.setStyleSheet(f"FilterTab {{ background-color: {bg}; border-radius: 16px; border: none; }}")  # theme: raw
        self.text_lbl.setStyleSheet(f"color: {text}; border: none; background: transparent;")  # theme: raw
        self.badge_lbl.setStyleSheet(f'background-color: {badge_bg}; color: {text}; border-radius: 10px; min-width: 14px; font-size: 11px; font-weight: bold; padding: 0px 8px; border: none;')  # theme: raw
    def enterEvent(self, event):
        if not self._is_active: self._animate_colors(*self._colors(False, hover=True), 150)
        super().enterEvent(event)
    def leaveEvent(self, event):
        if not self._is_active: self._animate_colors(*self._colors(False), 150)
        super().leaveEvent(event)
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton: self.clicked.emit(self.name)

class FilterTabBar(QFrame):
    filter_changed = Signal(str)
    def __init__(self, filters, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.tabs = {}
        self.active_tab = filters[0]
        self.indicator = QFrame(self)
        _ss(self.indicator, "background-color: transparent; border: none; border-radius: 16px;")
        self.anim = QPropertyAnimation(self.indicator, b"geometry")
        self.anim.setDuration(350)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 6, 0, 6)
        self.layout.setSpacing(10)
        for f_name in filters:
            tab = FilterTab(f_name, 0, is_active=(f_name == self.active_tab))
            tab.clicked.connect(self._on_tab_clicked)
            self.layout.addWidget(tab)
            self.tabs[f_name] = tab
        self.layout.addStretch()
    def update_counts(self, counts):
        for f_name, tab in self.tabs.items():
            if f_name in counts: tab.update_count(counts[f_name])
        if not self.anim.state() == QPropertyAnimation.State.Running and self.active_tab in self.tabs:
            self.indicator.setGeometry(self.tabs[self.active_tab].geometry())
    def _on_tab_clicked(self, f_name):
        if f_name == self.active_tab: return
        prev_tab = self.active_tab
        self.active_tab = f_name
        self.tabs[prev_tab].set_active(False)
        self.tabs[f_name].set_active(True)
        self.anim.stop()
        self.anim.setStartValue(self.indicator.geometry())
        self.anim.setEndValue(self.tabs[f_name].geometry())
        self.anim.start()
        self.filter_changed.emit(f_name)
    def showEvent(self, event):
        super().showEvent(event)
        if self.active_tab in self.tabs: self.indicator.setGeometry(self.tabs[self.active_tab].geometry())
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.active_tab in self.tabs and self.anim.state() != QPropertyAnimation.State.Running:
            self.indicator.setGeometry(self.tabs[self.active_tab].geometry())

class SidebarNavButton(QFrame):
    clicked = Signal(int)
    def __init__(self, name, icon, idx, is_active=False, parent=None):
        super().__init__(parent)
        self.idx = idx
        self._is_active = is_active
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedHeight(44)
        
        _ss(self, "SidebarNavButton { background: transparent; border: none; }")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 0, 11, 0)
        layout.setSpacing(12)
        
        self.icon_lbl = QLabel()
        self.icon_lbl.setPixmap(icon.pixmap(22, 22))
        self.icon_lbl.setFixedSize(22, 22)
        _ss(self.icon_lbl, "background: transparent; border: none;")
        
        self.text_lbl = QLabel(name)
        self.text_lbl.setFont(_crisp_font("Roboto", 11, QFont.Weight.Medium))
        self.text_lbl.setMinimumWidth(0)
        
        layout.addWidget(self.icon_lbl)
        layout.addWidget(self.text_lbl, 1)

        self._color_anim = QVariantAnimation(self)
        self._color_anim.setDuration(220)
        self._color_anim.valueChanged.connect(self._on_color_step)

        self._current_text = self._text_for(is_active)
        self._current_bg = self._bg_for(is_active)
        self._update_text_color()

        self._ripple_pos = QPoint(0, 0)
        self._ripple_radius = 0.0
        self._ripple_opacity = 0.0
        
        self._ripple_anim = QVariantAnimation(self)
        self._ripple_anim.setDuration(400)
        self._ripple_anim.valueChanged.connect(self._on_ripple_step)
        self._ripple_anim.setEasingCurve(QEasingCurve.Type.OutQuad)

        self._collapsed = False
        self._text_fade_anim = None

        self._text_opacity = QGraphicsOpacityEffect(self.text_lbl)
        self.text_lbl.setGraphicsEffect(self._text_opacity)
        self._text_opacity.setOpacity(1.0)

    def _text_for(self, active):
        """Already-themed QColors (see FilterTab._colors for why that matters)."""
        return QColor(theme.accent_text()) if active else _qc(TEXT_MUTED)

    def _bg_for(self, active):
        return _qc("#142C28", "bg") if active else QColor(0, 0, 0, 0)

    def apply_theme(self):
        self._color_anim.stop()
        self._current_text = self._text_for(self._is_active)
        self._current_bg = self._bg_for(self._is_active)
        self._update_text_color()
        self.update()

    def set_icon(self, icon):
        self.icon_lbl.setPixmap(icon.pixmap(22, 22))

    def _update_text_color(self):
        text = self._current_text.name()
        self.text_lbl.setStyleSheet(f"color: {text}; border: none; background: transparent;")  # theme: raw
        self.update()

    def _on_color_step(self, progress):
        self._current_text = self._lerp(self._start_text, self._target_text, progress)
        self._current_bg = self._lerp(self._start_bg, self._target_bg, progress)
        self._update_text_color()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg_path = QPainterPath()
        bg_path.addRoundedRect(0, 0, self.width(), self.height(), 12, 12)
        painter.fillPath(bg_path, self._current_bg)

        if self._ripple_opacity > 0:
            painter.setClipPath(bg_path)
            ripple_color = _qc(TEAL_ACCENT)
            ripple_color.setAlphaF(self._ripple_opacity)
            painter.setBrush(ripple_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(self._ripple_pos, self._ripple_radius, self._ripple_radius)

    def set_active(self, active, animate=True):
        if self._is_active == active:
            return
        self._is_active = active
        target_text = self._text_for(active)
        target_bg = self._bg_for(active)
        if animate:
            self._animate_to(target_text, target_bg, 220)
        else:
            self._current_text, self._current_bg = target_text, target_bg
            self._update_text_color()

    def _animate_to(self, target_text, target_bg, duration):
        self._color_anim.stop()
        self._color_anim.setDuration(duration)
        self._start_text, self._start_bg = self._current_text, self._current_bg
        self._target_text, self._target_bg = target_text, target_bg
        self._color_anim.setStartValue(0.0)
        self._color_anim.setEndValue(1.0)
        self._color_anim.start()

    def _lerp(self, c1, c2, t):
        return QColor(
            int(c1.red() + (c2.red() - c1.red()) * t),
            int(c1.green() + (c2.green() - c1.green()) * t),
            int(c1.blue() + (c2.blue() - c1.blue()) * t),
            int(c1.alpha() + (c2.alpha() - c1.alpha()) * t),
        )

    def enterEvent(self, event):
        if not self._is_active:
            self._animate_to(self._text_for(False), _hover_overlay(), 150)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._is_active:
            self._animate_to(self._text_for(False), QColor(0, 0, 0, 0), 150)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._ripple_pos = event.position().toPoint()
            self._ripple_radius = 0.0
            self._ripple_opacity = 0.35

            self._ripple_anim.stop()
            self._ripple_anim.setStartValue(0.0)
            self._ripple_anim.setEndValue(1.0)
            self._ripple_anim.start()
            
            self.clicked.emit(self.idx)
        super().mousePressEvent(event)

    def _on_ripple_step(self, progress):
        max_radius = self.width() * 1.5
        self._ripple_radius = max_radius * progress
        self._ripple_opacity = 0.35 * (1.0 - progress)
        self.update()

    def set_collapsed(self, collapsed, animate=True):
        self._collapsed = collapsed
        if collapsed:
            self._text_opacity.setOpacity(0.0)
        else:
            self._text_opacity.setOpacity(1.0)

    def begin_collapse_fade(self):
        self._collapsed = True
        if self._text_fade_anim and self._text_fade_anim.state() == QPropertyAnimation.State.Running:
            self._text_fade_anim.stop()
        
        anim = QPropertyAnimation(self._text_opacity, b"opacity", self)
        anim.setDuration(160)
        anim.setStartValue(self._text_opacity.opacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._text_fade_anim = anim
        anim.start()

    def begin_expand_layout(self):
        self._collapsed = False
        if self._text_fade_anim and self._text_fade_anim.state() == QPropertyAnimation.State.Running:
            self._text_fade_anim.stop()
        
        anim = QPropertyAnimation(self._text_opacity, b"opacity", self)
        anim.setDuration(200)
        anim.setStartValue(self._text_opacity.opacity())
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._text_fade_anim = anim
        anim.start()

class ProcessingOverlay(QWidget):
    """The "processing" screen shown while a pasted link is being parsed.

    Everything (background, headline, step tracker, racing line, cancel
    button) is painted by this one widget instead of being built from a
    pile of child widgets + QGraphicsEffects. That keeps it cheap (no
    offscreen buffers, no per-widget effect compositing) and lets the
    whole thing fade/slide as a single unit.

    The screen mirrors what the worker thread is *actually* doing: the
    app calls set_phase() as each real stage begins. Each stage is held
    on screen for at least MIN_DWELL_MS so quick stages stay readable,
    and the queued stages are shown in order."""

    # key, headline, tracker label, detail line
    STEPS = [
        ("start",   "Starting",             "Start",   "Getting things ready"),
        ("fetch",   "Fetching URL streams", "Streams", "Contacting {host} and reading the available streams"),
        ("process", "Processing",           "Process", "Reading the title, duration and available qualities"),
        ("preview", "Loading preview",      "Preview", "Grabbing the thumbnail and site info"),
        ("done",    "Done",                 "Done",    "All set"),
    ]
    MIN_DWELL_MS = 260      # shortest time a stage stays on screen
    DONE_DWELL_MS = 550     # how long "Done" is shown before the card appears
    FAIL_COLOR_MS = 480     # how long the ambient glow/accent takes to turn red
    LINE_PERIOD_MS = 1500   # one lap of the racing line
    FADE_IN_MS = 240
    FADE_OUT_MS = 280
    LOG_PANEL_H = 134

    cancel_requested = Signal()
    retry_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self._keys = [s[0] for s in self.STEPS]
        self._host = ""
        self._active, self._prev = 0, 0
        self._queue = []
        self._done_at = [None] * len(self.STEPS)
        self._change_at, self._active_since = -10000, 0
        self._failed, self._fail_at = False, 0
        self._error_text = ""
        self._log_open = False
        self._finish_cb, self._finish_armed = None, False
        self._alpha, self._opaque = 0.0, False
        self._ease = QEasingCurve(QEasingCurve.Type.OutCubic)
        self._line_ease = QEasingCurve(QEasingCurve.Type.InOutCubic)

        self._clock = QElapsedTimer(); self._clock.start()
        # 30fps rather than 60fps - this is slow ambient motion (gradient
        # sweeps, a pulsing dot), not fast action; halving the repaint
        # rate is not visually distinguishable here but meaningfully cuts
        # sustained CPU/power draw for however long a fetch takes.
        self._tick = QTimer(self); self._tick.setInterval(33)
        self._tick.timeout.connect(self._on_tick)
        self._fade = QVariantAnimation(self)
        self._fade.valueChanged.connect(self._on_fade_step)
        self._fade.finished.connect(self._on_fade_finished)

        # Smoothly carries the ambient glow/accent color from teal to red
        # on failure, instead of snapping instantly.
        self._fail_color_t = 0.0
        self._fail_color_anim = QVariantAnimation(self)
        self._fail_color_anim.valueChanged.connect(self._on_fail_color_step)

        self._build_buttons()
        self._build_log_panel()

        parent.installEventFilter(self)
        self.hide()

    # ---------- buttons / log panel (real child widgets, not painted) ----------
    def _pill_style(self, filled=False):
        if filled:
            return (f"QPushButton {{ background-color: {TEAL_ACCENT}; border: none; border-radius: 17px; "
                     f"color: #0B0B0B; font-size: 13px; font-weight: bold; }} "
                     f"QPushButton:hover {{ background-color: #1ED6B8; }}")
        return (f"QPushButton {{ background: transparent; border: 1.2px solid #3A3A3A; border-radius: 17px; "
                 f"color: {TEXT_MUTED}; font-size: 13px; }} "
                 f"QPushButton:hover {{ border-color: {TEAL_ACCENT}; color: {TEXT_MAIN}; background-color: rgba(0, 191, 165, 22); }}")

    def _fade_with_overlay(self, widget):
        """Gives a child widget its own opacity effect so it fades in/out
        together with the hand-painted content instead of popping in/out
        at full opacity (child widgets aren't touched by this widget's own
        paintEvent-level p.setOpacity() calls)."""
        eff = QGraphicsOpacityEffect(widget)
        eff.setOpacity(0.0)
        widget.setGraphicsEffect(eff)
        return eff

    def _build_buttons(self):
        self._btn_cancel = QPushButton("Cancel", self)
        self._btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        _ss(self._btn_cancel, self._pill_style(filled=False))
        self._btn_cancel.clicked.connect(self.cancel_requested.emit)
        self._cancel_fx = self._fade_with_overlay(self._btn_cancel)

        self._btn_retry = QPushButton("Try Again", self)
        self._btn_retry.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        _ss(self._btn_retry, self._pill_style(filled=True))
        self._btn_retry.clicked.connect(self.retry_requested.emit)
        self._retry_fx = self._fade_with_overlay(self._btn_retry)

        self._btn_log = QPushButton("Error Log", self)
        self._btn_log.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        _ss(self._btn_log, self._pill_style(filled=False))
        self._btn_log.clicked.connect(self._toggle_log)
        self._log_btn_fx = self._fade_with_overlay(self._btn_log)

        for b in (self._btn_cancel, self._btn_retry, self._btn_log):
            b.setFixedSize(120, 34); b.hide()

    def _build_log_panel(self):
        self._log_panel = QFrame(self)
        _ss(self._log_panel, "QFrame { background-color: #161616; border: 1px solid #2E2E2E; border-radius: 10px; }")
        lay = QVBoxLayout(self._log_panel)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("Error details")
        _ss(title, f"color: {TEXT_MUTED}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        header.addWidget(title)
        header.addStretch()
        self._btn_copy = QPushButton("Copy")
        self._btn_copy.setFixedSize(64, 24)
        self._btn_copy.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._copy_style_normal = (f"QPushButton {{ background: transparent; border: 1px solid #3A3A3A; border-radius: 12px; "
                                    f"color: {TEXT_MUTED}; font-size: 11px; }} "
                                    f"QPushButton:hover {{ border-color: {TEAL_ACCENT}; color: {TEXT_MAIN}; }}")
        self._copy_style_done = (f"QPushButton {{ background: transparent; border: 1px solid {TEAL_ACCENT}; border-radius: 12px; "
                                  f"color: {TEAL_ACCENT}; font-size: 11px; font-weight: bold; }}")
        _ss(self._btn_copy, self._copy_style_normal)
        self._btn_copy.clicked.connect(self._copy_error_text)
        header.addWidget(self._btn_copy)
        lay.addLayout(header)
        self._log_text = QPlainTextEdit()
        self._log_text.setReadOnly(True)
        _ss(self._log_text, f"QPlainTextEdit {{ background: transparent; border: none; color: {ERROR_RED}; "
                                       f"font-family: '{_mono_family()}'; font-size: 11px; }}")
        lay.addWidget(self._log_text, 1)
        self._log_panel.hide()
        self._log_fx = self._fade_with_overlay(self._log_panel)
        self._log_anim = QPropertyAnimation(self._log_panel, b"geometry", self)
        self._log_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._log_anim.setDuration(240)
        self._log_anim.finished.connect(self._on_log_anim_finished)

    def _copy_error_text(self):
        QApplication.clipboard().setText(self._error_text)
        self._btn_copy.setText("Copied")
        _ss(self._btn_copy, self._copy_style_done)
        QTimer.singleShot(1500, self._reset_copy_button)

    def _reset_copy_button(self):
        self._btn_copy.setText("Copy")
        _ss(self._btn_copy, self._copy_style_normal)

    def _toggle_log(self):
        self._log_open = not self._log_open
        self._btn_log.setText("Hide Log" if self._log_open else "Error Log")
        rect = self._log_panel_geometry()
        collapsed = QRect(rect.x(), rect.y(), rect.width(), 0)
        expanded = QRect(rect.x(), rect.y(), rect.width(), self.LOG_PANEL_H)
        self._log_anim.stop()
        if self._log_open:
            self._log_panel.setGeometry(collapsed)
            self._log_panel.show()
            self._log_fx.setOpacity(1.0)
            self._log_anim.setStartValue(collapsed)
            self._log_anim.setEndValue(expanded)
        else:
            self._log_anim.setStartValue(self._log_panel.geometry())
            self._log_anim.setEndValue(collapsed)
        self._log_anim.start()

    def _on_log_anim_finished(self):
        if not self._log_open:
            self._log_panel.hide()

    # ---------- public API ----------
    def begin(self, host):
        n = len(self.STEPS)
        self._host = host or "the link"
        self._active, self._prev = 0, 0
        self._queue = []
        self._done_at = [None] * n
        self._change_at, self._active_since = -10000, 0
        self._failed = False
        self._error_text = ""
        self._log_open = False
        self._log_panel.hide()
        self._btn_log.setText("Error Log")
        self._reset_copy_button()
        self._finish_cb, self._finish_armed = None, False
        self._fail_color_anim.stop(); self._fail_color_t = 0.0
        self._clock.restart()
        self.setGeometry(self.parentWidget().rect())
        self._reposition_buttons()
        self.raise_()
        self.show()
        self._fade_to(1.0, self.FADE_IN_MS)

    def set_phase(self, key):
        if self._failed or key not in self._keys: return
        idx = self._keys.index(key)
        last = self._queue[-1] if self._queue else self._active
        if idx > last: self._queue.append(idx)

    def finish(self, callback):
        """Queue up "Done"; callback fires once it has been shown."""
        last = self._queue[-1] if self._queue else self._active
        for i in range(last + 1, len(self.STEPS)): self._queue.append(i)
        self._finish_cb, self._finish_armed = callback, True
        self._reposition_buttons()

    def fail(self, message):
        now = self._clock.elapsed()
        self._failed, self._fail_at = True, now
        self._prev, self._change_at = self._active, now
        self._queue = []
        self._error_text = message or "Unknown error"
        self._log_text.setPlainText(self._error_text)
        self._log_open = False
        self._log_panel.hide()
        self._btn_log.setText("Error Log")
        self._finish_cb, self._finish_armed = None, False
        self._fail_color_anim.stop()
        self._fail_color_anim.setStartValue(self._fail_color_t)
        self._fail_color_anim.setEndValue(1.0)
        self._fail_color_anim.setDuration(self.FAIL_COLOR_MS)
        self._fail_color_anim.start()
        self._reposition_buttons()

    def dismiss(self):
        self._finish_cb, self._finish_armed = None, False
        if self._log_open:
            self._log_open = False
            self._log_anim.stop()
            self._log_panel.hide()
        self._fade_to(0.0, self.FADE_OUT_MS)

    # ---------- internals ----------
    def _fade_to(self, target, ms):
        self._fade.stop()
        self._fade.setStartValue(self._alpha)
        self._fade.setEndValue(float(target))
        self._fade.setDuration(ms)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._fade.start()

    def _on_fade_step(self, v):
        self._alpha = float(v)
        opaque = self._alpha >= 0.999
        if opaque != self._opaque:
            self._opaque = opaque
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, opaque)
        # Child widgets (real buttons/panel) don't fade on their own just
        # because the parent's paintEvent sets an opacity - drive each
        # one's own opacity effect off the same alpha so everything fades
        # together as one unit.
        self._cancel_fx.setOpacity(self._alpha if self._btn_cancel.isVisible() else 0.0)
        self._retry_fx.setOpacity(self._alpha if self._btn_retry.isVisible() else 0.0)
        self._log_btn_fx.setOpacity(self._alpha if self._btn_log.isVisible() else 0.0)
        self._log_fx.setOpacity(self._alpha if self._log_panel.isVisible() else 0.0)
        self.update()

    def _on_fade_finished(self):
        if self._alpha <= 0.001: self.hide()

    def _on_fail_color_step(self, v):
        self._fail_color_t = float(v)
        self.update()

    def showEvent(self, e):
        self._tick.start()
        self._refresh_button_effects()
        super().showEvent(e)

    def hideEvent(self, e):
        self._tick.stop(); super().hideEvent(e)

    def _refresh_button_effects(self):
        """Re-creates each button's/the log panel's QGraphicsOpacityEffect
        from scratch instead of reusing the existing instance. This
        guards against a real Qt quirk: when this overlay's ancestor
        chain is hidden and shown again (e.g. switching away from the
        Home tab and back while a failure is still on screen), a widget
        under a QGraphicsOpacityEffect can keep showing its last cached
        render - invisible or faded - even though it's fully visible and
        clickable again as far as Qt's hit-testing is concerned. That's
        exactly "buttons gone but still clickable". A brand new effect
        instance has no stale cache to carry over."""
        self._cancel_fx = self._fade_with_overlay(self._btn_cancel)
        self._retry_fx = self._fade_with_overlay(self._btn_retry)
        self._log_btn_fx = self._fade_with_overlay(self._btn_log)
        self._log_fx = self._fade_with_overlay(self._log_panel)
        self._cancel_fx.setOpacity(self._alpha if self._btn_cancel.isVisible() else 0.0)
        self._retry_fx.setOpacity(self._alpha if self._btn_retry.isVisible() else 0.0)
        self._log_btn_fx.setOpacity(self._alpha if self._btn_log.isVisible() else 0.0)
        self._log_fx.setOpacity(self._alpha if self._log_panel.isVisible() else 0.0)

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())
            self._reposition_buttons()
            if self._log_open:
                self._log_anim.stop()
                self._log_panel.setGeometry(self._log_panel_geometry())
        return False

    def _activate(self, idx, now):
        for i in range(self._active, idx):
            if self._done_at[i] is None: self._done_at[i] = now
        self._prev, self._active = self._active, idx
        self._change_at = self._active_since = now
        if idx == len(self.STEPS) - 1: self._done_at[idx] = now
        self._reposition_buttons()

    def _on_tick(self):
        now = self._clock.elapsed()
        if self._queue and not self._failed and now - self._active_since >= self.MIN_DWELL_MS:
            self._activate(self._queue.pop(0), now)
        if self._finish_armed and not self._failed and self._active == len(self.STEPS) - 1 and not self._queue:
            if now - self._active_since >= self.DONE_DWELL_MS:
                cb, self._finish_cb, self._finish_armed = self._finish_cb, None, False
                if cb: QTimer.singleShot(0, cb)
        # Stage progression/dwell timing above always keeps running (so
        # things don't stall while minimized), but repainting a window
        # nobody can see is wasted power for zero benefit - skip it.
        win = self.window()
        if win is not None and win.isMinimized():
            return
        # Full repaint every tick: the ambient glow is large (a ~380px
        # soft radial gradient) and its color is continuously animating
        # between teal and red, so constraining updates to the small
        # content/line rects (as a pure power optimization) left stale,
        # hard-edged glow outside those rects - exactly the "rectangle"
        # artifact. A plain gradient fill is cheap regardless of area, so
        # the earlier partial-update split wasn't saving much that
        # mattered - correctness wins here.
        self.update()

    def _center(self): return QPointF(self.width() / 2, self.height() * 0.46)
    def _content_rect(self):
        c = self._center(); return QRect(int(c.x()) - 390, int(c.y()) - 150, 780, 350)
    def _line_rect(self): return QRect(0, self.height() - 16, self.width(), 16)

    def _button_row_rect(self):
        c = self._center()
        return QRect(int(c.x() - 56), int(c.y() + 132), 112, 34)

    def _log_panel_geometry(self):
        c = self._center()
        w = 460
        return QRect(int(c.x() - w / 2), int(c.y() + 132 + 34 + 14), w, self.LOG_PANEL_H)

    def _reposition_buttons(self):
        cancel_visible = (not self._failed) and (not self._finish_armed) and self._active < len(self.STEPS) - 1
        c = self._center()
        if self._failed:
            self._btn_retry.setGeometry(int(c.x() - 127), int(c.y() + 132), 120, 34)
            self._btn_log.setGeometry(int(c.x() + 7), int(c.y() + 132), 120, 34)
        # Visibility is purely state-driven (not gated on the current
        # alpha) - the fade itself is handled by each button's own
        # opacity effect in _on_fade_step, which reads this visibility to
        # decide whether to fade to 0 or to the current alpha. Gating
        # setVisible on alpha here would freeze a button hidden forever
        # once _reposition_buttons happened to run at alpha==0 (e.g. the
        # very start of begin()), since nothing re-shows it as alpha
        # rises during the fade-in.
        self._btn_cancel.setVisible(cancel_visible)
        self._btn_retry.setVisible(self._failed)
        self._btn_log.setVisible(self._failed)
        if cancel_visible:
            r = self._button_row_rect()
            self._btn_cancel.setGeometry(r)
        self._cancel_fx.setOpacity(self._alpha if self._btn_cancel.isVisible() else 0.0)
        self._retry_fx.setOpacity(self._alpha if self._btn_retry.isVisible() else 0.0)
        self._log_btn_fx.setOpacity(self._alpha if self._btn_log.isVisible() else 0.0)
        if not self._failed:
            self._log_panel.hide()

    # ---------- painting ----------
    def _lerp_color(self, c1, c2, t):
        t = max(0.0, min(1.0, t))
        return QColor(
            int(c1.red() + (c2.red() - c1.red()) * t),
            int(c1.green() + (c2.green() - c1.green()) * t),
            int(c1.blue() + (c2.blue() - c1.blue()) * t),
        )

    def _font(self, px, weight=None, spacing=None):
        f = QFont(self.font()); f.setPixelSize(px)
        if weight is not None: f.setWeight(weight)
        if spacing: f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        return f

    def _draw_phase_text(self, p, idx, failed, opacity, dy, now, cx, cy):
        base = p.opacity()
        p.setOpacity(base * opacity)
        headline = "Couldn't process link" if failed else self.STEPS[idx][1]
        detail = "Check the link and try again" if failed else self.STEPS[idx][3].format(host=self._host)
        hf = self._font(38, QFont.Weight.Light)
        p.setFont(hf); p.setPen(_qc(TEXT_MAIN))
        p.drawText(QRectF(cx - 390, cy - 96 + dy, 780, 56), Qt.AlignmentFlag.AlignCenter, headline)
        if not failed and idx < len(self.STEPS) - 1:
            tw = QFontMetrics(hf).horizontalAdvance(headline)
            n = int(now / 380) % 4
            p.setPen(_qc(TEAL_ACCENT))
            p.drawText(QRectF(cx + tw / 2 + 4, cy - 96 + dy, 60, 56), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "." * n)
        p.setFont(self._font(14)); p.setPen(_qc(ERROR_RED if failed else TEXT_MUTED))
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(cx - 390, cy - 34 + dy, 780, 24), Qt.AlignmentFlag.AlignCenter,
                   fm.elidedText(detail, Qt.TextElideMode.ElideRight, 760))
        p.setOpacity(base)

    def paintEvent(self, event):
        p = QPainter(self)
        a = self._alpha
        if a <= 0.001: return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setOpacity(a)
        now = self._clock.elapsed()
        accent = self._lerp_color(_qc(TEAL_ACCENT), _qc(ERROR_RED), self._fail_color_t)

        # background + soft ambient glow
        p.fillRect(event.rect(), _qc(BG_DARK))
        c = self._center()
        glow = QRadialGradient(QPointF(c.x(), c.y() - 30), 380)
        g0 = QColor(accent); g0.setAlpha(24); g1 = QColor(accent); g1.setAlpha(0)
        glow.setColorAt(0.0, g0); glow.setColorAt(1.0, g1)
        p.fillRect(event.rect(), QBrush(glow))

        # content slides up a few px as the screen fades in
        p.save()
        p.translate(0, (1.0 - self._ease.valueForProgress(a)) * 14)
        cx, cy = c.x(), c.y()

        # host chip
        cf = self._font(12, None, 1.2)
        host = QFontMetrics(cf).elidedText(self._host, Qt.TextElideMode.ElideRight, 320)
        tw = QFontMetrics(cf).horizontalAdvance(host)
        chip = QRectF(cx - (tw + 46) / 2, cy - 138, tw + 46, 28)
        p.setPen(QPen(_qc("#2C2C2C"), 1)); p.setBrush(_tint_overlay(7, 6))
        p.drawRoundedRect(chip, 14, 14)
        pulse = 0.5 + 0.5 * math.sin(now / 260.0)
        dot = QColor(accent); dot.setAlpha(int(110 + 145 * pulse))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(dot)
        p.drawEllipse(QPointF(chip.left() + 16, chip.center().y()), 3, 3)
        p.setFont(cf); p.setPen(_qc(TEXT_MUTED))
        p.drawText(QRectF(chip.left() + 27, chip.top(), tw + 12, chip.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, host)

        # headline + detail (cross-fade / slide between stages)
        t = self._ease.valueForProgress(max(0.0, min(1.0, (now - self._change_at) / 360.0)))
        if t < 1.0:
            self._draw_phase_text(p, self._prev, False, 1.0 - t, -12 * t, now, cx, cy)
            self._draw_phase_text(p, self._active, self._failed, t, 12 * (1.0 - t), now, cx, cy)
        else:
            self._draw_phase_text(p, self._active, self._failed, 1.0, 0, now, cx, cy)

        # step tracker
        n, spacing, r = len(self.STEPS), 96.0, 8.0
        ty = cy + 64
        x0 = cx - spacing * (n - 1) / 2
        for i in range(n - 1):
            xa, xb = x0 + i * spacing + r + 7, x0 + (i + 1) * spacing - r - 7
            p.setPen(QPen(_qc("#2A2A2A"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(xa, ty), QPointF(xb, ty))
            if self._done_at[i] is not None:
                fp = self._ease.valueForProgress(max(0.0, min(1.0, (now - self._done_at[i]) / 320.0)))
                p.setPen(QPen(_qc(TEAL_ACCENT), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(xa, ty), QPointF(xa + (xb - xa) * fp, ty))
        lf = self._font(11, None, 0.6)
        for i in range(n):
            x = x0 + i * spacing
            done = self._done_at[i] is not None
            is_active = (i == self._active) and not done
            if self._failed and i == self._active:
                p.setPen(QPen(_qc(ERROR_RED), 2)); p.setBrush(_qc(ERROR_RED))
                p.drawEllipse(QPointF(x, ty), r, r)
                p.setPen(QPen(_qc("#0B0B0B"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(x - 3, ty - 3), QPointF(x + 3, ty + 3)); p.drawLine(QPointF(x + 3, ty - 3), QPointF(x - 3, ty + 3))
                lab = _qc(ERROR_RED)
            elif done:
                e = self._ease.valueForProgress(max(0.0, min(1.0, (now - self._done_at[i]) / 280.0)))
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(_qc(TEAL_ACCENT))
                p.drawEllipse(QPointF(x, ty), r * (0.55 + 0.45 * e), r * (0.55 + 0.45 * e))
                ck = _qc("#0B0B0B"); ck.setAlphaF(e)
                p.setPen(QPen(ck, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)); p.setBrush(Qt.BrushStyle.NoBrush)
                path = QPainterPath(); path.moveTo(x - 3.6, ty + 0.4); path.lineTo(x - 1.0, ty + 3.0); path.lineTo(x + 3.8, ty - 2.8)
                p.drawPath(path)
                lab = _qc("#9A9A9A")
            elif is_active:
                ph = (now % 1300) / 1300.0
                halo = _qc(TEAL_ACCENT); halo.setAlpha(int(95 * (1 - ph)))
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(halo)
                p.drawEllipse(QPointF(x, ty), r + 2 + 9 * ph, r + 2 + 9 * ph)
                p.setPen(QPen(_qc(TEAL_ACCENT), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(x, ty), r, r)
                br = 3.2 + 0.8 * math.sin(now / 200.0)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(_qc(TEAL_ACCENT))
                p.drawEllipse(QPointF(x, ty), br, br)
                lab = _qc(TEXT_MAIN)
            else:
                p.setPen(QPen(_qc("#3A3A3A"), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(x, ty), r - 1, r - 1)
                lab = _qc("#555555")
            p.setFont(lf); p.setPen(lab)
            p.drawText(QRectF(x - 50, ty + r + 10, 100, 16), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.STEPS[i][2])

        # Cancel / Try Again / Error Log are real child widgets now (see
        # _build_buttons) - they paint themselves on top of this, and
        # _reposition_buttons keeps their geometry/visibility in sync.
        p.restore()

        # racing line along the bottom edge (VS Code style): thin, and the
        # lit segment grows out of a point, then the trailing edge speeds
        # up to close the gap on itself right as it reaches the far edge,
        # collapsing back to a point before instantly looping - rather
        # than a fixed-width comet that just slides back and forth.
        line_h = 2.0
        w, ly = float(self.width()), float(self.height() - line_h)
        track = QColor(accent); track.setAlpha(22)
        p.fillRect(QRectF(0, ly, w, line_h), track)
        done_prog = 0.0
        if self._active == len(self.STEPS) - 1 and not self._failed:
            done_prog = self._ease.valueForProgress(max(0.0, min(1.0, (now - self._active_since) / 320.0)))
        t = (now % self.LINE_PERIOD_MS) / float(self.LINE_PERIOD_MS)
        lead = w * t
        gap = (w * 0.32) * math.sin(math.pi * t)
        trail = max(0.0, lead - gap)
        seg_w = max(1.5, lead - trail)
        p.setOpacity(a * (1.0 - done_prog))
        body = QColor(accent); body.setAlpha(150)
        p.fillRect(QRectF(trail, ly, seg_w, line_h), body)
        head_w = min(seg_w, 18.0)
        p.fillRect(QRectF(lead - head_w, ly, head_w, line_h), QColor(accent).lighter(160))
        if done_prog > 0:
            p.setOpacity(a * done_prog)
            p.fillRect(QRectF(0, ly, w * done_prog, line_h), accent)
        p.end()

class AppSignals(QObject):
    update_progress = Signal(object)
    refresh_card = Signal(object)
    home_result = Signal(dict, str, object, object, object, object)
    home_error = Signal(str)
    home_phase = Signal(int, str)
    ffmpeg_version = Signal(str)

class DownloadItem:
    _counter = int(time.time())
    def __init__(self, url, title, channel, thumb_path, out_dir, is_audio, selected_fid, duration_str, site_name, thumb_url=None, fav_path=None, start_time=None, end_time=None):
        DownloadItem._counter += 1
        self.id = DownloadItem._counter
        self.url, self.title, self.channel = url, title, channel
        self.thumb_url, self.thumb_path, self.fav_path = thumb_url, thumb_path, fav_path
        self.out_dir, self.is_audio, self.selected_fid = out_dir, is_audio, selected_fid
        self.duration_str, self.site_name = duration_str, site_name
        self.start_time, self.end_time = start_time, end_time
        self.status, self.percent, self.speed, self.eta, self.downloaded, self.total_size, self.error_msg = "active", 0.0, "", "", "", "", ""
        self.final_path = None
        self.timestamp = datetime.now().strftime("%H:%M")
        self._thread, self._cancelled, self._trash_ready, self._notified = None, False, False, False

class DynamicPC(QMainWindow):
    DownloadItem = DownloadItem

    def __init__(self):
        super().__init__()
        self.settings = self._load_settings()
        theme.set_mode(theme.resolve(self.settings.get("theme", "Default")))
        self._apply_palette()
        self.setWindowTitle("Dynamic")
        self.resize(1100, 720)
        self.setWindowIcon(QIcon("icons/icon.ico"))
        self.setMinimumSize(1020, 640)
        _ss(self, f"QMainWindow {{ background-color: {BG_DARK}; }}")
        self.toast_mgr = ToastManager(self)
        self.signals = AppSignals()
        self.signals.update_progress.connect(self._update_card_progress)
        self.signals.refresh_card.connect(self._refresh_single_card)
        self.signals.home_result.connect(self._on_home_result)
        self.signals.home_error.connect(self._on_home_error)
        self.signals.home_phase.connect(self._on_home_phase)
        self.signals.ffmpeg_version.connect(self._on_ffmpeg_version)
        font_path = str(repo_root / "assets" / "Roboto-Regular.ttf")
        if os.path.exists(font_path): QFontDatabase.addApplicationFont(font_path)
        app_font = _crisp_font("Roboto", 10)
        app_font.setStyleHint(QFont.StyleHint.SansSerif)
        QApplication.setFont(app_font)
        self._dl_items, self._dl_cards, self._dl_tab_filter, self._last_url = [], {}, "All", ""
        self._dl_list_dirty, self._dl_first_build_done = True, False
        # settings are loaded at the top of __init__ (theme must be known before any widget is built)
        self._load_icons()
        self._setup_tray_icon()
        self._load_downloads()
        self._setup_ui()
        self._show_tab(0)

    def _load_settings(self):
        default_settings = {
            "download_dir": str(repo_root / "downloads"),
            "sidebar_collapsed": False,  # Added default state
            "theme": "Default",
        }
        settings_path = repo_root / "settings.json"
        if settings_path.exists():
            try:
                with open(settings_path, "r") as f:
                    loaded = json.load(f)
                    default_settings.update(loaded)
            except Exception:
                pass
        return default_settings


    def _save_settings(self):
        try:
            with open(repo_root / "settings.json", "w") as f:
                json.dump(self.settings, f, indent=4)
        except Exception: pass

    def _restore_and_show_tab(self, idx):
        self.showNormal()
        self.activateWindow()
        self.raise_()
        self._show_tab(idx)

    def _setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        app_icon = QIcon("icons/icon.ico")
        self.tray_icon = QSystemTrayIcon(app_icon, self)
        if not app_icon.isNull(): self.tray_icon.setIcon(app_icon)
        else: self.tray_icon.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        
        self.tray_menu = QMenu(self)

        action_dynamic = QAction("Dynamic", self)
        action_dynamic.triggered.connect(lambda: self._restore_and_show_tab(0))
        self.tray_menu.addAction(action_dynamic)
        
        action_trimload = QAction("TrimLoad", self)
        action_trimload.triggered.connect(lambda: self._restore_and_show_tab(1))
        self.tray_menu.addAction(action_trimload)
        
        action_downloads = QAction("Downloads", self)
        action_downloads.triggered.connect(lambda: self._restore_and_show_tab(2))
        self.tray_menu.addAction(action_downloads)
        
        self.tray_menu.addSeparator()
        
        action_quit = QAction("Quit", self)
        action_quit.triggered.connect(QApplication.instance().quit)
        self.tray_menu.addAction(action_quit)
        
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.show()
        self.tray_icon.messageClicked.connect(self._on_tray_message_clicked)

    def _on_tray_message_clicked(self):
        self._restore_and_show_tab(2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "toast_mgr"): self.toast_mgr.update_positions()

    def _open_qurl(self, qurl): QDesktopServices.openUrl(qurl)

    def _reveal_in_explorer(self, item):
        path = getattr(item, "final_path", None)
        if path and os.path.isfile(path):
            self._reveal_file(path)
            return
        if os.path.isdir(item.out_dir):
            self._open_qurl(QUrl.fromLocalFile(item.out_dir))
        else:
            self.toast_mgr.show_toast("That folder no longer exists")

    def _reveal_file(self, path):
        path = os.path.normpath(path)
        try:
            if sys.platform == "win32":
                subprocess.run(["explorer", "/select,", path])
            elif sys.platform == "darwin":
                subprocess.run(["open", "-R", path])
            else:
                folder = os.path.dirname(path)
                for fm, args in (
                    ("nautilus", ["--select", path]),
                    ("dolphin", ["--select", path]),
                    ("nemo", [path]),
                    ("pcmanfm", [folder]),
                    ("thunar", [folder]),
                ):
                    if shutil.which(fm):
                        subprocess.Popen([fm] + args)
                        return
                self._open_qurl(QUrl.fromLocalFile(folder))
        except Exception:
            folder = os.path.dirname(path)
            if os.path.isdir(folder):
                self._open_qurl(QUrl.fromLocalFile(folder))

    def _load_icons(self):
        self._icons = {}
        for name in ["home", "scissors", "downloads", "settings", "pause", "play", "trash", "trash red", "retry", "check", "collapse", "expand", "arrow-right"]:
            self._icons[name] = theme.icon(name, repo_root / "icons", cache_dir)

    def _load_downloads(self):
        file_path = repo_root / "downloads.json"
        if file_path.exists():
            try:
                with open(file_path, "r") as f:
                    for d in json.load(f):
                        fav_p = d.get("fav_path")
                        if not fav_p or not os.path.exists(fav_p):
                            parsed = urlparse(d["url"])
                            possible_path = favicon_dir / f"{parsed.netloc or 'unknown'}.png"
                            fav_p = str(possible_path) if possible_path.exists() else None
                        it = DownloadItem(
                            url=d["url"], title=d["title"], channel=d["channel"], thumb_path=d.get("thumb_path"), 
                            out_dir=d["out_dir"], is_audio=d["is_audio"], selected_fid=d["selected_fid"], 
                            duration_str=d["duration_str"], site_name=_site_name(d["url"]), 
                            thumb_url=d.get("thumb_url"), fav_path=fav_p, 
                            start_time=d.get("start_time"), end_time=d.get("end_time")
                        )
                        it.status, it.percent, it.total_size, it.downloaded = d["status"], d.get("percent", 0.0), d.get("total_size", ""), d.get("downloaded", "")
                        it.error_msg = d.get("error_msg", "")
                        it.final_path = d.get("final_path")
                        if it.status == "done": it._notified = True
                        self._dl_items.append(it)
            except Exception: pass

    def closeEvent(self, event):
        # Save current sidebar state to settings dictionary
        self.settings["sidebar_collapsed"] = self._sidebar_collapsed
        self._save_settings()

        data = []
        for item in self._dl_items:
            # ... (keep your existing download serialization code here)
            data.append({
                "url": item.url, "title": item.title, "channel": item.channel, "out_dir": item.out_dir, "is_audio": item.is_audio,
                "selected_fid": item.selected_fid, "duration_str": item.duration_str, "status": "paused" if item.status == "active" else item.status,
                "percent": item.percent, "total_size": item.total_size, "downloaded": item.downloaded, "thumb_path": item.thumb_path,
                "thumb_url": item.thumb_url, "fav_path": getattr(item, 'fav_path', None), "start_time": item.start_time, "end_time": item.end_time,
                "error_msg": item.error_msg,
                "final_path": item.final_path
            })
        try:
            with open(repo_root / "downloads.json", "w") as f: json.dump(data, f)
        except Exception:
            pass
        event.accept()

    def _setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.sidebar = QFrame()
        self._sidebar_expanded_w = 210
        self._sidebar_collapsed_w = 68

        # Load the saved state from settings
        self._sidebar_collapsed = self.settings.get("sidebar_collapsed", False)

        # Set initial layout widths matching the saved state
        initial_width = self._sidebar_collapsed_w if self._sidebar_collapsed else self._sidebar_expanded_w
        self.sidebar.setFixedWidth(initial_width)
        _ss(self.sidebar, f"QFrame {{ background-color: {SIDEBAR_DARK}; border: none; }}")

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(12, 28, 12, 20)
        sidebar_layout.setSpacing(12)
        self._sidebar_layout = sidebar_layout

        self.menu_btn = QPushButton()
        # Set icon matching the saved state
        initial_icon = "expand" if self._sidebar_collapsed else "collapse"
        self.menu_btn.setIcon(self._icons.get(initial_icon, QIcon()))
        self.menu_btn.setIconSize(QSize(22, 22))
        self.menu_btn.setFixedSize(44, 32)
        self.menu_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        _ss(self.menu_btn, "QPushButton { background: transparent; border: none; border-radius: 8px; } QPushButton:hover { background-color: #222222; }")
        self.menu_btn.clicked.connect(self._toggle_sidebar)
        sidebar_layout.addWidget(self.menu_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        sidebar_layout.addSpacing(20)

        self._sidebar_anim = QVariantAnimation(self)
        self._sidebar_anim.setDuration(220)
        self._sidebar_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._sidebar_anim.valueChanged.connect(lambda v: self.sidebar.setFixedWidth(int(v)))

        self.nav_btns = []
        nav_items = [("Home", "home", 0), ("TrimLoad", "scissors", 1), ("Downloads", "downloads", 2), ("Settings", "settings", 3)]
        for name, icon_name, idx in nav_items:
            btn = SidebarNavButton(name, self._icons.get(icon_name, QIcon()), idx, is_active=(idx == 0))
            btn._icon_name = icon_name
            
            # Apply initial collapse status to each button without animation on launch
            btn.set_collapsed(self._sidebar_collapsed, animate=False)
            
            btn.clicked.connect(self._show_tab)
            sidebar_layout.addWidget(btn)
            self.nav_btns.append(btn)
            
        sidebar_layout.addStretch()
        main_layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        _ss(self.stack, f"background-color: {BG_DARK};")
        
        self.tab_home = QWidget()
        self._build_home_tab()
        self.stack.addWidget(self.tab_home)

        self.tab_trim = QWidget()
        self.trim_layout = QVBoxLayout(self.tab_trim)
        self.trim_layout.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(self.tab_trim)
        self._trim_loaded = False 
        
        self.tab_dl = QWidget()
        self._build_downloads_tab()
        self.stack.addWidget(self.tab_dl)

        self.tab_settings = QWidget()
        self._settings_built = False   # built on first visit (see _show_tab) so it costs nothing at startup
        self.stack.addWidget(self.tab_settings)

        main_layout.addWidget(self.stack)

    # ------------------------------------------------------------------
    # Settings tab. Built on first visit (see _show_tab).
    # Pages in self.settings_stack: 0 list | 1 downloads | 2 update |
    # 3 feedback | 4 about | 5 text viewer (terms / change log)
    # ------------------------------------------------------------------
    def _build_settings_tab(self):
        self._settings_built = True
        root = QVBoxLayout(self.tab_settings)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.settings_stack = QStackedWidget()
        root.addWidget(self.settings_stack)

        # decoded once and shared by every arrow in the tab
        self._settings_arrow = self._icons.get("arrow-right", QIcon()).pixmap(20, 20)
        for page in (self._build_settings_main_page(), self._build_settings_downloads_page(),
                     self._build_settings_update_page(), self._build_settings_feedback_page(),
                     self._build_settings_about_page(), self._build_settings_text_page()):
            self.settings_stack.addWidget(page)

    def _open_settings_page(self, idx):
        if idx == 2: self._refresh_update_info()
        self.settings_stack.setCurrentIndex(idx)

    def _settings_card(self):
        card = QFrame(); card.setObjectName("settingsCard")
        _ss(card, f"QFrame#settingsCard {{ background-color: {CARD_BG}; border: 1px solid #2A2A2A; border-radius: 14px; }}")
        lay = QVBoxLayout(card); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        return card, lay

    def _settings_divider(self):
        d = QFrame(); d.setFixedHeight(1)
        _ss(d, "background-color: #2A2A2A; border: none;")
        return d

    def _settings_fill_card(self, lay, rows):
        for n, row in enumerate(rows):
            lay.addWidget(row)
            if n < len(rows) - 1: lay.addWidget(self._settings_divider())

    def _settings_button(self, text, primary=False):
        b = QPushButton(text); b.setFixedHeight(40); b.setMinimumWidth(92)
        b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        if primary:
            _ss(b, f"QPushButton {{ background-color: {TEAL_ACCENT}; color: #0B0B0B; border: none; border-radius: 20px; padding: 0 20px; font-size: 14px; font-weight: bold; }} QPushButton:hover {{ background-color: #1ED6B8; }}")
        else:
            _ss(b, f"QPushButton {{ background: transparent; color: {TEXT_MUTED}; border: 1.2px solid #3A3A3A; border-radius: 20px; padding: 0 20px; font-size: 14px; }} QPushButton:hover {{ border-color: {TEAL_ACCENT}; color: {TEXT_MAIN}; }}")
        return b

    def _settings_subpage(self, title, body, expand=False):
        """Standard sub-page: back arrow + title, then the body."""
        page = QWidget()
        lay = QVBoxLayout(page); lay.setContentsMargins(32, 28, 32, 16); lay.setSpacing(20)
        head = QHBoxLayout(); head.setSpacing(12)
        back = QPushButton(); back.setFixedSize(40, 40)
        back.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        _ss(back, f"QPushButton {{ background: transparent; border: none; border-radius: 10px; color: {TEXT_MUTED}; font-size: 22px; }} QPushButton:hover {{ background-color: #222222; }}")
        if not self._settings_arrow.isNull():
            back.setIcon(QIcon(self._settings_arrow.transformed(QTransform().scale(-1, 1))))
            back.setIconSize(QSize(20, 20)); back.setProperty("_arrow_back", True)
        else:
            back.setText("←")
        back.clicked.connect(lambda: self.settings_stack.setCurrentIndex(0))
        lbl = QLabel(title)
        _ss(lbl, f"color: {TEXT_MAIN}; font-size: 32px; font-weight: bold; background: transparent;")
        head.addWidget(back); head.addWidget(lbl); head.addStretch()
        lay.addLayout(head)
        if expand:
            lay.addWidget(body, 1)
        else:
            lay.addWidget(body); lay.addStretch()
        return page, lbl

    # ---- page 0: the list ----
    def _build_settings_main_page(self):
        page = QWidget()
        lay = QVBoxLayout(page); lay.setContentsMargins(32, 28, 32, 12); lay.setSpacing(20)
        title = QLabel("Settings")
        _ss(title, f"color: {TEXT_MAIN}; font-size: 32px; font-weight: bold; background: transparent;")
        lay.addWidget(title)

        theme = AnimatedComboBox(); theme.setFixedSize(150, 38)
        theme.addItems(["Default", "Dark", "Light"]); _ss(theme, COMBO_QSS)
        i = theme.findText(str(self.settings.get("theme", "Default")))
        theme.setCurrentIndex(i if i >= 0 else 0)
        theme.currentTextChanged.connect(self._on_theme_changed)
        self.theme_combo = theme

        arrow = self._settings_arrow
        spec = [("Interface", "Theme", "top", None, theme, None),
                ("Downloads", "Change where downloads and trims appear", "middle", arrow, None, 1),
                ("Update", "yt-dlp  ·  ffmpeg  ·  App", "middle", arrow, None, 2),
                ("Feedback", "Report a bug  ·  Suggest a feature", "middle", arrow, None, 3),
                ("About", "Terms and conditions  ·  Change log", "bottom", arrow, None, 4)]
        rows = []
        for t, s, pos, arr, ctl, target in spec:
            row = SettingsRow(t, s, pos, arrow_pixmap=arr, control=ctl)
            if target is not None:
                row.clicked.connect(lambda idx=target: self._open_settings_page(idx))
            rows.append(row)
        card, cl = self._settings_card()
        self._settings_fill_card(cl, rows)
        lay.addWidget(card)
        lay.addStretch()
        return page

    def _on_theme_changed(self, text):
        self.settings["theme"] = text
        self._save_settings()
        # deferred: the combo that fired this is itself about to be restyled
        QTimer.singleShot(0, lambda: self._apply_theme(text))

    # ---- page 1: downloads ----
    def _build_settings_downloads_page(self):
        card, cl = self._settings_card()
        inner = QVBoxLayout(); inner.setContentsMargins(26, 20, 22, 22); inner.setSpacing(6)
        h = QLabel("Download folder")
        _ss(h, f"color: {TEXT_MAIN}; font-size: 19px; background: transparent; border: none;")
        sub = QLabel("Downloads and trims are saved here. Applies to new downloads.")
        _ss(sub, f"color: {TEXT_MUTED}; font-size: 14px; background: transparent; border: none;")
        row = QHBoxLayout(); row.setSpacing(10)
        self.dir_input = QLineEdit(self.settings.get("download_dir", str(repo_root / "downloads")))
        self.dir_input.setReadOnly(True); self.dir_input.setFixedHeight(40)
        _ss(self.dir_input, f"QLineEdit {{ background-color: {INPUT_BG}; color: {TEXT_MAIN}; border-radius: 10px; padding: 0 14px; border: none; font-size: 14px; }}")
        browse = self._settings_button("Browse", primary=True); browse.clicked.connect(self._settings_choose_dir)
        reset = self._settings_button("Reset");                 reset.clicked.connect(self._settings_reset_dir)
        row.addWidget(self.dir_input, 1); row.addWidget(browse); row.addWidget(reset)
        inner.addWidget(h); inner.addWidget(sub); inner.addSpacing(10); inner.addLayout(row)
        cl.addLayout(inner)
        return self._settings_subpage("Downloads", card)[0]

    def _settings_choose_dir(self):
        start = self.settings.get("download_dir", str(repo_root / "downloads"))
        d = QFileDialog.getExistingDirectory(self, "Select download folder", start)
        if d: self._apply_download_dir(os.path.normpath(d))

    def _settings_reset_dir(self):
        self._apply_download_dir(str(repo_root / "downloads"))

    def _apply_download_dir(self, d):
        try:
            os.makedirs(d, exist_ok=True)
            ok = os.access(d, os.W_OK)
        except OSError:
            ok = False
        if not ok:
            self.toast_mgr.show_toast("Can't write to that folder")
            return
        self.settings["download_dir"] = d
        self._save_settings()
        self.dir_input.setText(d)
        self.toast_mgr.show_toast("Download folder updated", icon_pixmap=self._icons.get("check", QIcon()).pixmap(22, 22))

    # ---- page 2: update (read-only versions for now) ----
    def _build_settings_update_page(self):
        self._upd_values = {}
        rows = []
        for n, (key, name) in enumerate([("yt-dlp", "yt-dlp"), ("ffmpeg", "ffmpeg"), ("app", "Dynamic")]):
            v = QLabel("…")
            _ss(v, f"color: {TEXT_MUTED}; font-size: 15px; background: transparent; border: none;")
            self._upd_values[key] = v
            rows.append(SettingsRow(name, "", ("top", "middle", "bottom")[n], control=v))
        card, cl = self._settings_card()
        self._settings_fill_card(cl, rows)
        body = QWidget(); bl = QVBoxLayout(body); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(12)
        bl.addWidget(card)
        note = QLabel("Installed versions. Updating from inside the app isn't set up yet.")
        _ss(note, f"color: {TEXT_MUTED}; font-size: 13px; background: transparent;")
        bl.addWidget(note)
        return self._settings_subpage("Update", body)[0]

    def _refresh_update_info(self):
        self._upd_values["yt-dlp"].setText(_pkg_version("yt-dlp") or "Not installed")
        self._upd_values["app"].setText(APP_VERSION)
        if not getattr(self, "_ffmpeg_ver_started", False):
            self._ffmpeg_ver_started = True
            threading.Thread(target=lambda: self.signals.ffmpeg_version.emit(_ffmpeg_version_text()), daemon=True).start()

    def _on_ffmpeg_version(self, text):
        if hasattr(self, "_upd_values"): self._upd_values["ffmpeg"].setText(text)

    # ---- page 3: feedback ----
    def _build_settings_feedback_page(self):
        arrow = self._settings_arrow
        bug = SettingsRow("Report a bug", "", "top", arrow_pixmap=arrow)
        bug.clicked.connect(lambda: self._open_feedback(FEEDBACK_BUG_URL))
        feat = SettingsRow("Suggest a feature", "", "bottom", arrow_pixmap=arrow)
        feat.clicked.connect(lambda: self._open_feedback(FEEDBACK_FEATURE_URL))
        card, cl = self._settings_card()
        self._settings_fill_card(cl, [bug, feat])
        return self._settings_subpage("Feedback", card)[0]

    def _open_feedback(self, url):
        if url: QDesktopServices.openUrl(QUrl(url))
        else: self.toast_mgr.show_toast("Feedback link isn't set up yet")

    # ---- page 4: about / page 5: text viewer ----
    def _build_settings_about_page(self):
        arrow = self._settings_arrow
        terms = SettingsRow("Terms and conditions", "", "top", arrow_pixmap=arrow)
        terms.clicked.connect(lambda: self._open_text_page("Terms and conditions", ("TERMS.md", "TERMS.txt", "terms.md", "terms.txt")))
        log = SettingsRow("Change log", "", "bottom", arrow_pixmap=arrow)
        log.clicked.connect(lambda: self._open_text_page("Change log", ("CHANGELOG.md", "CHANGELOG.txt", "changelog.md", "changelog.txt")))
        card, cl = self._settings_card()
        self._settings_fill_card(cl, [terms, log])
        return self._settings_subpage("About", card)[0]

    def _build_settings_text_page(self):
        self._text_view = QPlainTextEdit(); self._text_view.setReadOnly(True)
        _ss(self._text_view, f"QPlainTextEdit {{ background-color: {CARD_BG}; color: {TEXT_MAIN}; border: 1px solid #2A2A2A; border-radius: 14px; padding: 16px; font-size: 14px; }}")
        page, self._text_title = self._settings_subpage("", self._text_view, expand=True)
        # the viewer goes back to About, not to the top-level list
        back = page.findChild(QPushButton)
        if back is not None:
            back.clicked.disconnect()
            back.clicked.connect(lambda: self.settings_stack.setCurrentIndex(4))
        return page

    def _open_text_page(self, title, candidates):
        text = None
        for name in candidates:
            p = repo_root / name
            if p.exists():
                try:
                    text = p.read_text(encoding="utf-8", errors="replace"); break
                except OSError:
                    pass
        if text is None:
            text = f"Nothing here yet.\n\nAdd a file named {candidates[1]} next to main.py and its contents will show up here."
        self._text_title.setText(title)
        self._text_view.setPlainText(text)
        self.settings_stack.setCurrentIndex(5)

    # ------------------------------------------------------------------
    # Theme engine (see theme.py for how colors are mapped)
    # ------------------------------------------------------------------
    def _apply_palette(self):
        P = QPalette.ColorRole
        pal = QPalette(QApplication.instance().palette())
        for role, color in ((P.Window, _qc(BG_DARK, "bg")), (P.WindowText, _qc(TEXT_MAIN, "text")),
                            (P.Base, _qc(CARD_BG, "bg")), (P.AlternateBase, _qc(CARD_INNER_BG, "bg")),
                            (P.Text, _qc(TEXT_MAIN, "text")), (P.Button, _qc(CARD_BG, "bg")),
                            (P.ButtonText, _qc(TEXT_MAIN, "text")), (P.ToolTipBase, _qc("#242424", "bg")),
                            (P.ToolTipText, _qc(TEXT_MAIN, "text")), (P.PlaceholderText, _qc(TEXT_MUTED, "text")),
                            (P.Highlight, _qc(TEAL_ACCENT)), (P.HighlightedText, _qc("#000000")),
                            (P.Link, QColor(theme.accent_text()))):
            pal.setColor(role, color)
        QApplication.instance().setPalette(pal)

    def _apply_theme(self, setting=None):
        """Switch theme live. Every widget styled through _ss re-applies its
        stored source stylesheet in the new mode; icons, painted widgets and
        the title bar are refreshed explicitly."""
        setting = setting or self.settings.get("theme", "Default")
        changed = theme.set_mode(theme.resolve(setting))
        self._follow_system_theme(str(setting) == "Default")
        if not changed:
            return
        self._apply_palette()
        self._load_icons()
        self.setUpdatesEnabled(False)
        try:
            widgets = QApplication.instance().allWidgets()
            for w in widgets:
                theme.restyle(w)
            arrow = self._icons.get("arrow-right", QIcon()).pixmap(20, 20)
            if getattr(self, "_settings_built", False):
                self._settings_arrow = arrow
            for w in widgets:
                if isinstance(w, (FilterTab, SidebarNavButton)):
                    w.apply_theme()
                if isinstance(w, SidebarNavButton) and getattr(w, "_icon_name", None):
                    w.set_icon(self._icons.get(w._icon_name, QIcon()))
                elif isinstance(w, QPushButton):
                    n = w.property("_icon_name")
                    if isinstance(n, str):
                        w.setIcon(self._icons.get(n, QIcon()))
                    elif w.property("_arrow_back") and not arrow.isNull():
                        w.setIcon(QIcon(arrow.transformed(QTransform().scale(-1, 1))))
                elif isinstance(w, QLabel) and w.property("_arrow") and not arrow.isNull():
                    w.setPixmap(arrow)
            self.menu_btn.setIcon(self._icons.get("expand" if self._sidebar_collapsed else "collapse", QIcon()))
            tw = getattr(self, "trim_widget", None)
            if tw is not None and hasattr(tw, "apply_theme"):
                tw.apply_theme()
            for w in widgets:
                w.update()
        finally:
            self.setUpdatesEnabled(True)
        self._apply_titlebar_theme()

    def _follow_system_theme(self, enable):
        """With Theme = Default the app follows Windows' own light/dark setting live."""
        if not enable or getattr(self, "_sys_theme_hooked", False):
            return
        try:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self._on_system_scheme_changed())
            self._sys_theme_hooked = True
        except Exception:
            pass

    def _on_system_scheme_changed(self):
        if str(self.settings.get("theme", "Default")) == "Default":
            QTimer.singleShot(150, lambda: self._apply_theme("Default"))

    def _apply_titlebar_theme(self):
        """Dark/light native title bar to match (Windows 10 1809+ / 11)."""
        if sys.platform != "win32":
            return
        try:
            val = ctypes.c_int(0 if theme.is_light() else 1)
            hwnd = int(self.winId())
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                    break
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)   # nudge a frame redraw
        except Exception:
            pass

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_titlebar_theme()

    def _toggle_sidebar(self):
        self._sidebar_collapsed = not self._sidebar_collapsed
        collapsed = self._sidebar_collapsed

        self._sidebar_anim.stop()
        self._sidebar_anim.setStartValue(self.sidebar.width())
        self._sidebar_anim.setEndValue(self._sidebar_collapsed_w if collapsed else self._sidebar_expanded_w)

        self.menu_btn.setIcon(self._icons.get("expand" if collapsed else "collapse", QIcon()))

        if collapsed:
            for btn in self.nav_btns:
                btn.begin_collapse_fade()
        else:
            for btn in self.nav_btns:
                btn.begin_expand_layout()

        self._sidebar_anim.start()

    def _show_tab(self, idx):
        if self.stack.currentIndex() == idx:
            return
            
        if idx == 1 and not getattr(self, "_trim_loaded", False):
            from TrimLoad import TrimLoadTab
            self.trim_widget = TrimLoadTab(self)
            if hasattr(self.trim_widget, "request_trim"):
                self.trim_widget.request_trim.connect(self._handle_trim_request)
            self.trim_layout.addWidget(self.trim_widget)
            self._trim_loaded = True
            
        if idx == 3 and not getattr(self, "_settings_built", False):
            self._build_settings_tab()

        self.stack.setCurrentIndex(idx)
        for i, btn in enumerate(self.nav_btns):
            btn.set_active(i == idx)
        if idx == 2: self._ensure_dl_list_loaded()
        
    def _handle_trim_request(self, url, start, end):
        self._show_tab(0)
        self.url_entry.setText(url)
        self._process_link(url, start_time=start, end_time=end)

    def _build_home_tab(self):
        layout = QVBoxLayout(self.tab_home)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.home_center = QWidget()
        hc_layout = QVBoxLayout(self.home_center)
        hc_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl = QLabel("DYNAMIC")
        _ss(self.title_lbl, f"color: {TEXT_MAIN}; font-size: 68px; font-weight: 300;")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hc_layout.addWidget(self.title_lbl)
        hc_layout.addSpacing(20)
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("Enter link...")
        self.url_entry.setFixedSize(600, 54)
        _ss(self.url_entry, f"QLineEdit {{ background-color: {INPUT_BG}; color: {TEXT_MAIN}; border-radius: 27px; padding: 0 20px; font-size: 18px; border: none; }}")
        self.url_entry.returnPressed.connect(lambda: self._process_link(self.url_entry.text().strip()))
        hc_layout.addWidget(self.url_entry, alignment=Qt.AlignmentFlag.AlignCenter)
        self.home_slot = QWidget()
        self.home_slot_layout = QVBoxLayout(self.home_slot)
        hc_layout.addWidget(self.home_slot)
        layout.addWidget(self.home_center)

        self._proc_run_id, self._proc_cancel_flag = 0, None
        self.proc_overlay = ProcessingOverlay(self.tab_home)
        self.proc_overlay.cancel_requested.connect(self._on_processing_cancel)
        self.proc_overlay.retry_requested.connect(self._on_processing_cancel)

    def _clear_home_slot(self):
        while self.home_slot_layout.count():
            item = self.home_slot_layout.takeAt(0)
            w = item.widget()
            if w:
                for sb in w.findChildren(ShimmerBox):
                    ShimmerBox.unregister(sb)
                w.deleteLater()

    def _process_link(self, url, start_time=None, end_time=None):
        if not url: return
        self._last_url = url

        # Supersede whatever run may still be in flight.
        if self._proc_cancel_flag is not None: self._proc_cancel_flag[0] = True
        self._proc_run_id += 1
        run_id = self._proc_run_id
        cancelled = [False]
        self._proc_cancel_flag = cancelled

        host = (urlparse(url if "://" in url else "//" + url).netloc or url).lower()
        if host.startswith("www."): host = host[4:]
        self.proc_overlay.begin(host)

        # Once the overlay has faded in and fully covers the home screen,
        # tuck the title/input away underneath it (same end state as before).
        def _cover():
            if cancelled[0] or run_id != self._proc_run_id: return
            self._clear_home_slot(); self.title_lbl.hide(); self.url_entry.hide()
        QTimer.singleShot(300, _cover)

        def phase(key):
            if not cancelled[0]: self.signals.home_phase.emit(run_id, key)

        def _worker():
            phase("fetch")
            res = fetch_formats(url)
            if cancelled[0]: return
            if not res.get("ok"):
                self.signals.home_error.emit(res.get("error", "Unknown error"))
                return

            phase("process")
            force_audio = _is_audio_only_url(url) or res.get("audio_only", False)
            raw_dur = res.get("duration")
            if isinstance(raw_dur, str) and ":" in raw_dur:
                parts = raw_dur.split(":")
                try:
                    if len(parts) == 3: raw_dur = int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])
                    elif len(parts) == 2: raw_dur = int(parts[0])*60 + int(parts[1])
                except Exception: pass
            try: final_dur = float(raw_dur) if raw_dur else 0
            except (ValueError, TypeError): final_dur = 0
            
            info = {
                "title": res.get("title", "Unknown Title"), "uploader": res.get("channel") or res.get("uploader", "Unknown"), 
                "duration": final_dur, "_video_formats": [] if force_audio else res.get("video_formats",[]),
                "_audio_formats": res.get("audio_formats",[]), "_audio_only": force_audio,
                "thumbnail": res.get("thumbnail"), "is_live": res.get("is_live", False),
                "_run_id": run_id
            }
            
            phase("preview")
            thumb_path = None
            if info.get("thumbnail"):
                thumb_url = info["thumbnail"]
                if thumb_url.startswith("//"): thumb_url = "https:" + thumb_url
                try:
                    req = urllib.request.Request(thumb_url, headers={"User-Agent":"Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=8) as r: data = r.read()
                    t_hash = hashlib.md5(thumb_url.encode()).hexdigest()
                    thumb_path = str(thumb_dir / f"{t_hash}.png")
                    with open(thumb_path, 'wb') as f: f.write(data)
                except Exception: pass
            
            fav_path = _fetch_favicon_sync(url)
            if not cancelled[0]: self.signals.home_result.emit(info, url, thumb_path, fav_path, start_time, end_time)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_home_phase(self, run_id, key):
        if run_id == self._proc_run_id: self.proc_overlay.set_phase(key)

    def _on_home_result(self, info, url, thumb_path, fav_path, start_time, end_time):
        if info.get("_run_id") != self._proc_run_id: return
        if self._proc_cancel_flag is not None and self._proc_cancel_flag[0]: return
        run_id = self._proc_run_id
        def _reveal():
            if run_id != self._proc_run_id: return
            self.title_lbl.hide(); self.url_entry.hide()
            # Show the skeleton first and let it actually paint (the
            # profile card's thumbnail decode/scale + widget build is
            # real work, not free) before doing that work, so there's a
            # visible loading state instead of a dead pause.
            self._clear_home_slot()
            self.home_slot_layout.addWidget(self._build_home_skeleton_card(), alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            self.proc_overlay.dismiss()
            # 30ms was long enough to dodge the "frozen" case, but for a
            # fast fetch it meant the skeleton was on screen for less
            # than a couple of paint frames - technically shown, but not
            # actually perceivable. Give it a real minimum dwell so it
            # always reads as an intentional loading state.
            QTimer.singleShot(400, lambda: self._finish_home_reveal(run_id, info, url, thumb_path, fav_path, start_time, end_time))
        self.proc_overlay.finish(_reveal)

    def _finish_home_reveal(self, run_id, info, url, thumb_path, fav_path, start_time, end_time):
        if run_id != self._proc_run_id: return
        self._render_media_card_ui(info, url, thumb_path, fav_path, start_time, end_time)
        dur_s = round(self.proc_overlay._clock.elapsed() / 1000.0)
        color = theme.map_hex(ERROR_RED if dur_s >= 60 else TEXT_MAIN, "text")
        check_pix = self._icons.get("check", QIcon()).pixmap(22, 22)
        self.toast_mgr.show_toast(
            f'Finished in <span style="color: {color};">{dur_s}s</span>',
            icon_pixmap=check_pix, rich_text=True
        )

    def _on_home_error(self, msg):
        if self._proc_cancel_flag is not None and self._proc_cancel_flag[0]: return
        # The overlay's own failed state is now the complete error UI (it
        # has its own Try Again / Error Log buttons) - it just stays on
        # screen until the user acts, instead of auto-dismissing to a
        # separate fallback screen underneath.
        self.proc_overlay.fail(msg)

    def _on_processing_cancel(self):
        if self._proc_cancel_flag is not None: self._proc_cancel_flag[0] = True
        self._proc_run_id += 1
        self.title_lbl.show(); self.url_entry.show(); self._clear_home_slot()
        self.proc_overlay.dismiss()

    def _build_home_skeleton_card(self):
        """Mirrors _render_media_card_ui's real layout exactly (same
        widths/heights/radii/spacing pulled straight from that method) so
        the skeleton never has to guess at the card's shape."""
        outer = QFrame()
        _ss(outer, "QFrame { border: none; }")
        outer.setMaximumWidth(720)
        o_layout = QVBoxLayout(outer)
        o_layout.setContentsMargins(0, 0, 0, 0)
        o_layout.setSpacing(18)

        o_layout.addWidget(ShimmerBox(720, 405, radius=14), alignment=Qt.AlignmentFlag.AlignCenter)
        o_layout.addWidget(ShimmerBox(460, 30, radius=6))

        meta_row = QHBoxLayout(); meta_row.setSpacing(15)
        meta_row.addWidget(ShimmerBox(90, 18, radius=4))
        meta_row.addWidget(ShimmerBox(60, 18, radius=4))
        meta_row.addWidget(ShimmerBox(110, 18, radius=4))
        meta_row.addStretch()
        o_layout.addLayout(meta_row)
        o_layout.addSpacing(5)

        btn_row = QHBoxLayout(); btn_row.setSpacing(15)
        btn_row.addWidget(ShimmerBox(148, 38, radius=8))   # seg_frame (Video/Audio)
        btn_row.addWidget(ShimmerBox(160, 38, radius=6))   # quality combo (matches its real min-width)
        btn_row.addStretch()
        btn_row.addWidget(ShimmerBox(100, 38, radius=19))  # cancel (matches its real min-width)
        btn_row.addWidget(ShimmerBox(120, 38, radius=19))  # download (matches its real min-width)
        o_layout.addLayout(btn_row)

        return outer

    def _render_media_card_ui(self, info, url, thumb_path, fav_path, start_time=None, end_time=None):
        self._clear_home_slot()
        title_text = info.get("title", "Unknown Title")
        channel_text = info.get("uploader") or info.get("channel", "Unknown")
        dur_sec, audio_only, thumb_url, is_live = info.get("duration", 0), info.get("_audio_only", False), info.get("thumbnail"), info.get("is_live", False)

        if is_live: dur_str = "LIVE"
        elif dur_sec and float(dur_sec) > 0:
            t = int(float(dur_sec)); m, s = divmod(t, 60); h, m = divmod(m, 60)
            dur_str = f"{h}:{m:02d}:{s:02d} mins" if h else f"{m}:{s:02d} mins"
        else: dur_str = "0:00 mins"

        format_map, vf, af, video_q, audio_q = {}, info.get("_video_formats", []), info.get("_audio_formats", []), [], []
        
        for f in vf:
            label = f.get("label", "Unknown")
            if label == "Best": label = "Best quality"
            elif label.startswith("Best "): label = label.replace("Best ", "Best quality ", 1)
            
            if not any(x in label for x in ["MB", "KB", "GB"]):
                fs = f.get("filesize") or f.get("filesize_approx")
                if not fs and dur_sec and float(dur_sec) > 0:
                    kbps = 1800
                    if "4K" in label or "2160" in label: kbps = 8000
                    elif "1440" in label: kbps = 4000
                    elif "1080" in label: kbps = 1800
                    elif "720" in label: kbps = 900
                    elif "480" in label: kbps = 500
                    elif "360" in label: kbps = 300
                    elif "Best quality" in label: kbps = 2000
                    fs = ((kbps + 128) * 1000 * float(dur_sec)) / 8
                if fs:
                    size_str = f"~{max(1, round(fs / 1024))} KB" if fs < 1024 * 1024 else f"~{round(fs / (1024 * 1024), 1)} MB"
                    label = f"{label.split('—')[0].strip()} — {size_str}" if "—" in label else f"{label} — {size_str}"
            video_q.append(label); format_map[label] = f.get("format_id")
            
        for f in af:
            label = f.get("label", "Unknown")
            if not any(x in label for x in ["MB", "KB", "GB"]):
                fs = f.get("filesize") or f.get("filesize_approx")
                if not fs and dur_sec and float(dur_sec) > 0:
                    abr = 128
                    if "320" in label: abr = 320
                    elif "256" in label: abr = 256
                    elif "192" in label: abr = 192
                    elif "128" in label: abr = 128
                    elif "96" in label: abr = 96
                    elif "64" in label: abr = 64
                    fs = (abr * 1000 * float(dur_sec)) / 8
                if fs:
                    size_str = f"~{max(1, round(fs / 1024))} KB" if fs < 1024 * 1024 else f"~{round(fs / (1024 * 1024), 1)} MB"
                    label = f"{label.split('—')[0].strip()} — {size_str}" if "—" in label else f"{label} — {size_str}"
            audio_q.append(label); format_map[label] = f.get("format_id")
            
        if not audio_q: audio_q = [f"MP3 {br}kbps" for br in (320, 256, 192, 128, 96, 64)]
        site = _site_name(url)

        outer = QFrame()
        _ss(outer, "QFrame { border: none; }")
        outer.setMaximumWidth(720)
        
        o_layout = QVBoxLayout(outer)
        o_layout.setContentsMargins(0, 0, 0, 0)
        o_layout.setSpacing(18)

        thumb_lbl = QLabel()
        thumb_lbl.setFixedSize(720, 405) 
        _ss(thumb_lbl, f"background-color: {CARD_INNER_BG}; border-radius: 8px;")
        if thumb_path and os.path.exists(thumb_path):
            pix = QPixmap(thumb_path).scaled(720, 405, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            thumb_lbl.setPixmap(get_rounded_pixmap(pix, 14))
        o_layout.addWidget(thumb_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        t_lbl = QLabel(title_text)
        _ss(t_lbl, f"color: {TEXT_MAIN}; font-size: 34px; font-weight: normal; border: none;")
        t_lbl.setWordWrap(True)
        o_layout.addWidget(t_lbl)

        meta_layout = QHBoxLayout()
        meta_layout.setSpacing(15)
        
        c_lbl = QLabel(channel_text)
        _ss(c_lbl, f"color: {TEXT_MAIN}; font-size: 16px; border: none;")
        meta_layout.addWidget(c_lbl)

        d_lbl = QLabel(dur_str)
        _ss(d_lbl, f"color: {'#FF5555' if is_live else TEXT_MAIN}; font-size: 16px; font-weight: {'bold' if is_live else 'normal'}; border: none;")
        meta_layout.addWidget(d_lbl)
        
        site_layout = QHBoxLayout()
        site_layout.setSpacing(6)
        if fav_path and os.path.exists(fav_path):
            fav_lbl = QLabel()
            _ss(fav_lbl, "border: none; background: transparent;")
            fav_lbl.setPixmap(QPixmap(fav_path).scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            site_layout.addWidget(fav_lbl)
        else:
            site_icon = QLabel("▶" if "Youtube" in site else "🌐")
            _ss(site_icon, f"color: {'#FF0000' if 'Youtube' in site else TEAL_ACCENT}; font-size: 18px; border: none;")
            site_layout.addWidget(site_icon)
            
        site_lbl = QLabel(site)
        site_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        _ss(site_lbl, f"color: {TEXT_MAIN}; font-size: 16px; border: none;")
        site_lbl.mousePressEvent = lambda e: self._open_qurl(QUrl(url))
        
        site_layout.addWidget(site_lbl)
        meta_layout.addLayout(site_layout)
        meta_layout.addStretch()
        
        o_layout.addLayout(meta_layout)
        o_layout.addSpacing(5)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(15)

        seg_frame = QFrame()
        _ss(seg_frame, "QFrame { background-color: #222222; border-radius: 8px; }")
        seg_frame.setFixedHeight(38)
        seg_layout = QHBoxLayout(seg_frame)
        seg_layout.setContentsMargins(4, 4, 4, 4)
        seg_layout.setSpacing(2)
        
        bv = QPushButton("Video")
        bv.setFixedHeight(30)
        bv.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        
        ba = QPushButton("Audio")
        ba.setFixedHeight(30)
        ba.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        
        def update_btn_styles(mode):
            act = f"QPushButton {{ background-color: #333333; color: {TEAL_ACCENT}; font-size: 14px; border-radius: 6px; border: none; padding: 0 15px; }}"
            inact = f"QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; font-size: 14px; border-radius: 6px; border: none; padding: 0 15px; }} QPushButton:hover {{ background-color: #2A2A2A; }}"
            if mode == "Video": _ss(bv, act); _ss(ba, inact)
            else: _ss(bv, inact); _ss(ba, act)
        
        if audio_only:
            bv.setDisabled(True)
            _ss(bv, "QPushButton { background-color: transparent; color: #444; font-size: 14px; border-radius: 6px; border: none; padding: 0 15px; }")
            _ss(ba, f"QPushButton {{ background-color: #333333; color: {TEAL_ACCENT}; font-size: 14px; border-radius: 6px; border: none; padding: 0 15px; }}")
        else: update_btn_styles("Video")
        
        seg_layout.addWidget(bv)
        seg_layout.addWidget(ba)
        btn_layout.addWidget(seg_frame)

        qm = AnimatedComboBox()
        qm.setFixedHeight(38)
        qm.setMinimumWidth(160)
        _ss(qm, f"""
            QComboBox {{ 
                background-color: #1A1A1A; 
                color: #FFFFFF; 
                font-size: 14px; 
                border-radius: 6px; 
                padding: 5px 12px; 
                border: 1px solid #2A2A2A; 
            }}
            QComboBox::drop-down {{ 
                border: none; 
                width: 0px; 
            }}
            QComboBox::down-arrow {{
                image: none;
                border: none;
            }}
            QComboBox QAbstractItemView {{ 
                background-color: #181818; 
                color: #FFFFFF; 
                selection-background-color: #B5B5B5; 
                selection-color: #000000; 
                border: 1px solid #FFFFFF; 
                border-radius: 4px; 
                outline: none; 
                padding: 2px 0px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 26px;
                padding-left: 10px;
                color: #FFFFFF;
            }}
            QComboBox QAbstractItemView::item:selected {{
                background-color: #B5B5B5;
                color: #000000;
            }}
        """)
        qm.addItems(audio_q if audio_only else (video_q or ["Best quality"]))
        btn_layout.addWidget(qm)

        btn_layout.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setFixedHeight(38)
        btn_cancel.setMinimumWidth(100)
        btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        _ss(btn_cancel, f"QPushButton {{ background-color: transparent; border: 1px solid #777777; color: {TEXT_MAIN}; font-size: 14px; border-radius: 19px; }} QPushButton:hover {{ background-color: #222222; }}")
        btn_layout.addWidget(btn_cancel)

        btn_dl = QPushButton("Download")
        btn_dl.setFixedHeight(38)
        btn_dl.setMinimumWidth(120)
        btn_dl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        if is_live: 
            _ss(btn_dl, "QPushButton { background-color: #222222; color: #666666; font-size: 14px; font-weight: bold; border-radius: 19px; border: 1px solid #333333; } QPushButton:hover { background-color: #262626; }")
        else: 
            _ss(btn_dl, f"QPushButton {{ background-color: {TEAL_ACCENT}; color: #000000; font-size: 14px; font-weight: bold; border-radius: 19px; border: none; }} QPushButton:hover {{ background-color: #00A892; }}")
        btn_layout.addWidget(btn_dl)

        o_layout.addLayout(btn_layout)

        current_mode = ["Audio" if audio_only else "Video"]
        def set_mode(mode):
            if audio_only and mode == "Video": return
            current_mode[0] = mode; qm.clear()
            if mode == "Video": update_btn_styles("Video"); qm.addItems(video_q or ["Best quality"])
            else: update_btn_styles("Audio"); qm.addItems(audio_q or ["Best Audio"])
            
        bv.clicked.connect(lambda: set_mode("Video"))
        ba.clicked.connect(lambda: set_mode("Audio"))

        def _cancel(): self.title_lbl.show(); self.url_entry.show(); self.url_entry.clear(); self._clear_home_slot()
        btn_cancel.clicked.connect(_cancel)
        
        def _on_download():
            if is_live: jiggle_widget(btn_dl); self.toast_mgr.show_toast("Can't download live streams"); return
            item = DownloadItem(
                url=url, title=title_text, channel=channel_text, thumb_path=thumb_path, 
                out_dir=self.settings.get("download_dir", str(repo_root / "downloads")), is_audio=(current_mode[0] == "Audio"), 
                selected_fid=format_map.get(qm.currentText()), duration_str=dur_str, 
                site_name=site, thumb_url=thumb_url, fav_path=fav_path,
                start_time=start_time, end_time=end_time
            )
            self._dl_items.append(item)
            self._dl_item_added(item)
            self._start_download(item); self._show_tab(2); _cancel()
            
        btn_dl.clicked.connect(_on_download)
        
        self.home_slot_layout.addWidget(outer, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

    def _ensure_dl_list_loaded(self):
        if not self._dl_list_dirty:
            return
        if not self._dl_first_build_done:
            self._dl_first_build_done = True
            self._show_dl_skeleton()
            QTimer.singleShot(30, self._full_rebuild_dl_cards)
        else:
            self._full_rebuild_dl_cards()

    def _show_dl_skeleton(self):
        while self.scroll_layout.count():
            it = self.scroll_layout.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        skeleton_count = min(max(len(self._dl_items), 1), 5)
        for _ in range(skeleton_count):
            self.scroll_layout.addWidget(self._build_skeleton_card())
        self.scroll_layout.addStretch()

    def _build_skeleton_card(self):
        wrapper = QWidget()
        wrap_v = QVBoxLayout(wrapper); wrap_v.setContentsMargins(0, 0, 0, 14); wrap_v.setSpacing(0)

        card = QFrame(); card.setFixedHeight(180)
        _ss(card, f"QFrame {{ background-color: {CARD_BG}; border: 1px solid #333333; border-radius: 14px; }}")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(24); shadow.setColor(QColor(0, 0, 0, 130)); shadow.setOffset(0, 6)
        card.setGraphicsEffect(shadow)

        main_h = QHBoxLayout(card); main_h.setContentsMargins(16, 16, 16, 16); main_h.setSpacing(20)
        main_h.addWidget(SkeletonBox(240, 135, radius=14))

        right_v = QVBoxLayout(); right_v.setContentsMargins(0, 6, 0, 6); right_v.setSpacing(14)
        right_v.addWidget(SkeletonBox(None, 22, radius=4))
        meta_row = QHBoxLayout(); meta_row.setSpacing(10)
        meta_row.addWidget(SkeletonBox(90, 13, radius=4))
        meta_row.addWidget(SkeletonBox(60, 13, radius=4))
        meta_row.addWidget(SkeletonBox(70, 13, radius=4))
        meta_row.addStretch()
        right_v.addLayout(meta_row)
        right_v.addStretch()

        ctrl_row = QHBoxLayout(); ctrl_row.setSpacing(12)
        ctrl_row.addWidget(SkeletonBox(36, 36, radius=18))
        bar_col = QVBoxLayout(); bar_col.setSpacing(8)
        bar_col.addWidget(SkeletonBox(None, 4, radius=2))
        stats_row = QHBoxLayout()
        stats_row.addWidget(SkeletonBox(160, 12, radius=4))
        stats_row.addStretch()
        stats_row.addWidget(SkeletonBox(90, 12, radius=4))
        bar_col.addLayout(stats_row)
        ctrl_row.addLayout(bar_col)
        right_v.addLayout(ctrl_row)

        main_h.addLayout(right_v)
        wrap_v.addWidget(card)
        return wrapper

    def _build_downloads_tab(self):
        main_layout = QVBoxLayout(self.tab_dl); main_layout.setContentsMargins(0, 0, 0, 0); main_layout.setSpacing(0)
        top_container = QWidget(); top_layout = QVBoxLayout(top_container); top_layout.setContentsMargins(32, 28, 32, 12); top_layout.setSpacing(16)
        lbl = QLabel("Downloads"); _ss(lbl, f"color: {TEXT_MAIN}; font-size: 32px; font-weight: bold;"); top_layout.addWidget(lbl)
        filters = ["All", "Active", "Paused", "Done", "Failed"]
        self.filter_tab_bar = FilterTabBar(filters); self.filter_tab_bar.filter_changed.connect(self._on_filter_changed)
        top_layout.addWidget(self.filter_tab_bar, alignment=Qt.AlignmentFlag.AlignLeft)
        main_layout.addWidget(top_container)
        self.scroll = ModernScrollArea(); self.scroll_content = QWidget(); _ss(self.scroll_content, "background-color: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content); self.scroll_layout.setContentsMargins(32, 10, 32, 16); self.scroll_layout.setSpacing(16); self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content); main_layout.addWidget(self.scroll, 1)
        self._dl_empty_label = None

    def _on_filter_changed(self, f_name):
        if self._dl_tab_filter == f_name: return
        self._dl_tab_filter = f_name
        self._sync_dl_filter_view()

    def _full_rebuild_dl_cards(self):
        while self.scroll_layout.count():
            it = self.scroll_layout.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        self._dl_cards = {}
        for item in reversed(self._dl_items):
            self.scroll_layout.addWidget(self._build_dl_card_widget(item))
        self._dl_empty_label = QLabel("No downloads here")
        _ss(self._dl_empty_label, f"color: {TEXT_MUTED}; font-size: 15px;")
        self._dl_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_layout.addWidget(self._dl_empty_label)
        self.scroll_layout.addStretch()
        self._dl_list_dirty = False
        self._sync_dl_filter_view()

    def _sync_dl_filter_view(self):
        counts = {"All": len(self._dl_items), "Active": sum(1 for d in self._dl_items if d.status=="active"), "Paused": sum(1 for d in self._dl_items if d.status=="paused"), "Done": sum(1 for d in self._dl_items if d.status=="done"), "Failed": sum(1 for d in self._dl_items if d.status=="failed")}
        self.filter_tab_bar.update_counts(counts)
        visible = 0
        for item in self._dl_items:
            card = self._dl_cards.get(item.id)
            if not card: continue
            match = self._dl_tab_filter == "All" or item.status == self._dl_tab_filter.lower()
            if card.isVisible() != match: card.setVisible(match)
            if match: visible += 1
        if self._dl_empty_label is not None:
            self._dl_empty_label.setVisible(visible == 0)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if visible >= 3 else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _dl_item_added(self, item):
        if not self._dl_first_build_done: return
        self.scroll_layout.insertWidget(0, self._build_dl_card_widget(item))
        self._sync_dl_filter_view()

    def _dl_item_removed(self, item):
        if not self._dl_first_build_done: return
        card = self._dl_cards.pop(item.id, None)
        if card:
            self.scroll_layout.removeWidget(card)
            card.deleteLater()
        self._sync_dl_filter_view()

    def _build_dl_card_widget(self, item: DownloadItem):
        card = QFrame(); card.setFixedHeight(180)
        _ss(card, f"QFrame {{ background-color: {CARD_BG}; border: 1px solid #2A2A2A; border-radius: 14px; }}")
        main_h = QHBoxLayout(card); main_h.setContentsMargins(16, 16, 16, 16); main_h.setSpacing(20)
        thumb_lbl = QLabel(); thumb_lbl.setFixedSize(240, 135)
        _ss(thumb_lbl, f"background-color: {CARD_INNER_BG}; border-radius: 14px; border: none;")
        if item.thumb_path and os.path.exists(item.thumb_path):
            pix = QPixmap(item.thumb_path).scaled(240, 135, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            thumb_lbl.setPixmap(get_rounded_pixmap(pix, 14))
        main_h.addWidget(thumb_lbl)
        right_v = QVBoxLayout(); right_v.setContentsMargins(0, 0, 0, 0); right_v.setSpacing(4)
        title_h = QHBoxLayout()
        title_lbl = QLabel(item.title); _ss(title_lbl, f"color: {TEXT_MAIN}; font-size: 20px; font-weight: bold; border: none;"); title_lbl.setWordWrap(True)
        title_h.addWidget(title_lbl, 1)
        trash_btn = QPushButton(); trash_btn.setIcon(self._icons.get("trash", QIcon())); trash_btn.setProperty("_icon_name", "trash"); trash_btn.setIconSize(QSize(22, 22)); trash_btn.setFixedSize(32, 32); trash_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        _ss(trash_btn, f"QPushButton {{ background: transparent; border: none; }} QPushButton:hover {{ background: #2A1A1A; border-radius: 8px; }}")
        
        def _trash_click():
            if item._trash_ready:
                if item in self._dl_items: self._dl_items.remove(item)
                item._cancelled = True; self._dl_item_removed(item)
            else:
                item._trash_ready = True; trash_btn.setIcon(self._icons.get("trash red", QIcon())); trash_btn.setProperty("_icon_name", "trash red")
                QTimer.singleShot(3000, lambda: _reset_trash())
        def _reset_trash():
            item._trash_ready = False
            try: trash_btn.setIcon(self._icons.get("trash", QIcon())); trash_btn.setProperty("_icon_name", "trash")
            except RuntimeError: pass
        trash_btn.clicked.connect(_trash_click); title_h.addWidget(trash_btn); right_v.addLayout(title_h)
        meta_h = QHBoxLayout()
        ch_lbl = QLabel(item.channel); _ss(ch_lbl, f"color: {TEXT_MAIN}; font-size: 14px; border: none;"); meta_h.addWidget(ch_lbl)
        dur_lbl = QLabel(f" •  {item.duration_str}"); _ss(dur_lbl, f"color: {TEXT_MAIN}; font-size: 14px; border: none;"); meta_h.addWidget(dur_lbl)
        dot_lbl = QLabel(" • "); _ss(dot_lbl, f"color: #DDDDDD; font-size: 14px; border: none;"); meta_h.addWidget(dot_lbl)
        if item.fav_path and os.path.exists(item.fav_path):
            fav_lbl = QLabel(); _ss(fav_lbl, "border: none; background: transparent;")
            fav_lbl.setPixmap(QPixmap(item.fav_path).scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            meta_h.addWidget(fav_lbl)
        site_lbl = QLabel(item.site_name); site_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor)); _ss(site_lbl, f"color: {TEXT_MUTED}; font-size: 14px; border: none;"); site_lbl.mousePressEvent = lambda e, u=item.url: self._open_qurl(QUrl(u)); meta_h.addWidget(site_lbl)
        meta_h.addStretch(); right_v.addLayout(meta_h); right_v.addStretch()

        if item.status == "failed":
            err_frame = QFrame(); _ss(err_frame, f"background-color: {ERROR_BG}; border: 1px solid #4A2020; border-radius: 8px;")
            err_frame.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            err_layout = QVBoxLayout(err_frame); err_layout.setContentsMargins(10, 6, 10, 6)
            clean_err = (item.error_msg or "Unknown error").strip().replace("\n", " ")
            if len(clean_err) > 130: clean_err = clean_err[:130] + "..."
            err_lbl = QLabel(clean_err); err_lbl.setWordWrap(True); err_lbl.setMaximumWidth(520); _ss(err_lbl, "color: #FF8888; font-size: 11px; border: none;"); err_layout.addWidget(err_lbl)
            right_v.addWidget(err_frame, alignment=Qt.AlignmentFlag.AlignLeft)
            retry_h = QHBoxLayout(); retry_h.setContentsMargins(0, 4, 0, 0)
            retry_btn = QPushButton(" Retry"); retry_btn.setIcon(self._icons.get("retry", QIcon())); retry_btn.setProperty("_icon_name", "retry"); retry_btn.setIconSize(QSize(18, 18)); retry_btn.setFixedSize(90, 32); retry_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor)); _ss(retry_btn, f"QPushButton {{ background-color: #2A2A2A; color: {TEXT_MAIN}; font-weight: bold; border-radius: 16px; border: none; }} QPushButton:hover {{ background-color: #333333; }}")
            def _retry(): item.status, item.error_msg, item.percent = "active", "", 0.0; self._start_download(item); self._refresh_single_card(item)
            retry_btn.clicked.connect(_retry); retry_h.addWidget(retry_btn); retry_h.addStretch(); right_v.addLayout(retry_h)
        else:
            prog_h = QHBoxLayout(); prog_h.setSpacing(12)
            if item.status != "done":
                ctrl_btn = QPushButton(); ctrl_btn.setFixedSize(36, 36); ctrl_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor)); _ss(ctrl_btn, f"QPushButton {{ background: transparent; border: none; }} QPushButton:hover {{ background: #252525; border-radius: 8px; }}")
                if item.status == "active":
                    ctrl_btn.setIcon(self._icons.get("pause", QIcon())); ctrl_btn.setProperty("_icon_name", "pause"); ctrl_btn.setIconSize(QSize(24, 24))
                    def _pause():
                        if item.duration_str == "LIVE": self.toast_mgr.show_toast("Download cannot be paused"); return
                        item.status = "paused"; item._cancelled = True; item.speed, item.eta = "--", "--"; self._refresh_single_card(item)
                    ctrl_btn.clicked.connect(_pause)
                elif item.status == "paused":
                    ctrl_btn.setIcon(self._icons.get("play", QIcon())); ctrl_btn.setProperty("_icon_name", "play"); ctrl_btn.setIconSize(QSize(24, 24))
                    def _resume(): item.status = "active"; item._cancelled = False; item.speed, item.eta = "Resuming...", "--"; self._start_download(item); self._refresh_single_card(item)
                    ctrl_btn.clicked.connect(_resume)
                prog_h.addWidget(ctrl_btn)
            bar_v = QVBoxLayout(); bar_v.setSpacing(6)
            if item.status != "done":
                pb = QProgressBar(); pb.setFixedHeight(4); pb.setTextVisible(False); pb.setMaximum(1000); pb.setValue(int(item.percent * 1000))
                _ss(pb, f"QProgressBar {{ background-color: #333333; border: none; border-radius: 2px; }} QProgressBar::chunk {{ background-color: {TEAL_ACCENT}; border-radius: 2px; }}")
                bar_v.addWidget(pb); card.prog_bar = pb
            stats_h = QHBoxLayout()
            dl_str, tot_str, pct_val, spd, eta = item.downloaded or "0MB", item.total_size or "Unknown", item.percent * 100, item.speed or "0Mb/s", item.eta or "--:--"
            stat_txt = "Download complete" if item.status == "done" else (f"Paused - {int(item.percent*100)}%" if item.status == "paused" else f"{dl_str} / {tot_str} ({pct_val:.1f}%)   {spd}   ETA: {eta}")
            stat_lbl = QLabel(stat_txt); _ss(stat_lbl, f"color: {TEAL_ACCENT if item.status == 'done' else (TEXT_MAIN if item.status == 'active' else TEXT_MUTED)}; font-size: 12px; font-weight: {'bold' if item.status == 'done' else 'normal'}; border: none;"); stats_h.addWidget(stat_lbl); card.stat_lbl = stat_lbl
            path_lbl = QLabel(item.out_dir); path_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor)); _ss(path_lbl, f"color: {TEXT_MUTED}; font-size: 12px; border: none;"); path_lbl.mousePressEvent = lambda e, it=item: self._reveal_in_explorer(it); stats_h.addWidget(path_lbl, alignment=Qt.AlignmentFlag.AlignRight)
            bar_v.addLayout(stats_h); prog_h.addLayout(bar_v); right_v.addLayout(prog_h)

        main_h.addLayout(right_v)
        self._dl_cards[item.id] = card
        return card

    def _start_download(self, item: DownloadItem):
        def _worker():
            evts = fetcher_download(item.url, item.selected_fid, item.out_dir, item.is_audio, item.start_time, item.end_time)
            for evt in evts:
                if item._cancelled: break
                t = evt.get("type")
                if t == "progress":
                    item.percent, item.speed, item.eta = evt.get("percent", 0), evt.get("speed", ""), evt.get("eta", "")
                    item.downloaded, item.total_size = evt.get("downloaded", ""), evt.get("size", "")
                    self.signals.update_progress.emit(item)
                elif t == "done":
                    item.status, item.percent = "done", 1.0
                    item.final_path = evt.get("filepath")
                    self.signals.refresh_card.emit(item)
                elif t == "error":
                    item.status, item.error_msg = "failed", evt.get("message", "Unknown error")
                    self._log_download_error(item)
                    self.signals.refresh_card.emit(item)
        item._thread = threading.Thread(target=_worker, daemon=True); item._thread.start()

    def _log_download_error(self, item):
        try:
            log_dir = repo_root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            clean_err = (item.error_msg or "Unknown error").strip().replace("\n", " ")
            line = f'[{ts}] FAILED  title="{item.title}"  url={item.url}  error={clean_err}\n'
            with open(log_dir / "download_errors.log", "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def _update_card_progress(self, item):
        if item.id not in self._dl_cards: return
        card = self._dl_cards[item.id]
        if hasattr(card, "prog_bar"): card.prog_bar.setValue(int(item.percent * 1000))
        if hasattr(card, "stat_lbl"):
            dl_str, tot_str, pct_val, spd, eta = item.downloaded or "0MB", item.total_size or "Unknown", item.percent * 100, item.speed or "0Mb/s", item.eta or "--:--"
            txt = "Download complete" if item.status == "done" else (f"Paused - {int(item.percent*100)}%" if item.status == "paused" else f"{dl_str} / {tot_str} ({pct_val:.1f}%)   {spd}   ETA: {eta}")
            card.stat_lbl.setText(txt)

    def _show_download_notification(self, item):
        raw_title = item.title or "File"
        if hasattr(self, "tray_icon") and self.tray_icon.isSystemTrayAvailable():
            self.tray_icon.showMessage("Download complete", raw_title, QIcon(ICON_PATH), 5000)

    def _refresh_single_card(self, item):
        if not self._dl_first_build_done:
            return
        old = self._dl_cards.pop(item.id, None)
        idx = -1
        if old is not None:
            try:
                idx = self.scroll_layout.indexOf(old)
                self.scroll_layout.removeWidget(old)
                old.deleteLater()
            except RuntimeError:
                idx = -1
        new_card = self._build_dl_card_widget(item)
        self.scroll_layout.insertWidget(idx if idx >= 0 else 0, new_card)
        self._sync_dl_filter_view()
        if item.status == "done" and not getattr(item, "_notified", False):
            item._notified = True; self._show_download_notification(item)

def configure_app(app):
    """App-wide Qt configuration. Split out of the __main__ block so
    launcher.py (the light startup stub that shows a splash screen first)
    can apply exactly the same setup to the QApplication it already
    created, instead of duplicating it."""
    app.setWindowIcon(QIcon(str(repo_root / "icons" / "icon.ico")))

    app.setStyle(NoFocusRectStyle(app.style()))

    ctx_menu_blocker = GlobalContextMenuBlocker(app)
    app.installEventFilter(ctx_menu_blocker)
    app._ctx_menu_blocker = ctx_menu_blocker  # keep a Python ref alive

    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Highlight, _qc(TEAL_ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, _qc("#000000"))
    app.setPalette(palette)


def main():
    app = QApplication(sys.argv)
    configure_app(app)
    window = DynamicPC()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()