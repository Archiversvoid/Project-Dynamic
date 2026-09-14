import sys
import os
import json
import threading
import time
import queue
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
                             QSystemTrayIcon, QStyle)
from PySide6.QtCore import (Qt, QThread, Signal, QSize, QObject, 
                          QTimer, QUrl, QVariantAnimation, QPropertyAnimation, QEasingCurve,
                          QPoint, QParallelAnimationGroup, QSequentialAnimationGroup)
from PySide6.QtGui import (QFont, QFontDatabase, QIcon, QPixmap, QImage, QColor, 
                         QPainter, QPainterPath, QCursor, QDesktopServices)

from TrimLoad import TrimLoadTab

try:
    import yt_dlp
except Exception:
    yt_dlp = None

repo_root = Path(__file__).resolve().parent
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

            if is_audio:
                ydl_opts['format'] = 'bestaudio/best'
                ydl_opts['postprocessors'] = [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }]
            elif selected_fid:
                ydl_opts['format'] = f"{selected_fid}+bestaudio/best"
            else:
                ydl_opts['format'] = 'bestvideo+bestaudio/best'

            if start_time is not None and end_time is not None:
                def range_func(info_dict, ydl):
                    return [{'start_time': start_time, 'end_time': end_time}]
                ydl_opts['download_ranges'] = range_func
                ydl_opts['force_keyframes_at_cuts'] = True
                
                if not is_audio:
                    ydl_opts.setdefault('external_downloader_args', {})
                    ydl_opts['external_downloader_args']['ffmpeg'] = ['-c:v', 'libx264', '-preset', 'fast', '-c:a', 'aac']

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            q.put({"type": "done"})
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
    def __init__(self, message, parent=None):
        super().__init__(parent)
        self.setFixedSize(290, 48)
        self.setStyleSheet("QFrame { background-color: #242424; border: 1px solid #333333; border-radius: 12px; }")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(10)
        icon_lbl = QLabel("✕")
        icon_lbl.setStyleSheet("color: #EF4444; font-size: 15px; font-weight: bold; border: none; background: transparent;")
        layout.addWidget(icon_lbl)
        msg_lbl = QLabel(message)
        msg_lbl.setStyleSheet("color: #FFFFFF; font-size: 13px; font-weight: 500; border: none; background: transparent;")
        layout.addWidget(msg_lbl, 1)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.setStyleSheet("QPushButton { color: #888888; font-size: 13px; font-weight: bold; border: none; background: transparent; } QPushButton:hover { color: #FFFFFF; }")
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
    def show_toast(self, message):
        toast = ToastWidget(message, parent=self.win)
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

class ModernScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("""
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
        self.text_lbl.setFont(QFont("Roboto", 10, QFont.Weight.Medium))
        self.badge_lbl = QLabel(str(count if count < 100 else "99+"))
        self.badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge_lbl.setFixedHeight(20)
        self.badge_lbl.setMinimumWidth(16)
        self.layout.addWidget(self.text_lbl)
        self.layout.addWidget(self.badge_lbl)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(250)
        self._anim.valueChanged.connect(self._on_anim_step)
        self._current_text = QColor(TEAL_ACCENT) if is_active else QColor("#E0E0E0")
        self._current_badge_bg = QColor("#1B3D37") if is_active else QColor("#3D3D3D")
        self._current_bg = QColor("#142C28") if is_active else QColor("#2A2A2A")
        self._update_stylesheet()
    def update_count(self, count):
        if self._count != count:
            self._count = count
            self.badge_lbl.setText(str(count if count < 100 else "99+"))
    def set_active(self, active):
        if self._is_active == active: return
        self._is_active = active
        self._animate_colors(QColor(TEAL_ACCENT) if active else QColor("#E0E0E0"), 
                             QColor("#1B3D37") if active else QColor("#3D3D3D"), 
                             QColor("#142C28") if active else QColor("#2A2A2A"), 250)
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
    def _update_stylesheet(self):
        text, badge_bg, bg = self._current_text.name(), self._current_badge_bg.name(), self._current_bg.name()
        self.setStyleSheet(f"FilterTab {{ background-color: {bg}; border-radius: 16px; border: none; }}")
        self.text_lbl.setStyleSheet(f"color: {text}; border: none; background: transparent;")
        self.badge_lbl.setStyleSheet(f'background-color: {badge_bg}; color: {text}; border-radius: 10px; min-width: 14px; font-size: 11px; font-weight: bold; padding: 0px 8px; border: none;')
    def enterEvent(self, event):
        if not self._is_active: self._animate_colors(QColor("#FFFFFF"), QColor("#555555"), QColor("#333333"), 150)
        super().enterEvent(event)
    def leaveEvent(self, event):
        if not self._is_active: self._animate_colors(QColor("#E0E0E0"), QColor("#3D3D3D"), QColor("#2A2A2A"), 150)
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
        self.indicator.setStyleSheet("background-color: transparent; border: none; border-radius: 16px;")
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

class AppSignals(QObject):
    update_progress = Signal(object)
    refresh_card = Signal(object)
    home_result = Signal(dict, str, object, object)
    home_error = Signal(str)

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
        self.timestamp = datetime.now().strftime("%H:%M")
        self._thread, self._cancelled, self._trash_ready, self._notified = None, False, False, False

class DynamicPC(QMainWindow):
    DownloadItem = DownloadItem

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dynamic")
        self.resize(1000, 660)
        self.setMinimumSize(860, 560)
        self.setStyleSheet(f"QMainWindow {{ background-color: {BG_DARK}; }}")
        self.toast_mgr = ToastManager(self)
        self.signals = AppSignals()
        self.signals.update_progress.connect(self._update_card_progress)
        self.signals.refresh_card.connect(self._refresh_single_card)
        self.signals.home_result.connect(self._render_media_card_ui)
        self.signals.home_error.connect(self._show_error)
        font_path = str(repo_root / "assets" / "Roboto-Regular.ttf")
        if os.path.exists(font_path): QFontDatabase.addApplicationFont(font_path)
        app_font = QFont("Roboto", 10)
        app_font.setStyleHint(QFont.StyleHint.SansSerif)
        QApplication.setFont(app_font)
        self._dl_items, self._dl_cards, self._dl_tab_filter, self._last_url = [], {}, "All", ""
        self._load_icons()
        self._setup_tray_icon()
        self._load_downloads()
        self._setup_ui()
        self._show_tab(0)

    def _setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        app_icon = self._icons.get("home") or QIcon()
        if not app_icon.isNull(): self.tray_icon.setIcon(app_icon)
        else: self.tray_icon.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        self.tray_icon.show()
        self.tray_icon.messageClicked.connect(self._on_tray_message_clicked)

    def _on_tray_message_clicked(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()
        self._show_tab(2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "toast_mgr"): self.toast_mgr.update_positions()

    def _open_qurl(self, qurl): QDesktopServices.openUrl(qurl)

    def _load_icons(self):
        self._icons = {}
        for name in ["home", "scissors", "downloads", "settings", "pause", "play", "trash", "trash red", "retry", "check"]:
            path = repo_root / "icons" / f"{name}.png"
            self._icons[name] = QIcon(str(path)) if path.exists() else QIcon()

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
                        if it.status == "done": it._notified = True
                        self._dl_items.append(it)
            except Exception: pass

    def closeEvent(self, event):
        data = []
        for item in self._dl_items:
            data.append({
                "url": item.url, "title": item.title, "channel": item.channel, "out_dir": item.out_dir, "is_audio": item.is_audio,
                "selected_fid": item.selected_fid, "duration_str": item.duration_str, "status": "paused" if item.status == "active" else item.status,
                "percent": item.percent, "total_size": item.total_size, "downloaded": item.downloaded, "thumb_path": item.thumb_path,
                "thumb_url": item.thumb_url, "fav_path": getattr(item, 'fav_path', None), "start_time": item.start_time, "end_time": item.end_time
            })
        try:
            with open(repo_root / "downloads.json", "w") as f: json.dump(data, f)
        except Exception: pass
        event.accept()

    def _setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(210)
        self.sidebar.setStyleSheet(f"QFrame {{ background-color: {SIDEBAR_DARK}; border: none; }}")
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(20, 30, 20, 20)
        sidebar_layout.setSpacing(12)
        menu_lbl = QLabel("≡")
        menu_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 28px;")
        sidebar_layout.addWidget(menu_lbl)
        sidebar_layout.addSpacing(20)

        self.nav_btns = []
        nav_items = [("Home", "home", 0), ("TrimLoad", "scissors", 1), ("Downloads", "downloads", 2), ("Settings", "settings", 3)]
        for name, icon_name, idx in nav_items:
            btn = QPushButton(f"  {name}")
            btn.setIcon(self._icons.get(icon_name, QIcon()))
            btn.setIconSize(QSize(22, 22))
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet(f"QPushButton {{ text-align: left; padding: 10px; border-radius: 10px; background-color: transparent; color: {TEXT_MUTED}; font-size: 16px; font-weight: 500; }} QPushButton:hover {{ background-color: #222222; }}")
            btn.clicked.connect(lambda checked, i=idx: self._show_tab(i))
            sidebar_layout.addWidget(btn)
            self.nav_btns.append(btn)
            
        sidebar_layout.addStretch()
        main_layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"background-color: {BG_DARK};")
        
        self.tab_home = QWidget()
        self._build_home_tab()
        self.stack.addWidget(self.tab_home)

        self.tab_trim = QWidget()
        trim_layout = QVBoxLayout(self.tab_trim)
        trim_layout.setContentsMargins(0, 0, 0, 0)
        self.trim_widget = TrimLoadTab(self)
        trim_layout.addWidget(self.trim_widget)
        self.stack.addWidget(self.tab_trim)
        
        self.tab_dl = QWidget()
        self._build_downloads_tab()
        self.stack.addWidget(self.tab_dl)

        self.tab_settings = QWidget()
        self.stack.addWidget(self.tab_settings)

        main_layout.addWidget(self.stack)

    def _show_tab(self, idx):
        self.stack.setCurrentIndex(idx)
        for i, btn in enumerate(self.nav_btns):
            btn.setStyleSheet(f"QPushButton {{ text-align: left; padding: 10px; border-radius: 10px; background-color: transparent; color: {TEXT_MAIN if i == idx else TEXT_MUTED}; font-size: 16px; font-weight: 500; }} QPushButton:hover {{ background-color: #222222; }}")
        if idx == 2: self._refresh_dl_list()

    def _build_home_tab(self):
        layout = QVBoxLayout(self.tab_home)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.home_center = QWidget()
        hc_layout = QVBoxLayout(self.home_center)
        hc_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl = QLabel("DYNAMIC")
        self.title_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 68px; font-weight: 300;")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hc_layout.addWidget(self.title_lbl)
        hc_layout.addSpacing(20)
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("Enter link...")
        self.url_entry.setFixedSize(600, 54)
        self.url_entry.setStyleSheet(f"QLineEdit {{ background-color: {INPUT_BG}; color: {TEXT_MAIN}; border-radius: 27px; padding: 0 20px; font-size: 18px; border: none; }}")
        self.url_entry.returnPressed.connect(lambda: self._process_link(self.url_entry.text().strip()))
        hc_layout.addWidget(self.url_entry, alignment=Qt.AlignmentFlag.AlignCenter)
        self.home_slot = QWidget()
        self.home_slot_layout = QVBoxLayout(self.home_slot)
        hc_layout.addWidget(self.home_slot)
        layout.addWidget(self.home_center)

    def _clear_home_slot(self):
        while self.home_slot_layout.count():
            item = self.home_slot_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def _process_link(self, url):
        if not url: return
        self._last_url = url
        self.title_lbl.hide(); self.url_entry.hide()
        self._clear_home_slot()

        lbl = QLabel("Processing link...")
        lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 22px; font-weight: bold;")
        self.home_slot_layout.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        bar = QProgressBar()
        bar.setFixedSize(300, 6); bar.setTextVisible(False); bar.setRange(0, 0)
        bar.setStyleSheet(f"QProgressBar {{ background-color: #333; border: none; }} QProgressBar::chunk {{ background-color: {TEAL_ACCENT}; }}")
        self.home_slot_layout.addWidget(bar, alignment=Qt.AlignmentFlag.AlignCenter)
        
        cancelled = [False]
        def _cancel():
            cancelled[0] = True; self.title_lbl.show(); self.url_entry.show(); self._clear_home_slot()

        btn = QPushButton("Cancel")
        btn.setFixedSize(120, 36)
        btn.setStyleSheet(f"QPushButton {{ border: 1px solid {TEAL_ACCENT}; border-radius: 18px; color: {TEXT_MAIN}; background: transparent; font-weight: bold; }}")
        btn.clicked.connect(_cancel)
        self.home_slot_layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        def _worker():
            res = fetch_formats(url)
            if cancelled[0]: return
            if not res.get("ok"):
                self.signals.home_error.emit(res.get("error", "Unknown error"))
                return

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
                "thumbnail": res.get("thumbnail"), "is_live": res.get("is_live", False)
            }
            
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
            if not cancelled[0]: self.signals.home_result.emit(info, url, thumb_path, fav_path)

        threading.Thread(target=_worker, daemon=True).start()

    def _show_error(self, msg):
        self._clear_home_slot()
        lbl = QLabel(f"Error: {msg}")
        lbl.setWordWrap(True); lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {ERROR_RED}; font-size: 14px; font-weight: bold; padding: 10px;")
        self.home_slot_layout.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        btn = QPushButton("Try Again")
        btn.setFixedSize(120, 38)
        btn.setStyleSheet(f"QPushButton {{ background-color: {TEAL_ACCENT}; color: black; border-radius: 19px; font-weight: bold; }}")
        btn.clicked.connect(lambda: (self.title_lbl.show(), self.url_entry.show(), self._clear_home_slot()))
        self.home_slot_layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def _render_media_card_ui(self, info, url, thumb_path, fav_path):
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
        outer.setStyleSheet(f"QFrame {{ background-color: {CARD_BG}; border: none; border-radius: 12px; }}")
        o_layout = QVBoxLayout(outer); o_layout.setContentsMargins(30, 30, 30, 30); o_layout.setSpacing(25)

        hdr_layout = QHBoxLayout(); hdr_layout.setContentsMargins(0, 0, 0, 0); hdr_layout.setSpacing(30)
        thumb_lbl = QLabel()
        thumb_lbl.setFixedSize(320, 180)
        thumb_lbl.setStyleSheet(f"background-color: {CARD_INNER_BG}; border-radius: 8px;")
        if thumb_path and os.path.exists(thumb_path):
            pix = QPixmap(thumb_path).scaled(320, 180, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            thumb_lbl.setPixmap(get_rounded_pixmap(pix, 8))
        hdr_layout.addWidget(thumb_lbl)

        meta_layout = QVBoxLayout(); meta_layout.setContentsMargins(0, 0, 0, 0); meta_layout.setSpacing(12)
        t_lbl = QLabel(title_text); t_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 30px; font-weight: bold; border: none;"); t_lbl.setWordWrap(True)
        c_lbl = QLabel(channel_text); c_lbl.setStyleSheet(f"color: #DDDDDD; font-size: 18px; border: none;")
        meta_layout.addWidget(t_lbl); meta_layout.addWidget(c_lbl)
        
        dur_site_layout = QHBoxLayout(); dur_site_layout.setSpacing(10)
        d_lbl = QLabel(dur_str); d_lbl.setStyleSheet(f"color: {'#FF5555' if is_live else TEXT_MAIN}; font-size: 16px; font-weight: {'bold' if is_live else 'normal'}; border: none;")
        dot_lbl = QLabel(" • "); dot_lbl.setStyleSheet(f"color: #DDDDDD; font-size: 16px; border: none;")
        dur_site_layout.addWidget(d_lbl); dur_site_layout.addWidget(dot_lbl)
        
        if fav_path and os.path.exists(fav_path):
            fav_lbl = QLabel(); fav_lbl.setStyleSheet("border: none; background: transparent;")
            fav_lbl.setPixmap(QPixmap(fav_path).scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            dur_site_layout.addWidget(fav_lbl)
        else:
            site_icon = QLabel("▶" if "Youtube" in site else "🌐")
            site_icon.setStyleSheet(f"color: {'#FF0000' if 'Youtube' in site else TEAL_ACCENT}; font-size: 18px; border: none;")
            dur_site_layout.addWidget(site_icon)
            
        site_lbl = QLabel(site); site_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        site_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 16px; font-weight: bold; border: none;")
        site_lbl.mousePressEvent = lambda e: self._open_qurl(QUrl(url))
        dur_site_layout.addWidget(site_lbl); dur_site_layout.addStretch()
        meta_layout.addLayout(dur_site_layout); meta_layout.addStretch()
        hdr_layout.addLayout(meta_layout); hdr_layout.addStretch(); o_layout.addLayout(hdr_layout)

        seg_frame = QFrame(); seg_frame.setStyleSheet("QFrame { background-color: #222222; border-radius: 12px; }")
        seg_frame.setFixedHeight(48); seg_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        seg_layout = QHBoxLayout(seg_frame); seg_layout.setContentsMargins(4, 4, 4, 4); seg_layout.setSpacing(4)
        bv = QPushButton("Video"); bv.setFixedHeight(40); bv.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ba = QPushButton("Audio"); ba.setFixedHeight(40); ba.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        
        def update_btn_styles(mode):
            act = f"QPushButton {{ background-color: #333333; color: {TEAL_ACCENT}; font-size: 18px; font-weight: bold; border-radius: 8px; border: none; }}"
            inact = f"QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; font-size: 18px; font-weight: bold; border-radius: 8px; border: none; }} QPushButton:hover {{ background-color: #2A2A2A; }}"
            if mode == "Video": bv.setStyleSheet(act); ba.setStyleSheet(inact)
            else: bv.setStyleSheet(inact); ba.setStyleSheet(act)
        
        if audio_only:
            bv.setDisabled(True); bv.setStyleSheet("QPushButton { background-color: transparent; color: #444; font-size: 18px; font-weight: bold; border-radius: 8px; border: none; }")
            ba.setStyleSheet(f"QPushButton {{ background-color: #333333; color: {TEAL_ACCENT}; font-size: 18px; font-weight: bold; border-radius: 8px; border: none; }}")
        else: update_btn_styles("Video")
        seg_layout.addWidget(bv); seg_layout.addWidget(ba); o_layout.addWidget(seg_frame)

        qual_layout = QVBoxLayout(); qual_layout.setSpacing(8)
        ql = QLabel("Quality" if not audio_only else "Bitrate"); ql.setStyleSheet("color: #DDDDDD; font-size: 16px; border: none;")
        qm = QComboBox(); qm.setFixedHeight(48); qm.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        qm.setStyleSheet(f"QComboBox {{ background-color: #181818; color: {TEAL_ACCENT}; font-size: 15px; border-radius: 8px; padding: 10px 15px; border: 1px solid #333333; }} QComboBox:focus {{ border: 2px solid #0078D4; }} QComboBox::drop-down {{ border: none; width: 30px; }} QComboBox QAbstractItemView {{ background-color: #202020; color: {TEXT_MAIN}; selection-background-color: #383838; border: 1px solid #333333; border-radius: 6px; outline: none; }}")
        qm.addItems(audio_q if audio_only else (video_q or ["Best quality"]))
        qual_layout.addWidget(ql); qual_layout.addWidget(qm); o_layout.addLayout(qual_layout)

        btn_layout = QHBoxLayout(); btn_layout.setSpacing(20)
        btn_cancel = QPushButton("Cancel"); btn_cancel.setFixedHeight(48); btn_cancel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed); btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_cancel.setStyleSheet(f"QPushButton {{ background-color: transparent; border: 1px solid {TEAL_ACCENT}; color: {TEXT_MAIN}; font-size: 16px; font-weight: bold; border-radius: 24px; }} QPushButton:hover {{ background-color: #222222; }}")
        btn_dl = QPushButton("Download"); btn_dl.setFixedHeight(48); btn_dl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed); btn_dl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        if is_live: btn_dl.setStyleSheet("QPushButton { background-color: #222222; color: #666666; font-size: 16px; font-weight: bold; border-radius: 24px; border: 1px solid #333333; } QPushButton:hover { background-color: #262626; }")
        else: btn_dl.setStyleSheet(f"QPushButton {{ background-color: {TEAL_ACCENT}; color: #000000; font-size: 16px; font-weight: bold; border-radius: 24px; border: none; }} QPushButton:hover {{ background-color: #00A892; }}")
        btn_layout.addWidget(btn_cancel); btn_layout.addWidget(btn_dl); o_layout.addLayout(btn_layout)

        current_mode = ["Audio" if audio_only else "Video"]
        def set_mode(mode):
            if audio_only and mode == "Video": return
            current_mode[0] = mode; qm.clear()
            if mode == "Video": update_btn_styles("Video"); ql.setText("Quality"); qm.addItems(video_q or ["Best quality"])
            else: update_btn_styles("Audio"); ql.setText("Bitrate"); qm.addItems(audio_q or ["Best Audio"])
        bv.clicked.connect(lambda: set_mode("Video"))
        ba.clicked.connect(lambda: set_mode("Audio"))

        def _cancel(): self.title_lbl.show(); self.url_entry.show(); self.url_entry.clear(); self._clear_home_slot()
        btn_cancel.clicked.connect(_cancel)
        
        def _on_download():
            if is_live: jiggle_widget(btn_dl); self.toast_mgr.show_toast("Can't download live streams"); return
            item = DownloadItem(
                url=url, title=title_text, channel=channel_text, thumb_path=thumb_path, 
                out_dir=str(repo_root / "downloads"), is_audio=(current_mode[0] == "Audio"), 
                selected_fid=format_map.get(qm.currentText()), duration_str=dur_str, 
                site_name=site, thumb_url=thumb_url, fav_path=fav_path
            )
            self._dl_items.append(item)
            self._start_download(item); self._show_tab(2); _cancel()
            
        btn_dl.clicked.connect(_on_download)
        self.home_slot_layout.addWidget(outer, alignment=Qt.AlignmentFlag.AlignTop)

    def _build_downloads_tab(self):
        main_layout = QVBoxLayout(self.tab_dl); main_layout.setContentsMargins(0, 0, 0, 0); main_layout.setSpacing(0)
        top_container = QWidget(); top_layout = QVBoxLayout(top_container); top_layout.setContentsMargins(32, 28, 32, 12); top_layout.setSpacing(16)
        lbl = QLabel("Downloads"); lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 32px; font-weight: bold;"); top_layout.addWidget(lbl)
        filters = ["All", "Active", "Paused", "Done", "Failed"]
        self.filter_tab_bar = FilterTabBar(filters); self.filter_tab_bar.filter_changed.connect(self._on_filter_changed)
        top_layout.addWidget(self.filter_tab_bar, alignment=Qt.AlignmentFlag.AlignLeft)
        main_layout.addWidget(top_container)
        self.scroll = ModernScrollArea(); self.scroll_content = QWidget(); self.scroll_content.setStyleSheet("background-color: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content); self.scroll_layout.setContentsMargins(32, 10, 32, 16); self.scroll_layout.setSpacing(16); self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content); main_layout.addWidget(self.scroll, 1)

    def _on_filter_changed(self, f_name):
        if self._dl_tab_filter == f_name: return
        self._dl_tab_filter = f_name; self._refresh_dl_list()

    def _refresh_dl_list(self):
        counts = {"All": len(self._dl_items), "Active": sum(1 for d in self._dl_items if d.status=="active"), "Paused": sum(1 for d in self._dl_items if d.status=="paused"), "Done": sum(1 for d in self._dl_items if d.status=="done"), "Failed": sum(1 for d in self._dl_items if d.status=="failed")}
        self.filter_tab_bar.update_counts(counts)
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        filtered = [d for d in reversed(self._dl_items) if self._dl_tab_filter=="All" or d.status==self._dl_tab_filter.lower()]
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if len(filtered) >= 3 else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for item in filtered: self.scroll_layout.addWidget(self._build_dl_card_widget(item))
        if not filtered:
            empty = QLabel("No downloads here")
            empty.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 15px;"); empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.scroll_layout.addWidget(empty)
        self.scroll_layout.addStretch()

    def _build_dl_card_widget(self, item: DownloadItem):
        card = QFrame(); card.setFixedHeight(180)
        card.setStyleSheet(f"QFrame {{ background-color: {CARD_BG}; border: 1px solid #2A2A2A; border-radius: 14px; }}")
        main_h = QHBoxLayout(card); main_h.setContentsMargins(16, 16, 16, 16); main_h.setSpacing(20)
        thumb_lbl = QLabel(); thumb_lbl.setFixedSize(240, 135)
        thumb_lbl.setStyleSheet(f"background-color: {CARD_INNER_BG}; border-radius: 8px; border: none;")
        if item.thumb_path and os.path.exists(item.thumb_path):
            pix = QPixmap(item.thumb_path).scaled(240, 135, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            thumb_lbl.setPixmap(get_rounded_pixmap(pix, 8))
        main_h.addWidget(thumb_lbl)
        right_v = QVBoxLayout(); right_v.setContentsMargins(0, 0, 0, 0); right_v.setSpacing(4)
        title_h = QHBoxLayout()
        title_lbl = QLabel(item.title); title_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 20px; font-weight: bold; border: none;"); title_lbl.setWordWrap(True)
        title_h.addWidget(title_lbl, 1)
        trash_btn = QPushButton(); trash_btn.setIcon(self._icons.get("trash", QIcon())); trash_btn.setIconSize(QSize(22, 22)); trash_btn.setFixedSize(32, 32); trash_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        trash_btn.setStyleSheet(f"QPushButton {{ background: transparent; border: none; }} QPushButton:hover {{ background: #2A1A1A; border-radius: 8px; }}")
        
        def _trash_click():
            if item._trash_ready:
                if item in self._dl_items: self._dl_items.remove(item)
                item._cancelled = True; self._refresh_dl_list()
            else:
                item._trash_ready = True; trash_btn.setIcon(self._icons.get("trash red", QIcon()))
                QTimer.singleShot(3000, lambda: _reset_trash())
        def _reset_trash():
            item._trash_ready = False
            try: trash_btn.setIcon(self._icons.get("trash", QIcon()))
            except RuntimeError: pass
        trash_btn.clicked.connect(_trash_click); title_h.addWidget(trash_btn); right_v.addLayout(title_h)
        meta_h = QHBoxLayout()
        ch_lbl = QLabel(item.channel); ch_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 14px; border: none;"); meta_h.addWidget(ch_lbl)
        dur_lbl = QLabel(f" •  {item.duration_str}"); dur_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 14px; border: none;"); meta_h.addWidget(dur_lbl)
        dot_lbl = QLabel(" • "); dot_lbl.setStyleSheet(f"color: #DDDDDD; font-size: 14px; border: none;"); meta_h.addWidget(dot_lbl)
        if item.fav_path and os.path.exists(item.fav_path):
            fav_lbl = QLabel(); fav_lbl.setStyleSheet("border: none; background: transparent;")
            fav_lbl.setPixmap(QPixmap(item.fav_path).scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            meta_h.addWidget(fav_lbl)
        site_lbl = QLabel(item.site_name); site_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor)); site_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 14px; border: none;"); site_lbl.mousePressEvent = lambda e, u=item.url: self._open_qurl(QUrl(u)); meta_h.addWidget(site_lbl)
        meta_h.addStretch(); right_v.addLayout(meta_h); right_v.addStretch()

        if item.status == "failed":
            err_frame = QFrame(); err_frame.setStyleSheet(f"background-color: {ERROR_BG}; border: 1px solid #4A2020; border-radius: 8px;")
            err_layout = QVBoxLayout(err_frame); err_layout.setContentsMargins(10, 6, 10, 6)
            clean_err = item.error_msg.strip().replace("\n", " ")
            if len(clean_err) > 130: clean_err = clean_err[:130] + "..."
            err_lbl = QLabel(clean_err); err_lbl.setWordWrap(True); err_lbl.setStyleSheet("color: #FF8888; font-size: 11px; font-family: sans-serif; border: none;"); err_layout.addWidget(err_lbl); right_v.addWidget(err_frame)
            retry_h = QHBoxLayout(); retry_h.setContentsMargins(0, 4, 0, 0)
            retry_btn = QPushButton(" Retry"); retry_btn.setIcon(self._icons.get("retry", QIcon())); retry_btn.setIconSize(QSize(18, 18)); retry_btn.setFixedSize(90, 32); retry_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor)); retry_btn.setStyleSheet(f"QPushButton {{ background-color: #2A2A2A; color: {TEXT_MAIN}; font-weight: bold; border-radius: 16px; border: none; }} QPushButton:hover {{ background-color: #333333; }}")
            def _retry(): item.status, item.error_msg, item.percent = "active", "", 0.0; self._start_download(item); self._refresh_single_card(item)
            retry_btn.clicked.connect(_retry); retry_h.addWidget(retry_btn); retry_h.addStretch(); right_v.addLayout(retry_h)
        else:
            prog_h = QHBoxLayout(); prog_h.setSpacing(12)
            ctrl_btn = QPushButton(); ctrl_btn.setFixedSize(36, 36); ctrl_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor)); ctrl_btn.setStyleSheet(f"QPushButton {{ background: transparent; border: none; }} QPushButton:hover {{ background: #252525; border-radius: 8px; }}")
            if item.status == "active":
                ctrl_btn.setIcon(self._icons.get("pause", QIcon())); ctrl_btn.setIconSize(QSize(24, 24))
                def _pause():
                    if item.duration_str == "LIVE": self.toast_mgr.show_toast("Download cannot be paused"); return
                    item.status = "paused"; item._cancelled = True; item.speed, item.eta = "--", "--"; self._refresh_single_card(item)
                ctrl_btn.clicked.connect(_pause)
            elif item.status == "paused":
                ctrl_btn.setIcon(self._icons.get("play", QIcon())); ctrl_btn.setIconSize(QSize(24, 24))
                def _resume(): item.status = "active"; item._cancelled = False; item.speed, item.eta = "Resuming...", "--"; self._start_download(item); self._refresh_single_card(item)
                ctrl_btn.clicked.connect(_resume)
            elif item.status == "done":
                ctrl_btn.setIcon(self._icons.get("check", QIcon())); ctrl_btn.setIconSize(QSize(24, 24))
            prog_h.addWidget(ctrl_btn)
            bar_v = QVBoxLayout(); bar_v.setSpacing(6)
            pb = QProgressBar(); pb.setFixedHeight(4); pb.setTextVisible(False); pb.setMaximum(1000); pb.setValue(int(item.percent * 1000))
            pb.setStyleSheet(f"QProgressBar {{ background-color: #333333; border: none; border-radius: 2px; }} QProgressBar::chunk {{ background-color: {TEAL_ACCENT}; border-radius: 2px; }}")
            bar_v.addWidget(pb); card.prog_bar = pb
            stats_h = QHBoxLayout()
            dl_str, tot_str, pct_val, spd, eta = item.downloaded or "0MB", item.total_size or "Unknown", item.percent * 100, item.speed or "0Mb/s", item.eta or "--:--"
            stat_txt = "Download complete" if item.status == "done" else (f"Paused - {int(item.percent*100)}%" if item.status == "paused" else f"{dl_str} / {tot_str} ({pct_val:.1f}%)   {spd}   ETA: {eta}")
            stat_lbl = QLabel(stat_txt); stat_lbl.setStyleSheet(f"color: {TEAL_ACCENT if item.status == 'done' else (TEXT_MAIN if item.status == 'active' else TEXT_MUTED)}; font-size: 12px; font-weight: {'bold' if item.status == 'done' else 'normal'}; border: none;"); stats_h.addWidget(stat_lbl); card.stat_lbl = stat_lbl
            path_lbl = QLabel(item.out_dir); path_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor)); path_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; border: none;"); path_lbl.mousePressEvent = lambda e: self._open_qurl(QUrl.fromLocalFile(item.out_dir)); stats_h.addWidget(path_lbl, alignment=Qt.AlignmentFlag.AlignRight)
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
                    item.status, item.percent = "done", 1.0; self.signals.refresh_card.emit(item)
                elif t == "error":
                    item.status, item.error_msg = "failed", evt.get("message", "Unknown error"); self.signals.refresh_card.emit(item)
        item._thread = threading.Thread(target=_worker, daemon=True); item._thread.start()

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
            self.tray_icon.showMessage("Download finished", raw_title, QSystemTrayIcon.MessageIcon.Information, 5000)

    def _refresh_single_card(self, item):
        try:
            if item.id in self._dl_cards:
                card = self._dl_cards.pop(item.id); card.deleteLater()
        except RuntimeError: pass
        self._refresh_dl_list()
        if item.status == "done" and not getattr(item, "_notified", False):
            item._notified = True; self._show_download_notification(item)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DynamicPC()
    window.show()
    sys.exit(app.exec())