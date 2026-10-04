import sys
import os
import math
import threading
import urllib.request
import webbrowser
import hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QLineEdit, QComboBox, 
                             QProgressBar, QFrame, QGraphicsDropShadowEffect,
                             QGridLayout, QSpacerItem, QSizePolicy,
                             QGraphicsOpacityEffect, QPlainTextEdit)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import (Qt, QUrl, Signal, QPointF, QRectF, QLineF, QObject, QTimer, 
                          QPropertyAnimation, QSequentialAnimationGroup, QParallelAnimationGroup, 
                          QPoint, QEasingCurve, QEvent, QRect, QElapsedTimer, QVariantAnimation)
from PySide6.QtGui import (QFont, QPainter, QColor, QPen, QCursor, QPixmap, QIcon, QDesktopServices,
                         QPainterPath, QBrush, QLinearGradient, QRadialGradient, QFontMetrics)

try:
    from PySide6.QtSvgWidgets import QSvgWidget
    from PySide6.QtSvg import QSvgRenderer
    HAS_SVG = True
except ImportError:
    HAS_SVG = False

try:
    import yt_dlp
    import builtins
    # Inject yt_dlp globally so the DownloadWorker in main.py can access it for range functions
    builtins.yt_dlp = yt_dlp
except ImportError:
    yt_dlp = None

# Cache Directories
repo_root = Path(__file__).resolve().parent
cache_dir = repo_root / "cache"
favicon_dir = cache_dir / "favicons"
thumb_dir = cache_dir / "thumbnails"
favicon_dir.mkdir(parents=True, exist_ok=True)
thumb_dir.mkdir(parents=True, exist_ok=True)

# UI Theme Constants
TEAL_ACCENT   = "#00BFA5"
BG_DARK       = "#121212"
CARD_BG       = "#1E1E1E"
INPUT_BG      = "#333333"
TEXT_MAIN     = "#FFFFFF"
TEXT_MUTED    = "#8A8A8A"
ERROR_RED     = "#FF5555"

# Global Headers
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

_AUDIO_ONLY_DOMAINS = (
    "music.youtube.com", "soundcloud.com", "spotify.com",
    "tidal.com", "deezer.com", "music.apple.com", "apple.com",
    "bandcamp.com", "audiomack.com", "reverbnation.com",
    "mixcloud.com",
)


def _is_audio_only_url(url):
    return any(domain in url.lower() for domain in _AUDIO_ONLY_DOMAINS)


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
        host = host.replace("www.", "").replace("m.", "").replace("music.", "")
        return host.split(".")[0].title()
    except Exception:
        return "YouTube"


def _fetch_favicon_sync(url):
    try:
        parsed = urlparse(url)
        host = parsed.netloc or "unknown"
        filepath = favicon_dir / f"{host}.png"
        if filepath.exists(): 
            return str(filepath)
            
        furl = f"https://icons.duckduckgo.com/ip3/{parsed.netloc}.ico"
        req = urllib.request.Request(furl, headers={"User-Agent": DEFAULT_UA})
        with urllib.request.urlopen(req, timeout=3) as r: 
            data = r.read()
        with open(filepath, 'wb') as f: 
            f.write(data)
        return str(filepath)
    except Exception:
        return None


def format_time_str(seconds):
    seconds = max(0, float(seconds))
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_time_str(time_str):
    try:
        parts = [float(p) for p in time_str.split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 1:
            return parts[0]
    except Exception:
        pass
    return -1.0


def jiggle_widget(widget):
    if hasattr(widget, "_jiggle_anim") and widget._jiggle_anim.state() == QSequentialAnimationGroup.State.Running:
        return
    orig_pos = widget.pos()
    group = QSequentialAnimationGroup(widget)
    for offset in [-10, 10, -7, 7, -4, 4, -2, 2, 0]:
        anim = QPropertyAnimation(widget, b"pos", widget)
        anim.setDuration(35)
        anim.setStartValue(widget.pos())
        anim.setEndValue(orig_pos + QPoint(offset, 0))
        group.addAnimation(anim)
    widget._jiggle_anim = group
    group.start()


def select_preview_stream(info, url):
    if not isinstance(info, dict):
        return None, None, False

    formats = info.get('formats') or []
    is_audio_platform = _is_audio_only_url(url)

    if not formats:
        stream_url = info.get('url')
        if stream_url:
            vcodec = info.get('vcodec')
            is_audio = is_audio_platform or (vcodec == 'none')
            return stream_url, 0, is_audio
        return None, None, False

    valid = [f for f in formats if f.get('url') and str(f.get('url')).startswith('http')]
    vids = [f for f in valid if f.get('vcodec') not in ('none', None)]

    all_audio_only = bool(valid) and all(f.get('vcodec') == 'none' for f in valid)
    is_pure_audio = is_audio_platform or all_audio_only

    if is_pure_audio:
        auds = [f for f in valid if f.get('acodec') not in ('none', None)]
        if auds:
            best_aud = sorted(auds, key=lambda x: x.get('abr') or 0, reverse=True)[0]
            return best_aud.get('url'), 0, True
        fallback = [f for f in valid]
        if fallback:
            return fallback[0].get('url'), 0, True
        return None, None, True

    def vid_score(f):
        s = 0
        h = f.get('height') or 9999
        vcodec = (f.get('vcodec') or '').lower()
        ext = (f.get('ext') or '').lower()
        
        if 'av1' in vcodec or 'av01' in vcodec:
            s -= 100000
        if 'vp9' in vcodec or 'vp09' in vcodec:
            s -= 20000

        if 'avc' in vcodec or 'h264' in vcodec: 
            s += 50000
        if ext == 'mp4': 
            s += 20000

        if 0 < h <= 720:
            s += (10000 - h)
            
        return s

    if vids:
        best_vid = max(vids, key=vid_score)
    elif valid:
        best_vid = valid[0]
    else:
        return None, None, False

    return best_vid.get('url'), best_vid.get('height') or 0, False


class SvgSpinnerWidget(QWidget):
    def __init__(self, svg_path, parent=None):
        super().__init__(parent)
        self.setFixedSize(48, 48)
        self.renderer = QSvgRenderer(svg_path) if HAS_SVG and os.path.exists(svg_path) else None
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate)
        self.timer.start(16)

    def rotate(self):
        self.angle = (self.angle + 8) % 360
        self.update()

    def paintEvent(self, event):
        if not self.renderer:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self.angle)
        painter.translate(-self.width() / 2, -self.height() / 2)
        self.renderer.render(painter, QRectF(0, 0, self.width(), self.height()))


class LoadingOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 160); border-radius: 12px")
        
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
        svg_path = os.path.join(icon_dir, "line-md--loading-loop.svg")
        if not os.path.exists(svg_path):
            svg_path = os.path.join(icon_dir, "line-md--loading-loop")

        if HAS_SVG and os.path.exists(svg_path):
            self.spinner = SvgSpinnerWidget(svg_path, self)
            layout.addWidget(self.spinner, alignment=Qt.AlignmentFlag.AlignCenter)
        else:
            self.spinner = None
            lbl = QLabel("Loading...")
            lbl.setStyleSheet(f"color: {TEAL_ACCENT}; font-size: 16px; font-weight: bold;")
            layout.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.parent():
            self.setGeometry(0, 0, self.parent().width(), self.parent().height())

    def start(self):
        if self.parent():
            self.setGeometry(0, 0, self.parent().width(), self.parent().height())
        self.show()
        self.raise_()

    def stop(self):
        self.hide()


class StandaloneToastWidget(QFrame):
    dismissed = Signal(object)

    def __init__(self, message, parent=None, icon_pixmap=None, rich_text=False):
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
        
        if icon_pixmap is not None and not icon_pixmap.isNull():
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(22, 22)
            icon_lbl.setPixmap(icon_pixmap)
            icon_lbl.setStyleSheet("background: transparent; border: none;")
        else:
            icon_lbl = QLabel("✕")
            icon_lbl.setStyleSheet("color: #FF5555; font-size: 15px; font-weight: bold; border: none; background: transparent;")
        layout.addWidget(icon_lbl)
        
        msg_lbl = QLabel(message)
        if rich_text:
            msg_lbl.setTextFormat(Qt.TextFormat.RichText)
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


class LocalToastManager(QObject):
    def __init__(self, parent_window):
        super().__init__(parent_window)
        self.win = parent_window
        self.toasts = []

    def show_toast(self, message, icon_pixmap=None, rich_text=False):
        toast = StandaloneToastWidget(message, parent=self.win, icon_pixmap=icon_pixmap, rich_text=rich_text)
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


class AspectRatioLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pix = None
        self.setStyleSheet("background-color: #000000;")
        
    def setPixmap(self, p):
        self.pix = p
        super().setPixmap(self.scaled_pixmap())
        
    def resizeEvent(self, event):
        if self.pix:
            super().setPixmap(self.scaled_pixmap())
        super().resizeEvent(event)
        
    def scaled_pixmap(self):
        if not self.pix: 
            return QPixmap()
        return self.pix.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


class TrimRangeSlider(QWidget):
    positionChanged = Signal(float)
    seekRequested = Signal(float)
    trimRangeChanged = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.duration = 1.0
        self.start_time = 0.0
        self.end_time = 1.0
        self.current_time = 0.0
        self._dragging = None
        self.setMouseTracking(True)

    def set_duration(self, dur):
        self.duration = max(dur, 1.0)
        self.start_time = 0.0
        self.end_time = self.duration
        self.current_time = 0.0
        self.update()

    def set_position(self, pos):
        self.current_time = max(0.0, min(pos, self.duration))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w, h = self.width(), self.height()
        cy = h - 12
        margin = 14
        usable_w = w - (margin * 2)
        if usable_w <= 0 or self.duration <= 0:
            return

        painter.setPen(QPen(QColor("#3A3A3A"), 4))
        painter.drawLine(QLineF(margin, cy, w - margin, cy))

        start_x = margin + (self.start_time / self.duration) * usable_w
        end_x = margin + (self.end_time / self.duration) * usable_w

        painter.setPen(QPen(QColor(TEAL_ACCENT), 4))
        painter.drawLine(QLineF(start_x, cy, end_x, cy))

        handle_w = 12
        handle_h = 22

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(TEAL_ACCENT))
        start_rect = QRectF(start_x - handle_w / 2, cy - handle_h / 2, handle_w, handle_h)
        painter.drawRoundedRect(start_rect, handle_w / 2, handle_w / 2)
        
        painter.setPen(QPen(QColor("#121212"), 2))
        painter.drawLine(QLineF(start_x, cy - 4, start_x, cy + 4))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(TEAL_ACCENT))
        end_rect = QRectF(end_x - handle_w / 2, cy - handle_h / 2, handle_w, handle_h)
        painter.drawRoundedRect(end_rect, handle_w / 2, handle_w / 2)
        
        painter.setPen(QPen(QColor("#121212"), 2))
        painter.drawLine(QLineF(end_x, cy - 4, end_x, cy + 4))

        pos_x = margin + (self.current_time / self.duration) * usable_w
        top_y = cy - 22

        painter.setPen(QPen(QColor(TEAL_ACCENT), 2))
        painter.drawLine(QLineF(pos_x, cy, pos_x, top_y))

        glow_color = QColor(TEAL_ACCENT)
        glow_color.setAlpha(70)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow_color)
        painter.drawEllipse(QPointF(pos_x, top_y), 8, 8)

        painter.setBrush(QColor(TEAL_ACCENT))
        painter.drawEllipse(QPointF(pos_x, top_y), 4.5, 4.5)

    def mousePressEvent(self, event):
        x = event.position().x()
        margin = 14
        usable_w = self.width() - (margin * 2)
        if usable_w <= 0: return

        click_time = max(0.0, min(((x - margin) / usable_w) * self.duration, self.duration))
        start_x = margin + (self.start_time / self.duration) * usable_w
        end_x = margin + (self.end_time / self.duration) * usable_w
        pos_x = margin + (self.current_time / self.duration) * usable_w

        if abs(x - start_x) <= 12:
            self._dragging = 'start'
        elif abs(x - end_x) <= 12:
            self._dragging = 'end'
        elif abs(x - pos_x) <= 12:
            self._dragging = 'position'
        else:
            self._dragging = 'position'
            self.current_time = click_time
            self.positionChanged.emit(self.current_time)
        self.update()

    def mouseMoveEvent(self, event):
        margin = 14
        usable_w = self.width() - (margin * 2)
        if usable_w <= 0: return

        x = event.position().x()
        start_x = margin + (self.start_time / self.duration) * usable_w
        end_x = margin + (self.end_time / self.duration) * usable_w
        pos_x = margin + (self.current_time / self.duration) * usable_w

        if self._dragging:
            t = max(0.0, min(((x - margin) / usable_w) * self.duration, self.duration))
            if self._dragging == 'start':
                self.start_time = min(t, self.end_time - 0.5)
                self.current_time = self.start_time
                self.positionChanged.emit(self.current_time)
                self.trimRangeChanged.emit(self.start_time, self.end_time)
            elif self._dragging == 'end':
                self.end_time = max(t, self.start_time + 0.5)
                self.current_time = self.end_time
                self.positionChanged.emit(self.current_time)
                self.trimRangeChanged.emit(self.start_time, self.end_time)
            elif self._dragging == 'position':
                self.current_time = t
                self.positionChanged.emit(self.current_time)
            self.update()
        else:
            if abs(x - start_x) <= 12 or abs(x - end_x) <= 12 or abs(x - pos_x) <= 12:
                self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self.seekRequested.emit(self.current_time)
        self._dragging = None


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
        self._btn_cancel.setStyleSheet(self._pill_style(filled=False))
        self._btn_cancel.clicked.connect(self.cancel_requested.emit)
        self._cancel_fx = self._fade_with_overlay(self._btn_cancel)

        self._btn_retry = QPushButton("Try Again", self)
        self._btn_retry.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_retry.setStyleSheet(self._pill_style(filled=True))
        self._btn_retry.clicked.connect(self.retry_requested.emit)
        self._retry_fx = self._fade_with_overlay(self._btn_retry)

        self._btn_log = QPushButton("Error Log", self)
        self._btn_log.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_log.setStyleSheet(self._pill_style(filled=False))
        self._btn_log.clicked.connect(self._toggle_log)
        self._log_btn_fx = self._fade_with_overlay(self._btn_log)

        for b in (self._btn_cancel, self._btn_retry, self._btn_log):
            b.setFixedSize(120, 34); b.hide()

    def _build_log_panel(self):
        self._log_panel = QFrame(self)
        self._log_panel.setStyleSheet("QFrame { background-color: #161616; border: 1px solid #2E2E2E; border-radius: 10px; }")
        lay = QVBoxLayout(self._log_panel)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("Error details")
        title.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
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
        self._btn_copy.setStyleSheet(self._copy_style_normal)
        self._btn_copy.clicked.connect(self._copy_error_text)
        header.addWidget(self._btn_copy)
        lay.addLayout(header)
        self._log_text = QPlainTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setStyleSheet(f"QPlainTextEdit {{ background: transparent; border: none; color: {ERROR_RED}; "
                                       f"font-family: Consolas, monospace; font-size: 11px; }}")
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
        self._btn_copy.setStyleSheet(self._copy_style_done)
        QTimer.singleShot(1500, self._reset_copy_button)

    def _reset_copy_button(self):
        self._btn_copy.setText("Copy")
        self._btn_copy.setStyleSheet(self._copy_style_normal)

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
        p.setFont(hf); p.setPen(QColor(TEXT_MAIN))
        p.drawText(QRectF(cx - 390, cy - 96 + dy, 780, 56), Qt.AlignmentFlag.AlignCenter, headline)
        if not failed and idx < len(self.STEPS) - 1:
            tw = QFontMetrics(hf).horizontalAdvance(headline)
            n = int(now / 380) % 4
            p.setPen(QColor(TEAL_ACCENT))
            p.drawText(QRectF(cx + tw / 2 + 4, cy - 96 + dy, 60, 56), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "." * n)
        p.setFont(self._font(14)); p.setPen(QColor(ERROR_RED if failed else TEXT_MUTED))
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
        accent = self._lerp_color(QColor(TEAL_ACCENT), QColor(ERROR_RED), self._fail_color_t)

        # background + soft ambient glow
        p.fillRect(event.rect(), QColor(BG_DARK))
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
        p.setPen(QPen(QColor("#2C2C2C"), 1)); p.setBrush(QColor(255, 255, 255, 7))
        p.drawRoundedRect(chip, 14, 14)
        pulse = 0.5 + 0.5 * math.sin(now / 260.0)
        dot = QColor(accent); dot.setAlpha(int(110 + 145 * pulse))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(dot)
        p.drawEllipse(QPointF(chip.left() + 16, chip.center().y()), 3, 3)
        p.setFont(cf); p.setPen(QColor(TEXT_MUTED))
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
            p.setPen(QPen(QColor("#2A2A2A"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(xa, ty), QPointF(xb, ty))
            if self._done_at[i] is not None:
                fp = self._ease.valueForProgress(max(0.0, min(1.0, (now - self._done_at[i]) / 320.0)))
                p.setPen(QPen(QColor(TEAL_ACCENT), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(xa, ty), QPointF(xa + (xb - xa) * fp, ty))
        lf = self._font(11, None, 0.6)
        for i in range(n):
            x = x0 + i * spacing
            done = self._done_at[i] is not None
            is_active = (i == self._active) and not done
            if self._failed and i == self._active:
                p.setPen(QPen(QColor(ERROR_RED), 2)); p.setBrush(QColor(ERROR_RED))
                p.drawEllipse(QPointF(x, ty), r, r)
                p.setPen(QPen(QColor("#0B0B0B"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(x - 3, ty - 3), QPointF(x + 3, ty + 3)); p.drawLine(QPointF(x + 3, ty - 3), QPointF(x - 3, ty + 3))
                lab = QColor(ERROR_RED)
            elif done:
                e = self._ease.valueForProgress(max(0.0, min(1.0, (now - self._done_at[i]) / 280.0)))
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(TEAL_ACCENT))
                p.drawEllipse(QPointF(x, ty), r * (0.55 + 0.45 * e), r * (0.55 + 0.45 * e))
                ck = QColor("#0B0B0B"); ck.setAlphaF(e)
                p.setPen(QPen(ck, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)); p.setBrush(Qt.BrushStyle.NoBrush)
                path = QPainterPath(); path.moveTo(x - 3.6, ty + 0.4); path.lineTo(x - 1.0, ty + 3.0); path.lineTo(x + 3.8, ty - 2.8)
                p.drawPath(path)
                lab = QColor("#9A9A9A")
            elif is_active:
                ph = (now % 1300) / 1300.0
                halo = QColor(TEAL_ACCENT); halo.setAlpha(int(95 * (1 - ph)))
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(halo)
                p.drawEllipse(QPointF(x, ty), r + 2 + 9 * ph, r + 2 + 9 * ph)
                p.setPen(QPen(QColor(TEAL_ACCENT), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(x, ty), r, r)
                br = 3.2 + 0.8 * math.sin(now / 200.0)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(TEAL_ACCENT))
                p.drawEllipse(QPointF(x, ty), br, br)
                lab = QColor(TEXT_MAIN)
            else:
                p.setPen(QPen(QColor("#3A3A3A"), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(x, ty), r - 1, r - 1)
                lab = QColor("#555555")
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

class TrimSignals(QObject):
    loaded = Signal(dict)
    error = Signal(int, str)
    phase = Signal(int, str)


class TrimLoadTab(QWidget):
    def __init__(self, main_win=None):
        super().__init__()
        self.main_win = main_win
        self.signals = TrimSignals()
        self.signals.loaded.connect(self._on_metadata_loaded)
        self.signals.error.connect(self._on_scrape_error)
        self.signals.phase.connect(self._on_trim_phase)
        
        self.current_info = {}
        self.duration = 1.0
        self.start_trim = 0.0
        self.end_trim = 1.0
        self.is_audio_mode = False
        self.preview_failed = False 

        self._setup_player()
        self._build_ui()

        self._proc_run_id, self._proc_cancel_flag = 0, None
        self.proc_overlay = ProcessingOverlay(self)
        self.proc_overlay.cancel_requested.connect(self._on_processing_cancel)
        self.proc_overlay.retry_requested.connect(self._on_processing_cancel)

        self._show_initial_view()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.setGeometry(0, 0, self.video_frame.width(), self.video_frame.height())
            
        if hasattr(self, "preview_error_lbl") and self.preview_error_lbl.isVisible():
            self.preview_error_lbl.adjustSize()
            self.preview_error_lbl.move(
                (self.video_frame.width() - self.preview_error_lbl.width()) // 2,
                (self.video_frame.height() - self.preview_error_lbl.height()) // 2
            )

    def _setup_player(self):
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(1.0) 
        
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        
        self.player.positionChanged.connect(self._on_player_position_changed)
        self.player.durationChanged.connect(self._on_player_duration_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.errorOccurred.connect(self._on_player_error)

    def _on_player_error(self, error, error_string):
        if error != QMediaPlayer.Error.NoError:
            self._show_toast_error("Preview stream unavailable. Thumbnail mode active.")
            self.player.stop()
            self.preview_failed = True
            
            self.video_widget.hide()
            self.thumbnail_label.show()
            self.preview_error_lbl.show()
            self.preview_error_lbl.raise_()
            
            self.preview_error_lbl.adjustSize()
            self.preview_error_lbl.move(
                (self.video_frame.width() - self.preview_error_lbl.width()) // 2,
                (self.video_frame.height() - self.preview_error_lbl.height()) // 2
            )
            self.loading_overlay.stop()

    def _on_media_status_changed(self, status):
        if status in (QMediaPlayer.MediaStatus.BufferingMedia, QMediaPlayer.MediaStatus.LoadingMedia, QMediaPlayer.MediaStatus.StalledMedia):
            self.loading_overlay.start()
        else:
            self.loading_overlay.stop()

    def _show_toast_error(self, msg):
        self._show_toast(msg)

    def _show_toast(self, msg, icon_pixmap=None, rich_text=False):
        if self.main_win and hasattr(self.main_win, "toast_mgr"):
            self.main_win.toast_mgr.show_toast(msg, icon_pixmap=icon_pixmap, rich_text=rich_text)
        else:
            if not hasattr(self, "local_toast_mgr"):
                self.local_toast_mgr = LocalToastManager(self)
            self.local_toast_mgr.show_toast(msg, icon_pixmap=icon_pixmap, rich_text=rich_text)

    def _mark_inputs_invalid(self):
        err_style = f"background-color: #2A1515; color: {TEXT_MAIN}; border: 1.5px solid {ERROR_RED}; border-radius: 4px; padding: 0 8px; font-size: 13px;"
        self.start_input.setStyleSheet(err_style)
        self.end_input.setStyleSheet(err_style)
        jiggle_widget(self.start_input)
        jiggle_widget(self.end_input)
        jiggle_widget(self.btn_download)

    def _reset_inputs_style(self):
        norm_style = f"background-color: {INPUT_BG}; color: {TEXT_MAIN}; border: none; border-radius: 4px; padding: 0 8px; font-size: 13px;"
        self.start_input.setStyleSheet(norm_style)
        self.end_input.setStyleSheet(norm_style)

    def _open_site_url(self):
        url = self.current_info.get('_url')
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _build_ui(self):
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet(f"background-color: {BG_DARK};")

        self.search_container = QWidget()
        sc_layout = QVBoxLayout(self.search_container)
        sc_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_lbl = QLabel("TRIMLOAD")
        self.title_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 68px; font-weight: 300;")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sc_layout.addWidget(self.title_lbl)
        sc_layout.addSpacing(20)

        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("Enter link to trim...")
        self.url_entry.setFixedSize(600, 54)
        self.url_entry.setStyleSheet(f"QLineEdit {{ background-color: {INPUT_BG}; color: {TEXT_MAIN}; border-radius: 27px; padding: 0 20px; font-size: 18px; border: none; }}")
        self.url_entry.returnPressed.connect(self._process_link)
        sc_layout.addWidget(self.url_entry, alignment=Qt.AlignmentFlag.AlignCenter)

        self.processing_slot = QWidget()
        self.proc_layout = QVBoxLayout(self.processing_slot)
        sc_layout.addWidget(self.processing_slot)

        self.layout.addWidget(self.search_container)

        self.editor_container = QWidget()
        ed_layout = QVBoxLayout(self.editor_container)
        ed_layout.setContentsMargins(20, 16, 20, 20)
        ed_layout.setSpacing(10)

        self.video_frame = QFrame()
        self.video_frame.setStyleSheet("background-color: #000000;")
        vf_layout = QVBoxLayout(self.video_frame)
        vf_layout.setContentsMargins(0, 0, 0, 0)

        self.video_widget = QVideoWidget()
        self.player.setVideoOutput(self.video_widget)
        vf_layout.addWidget(self.video_widget)

        self.thumbnail_label = AspectRatioLabel(self.video_frame)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vf_layout.addWidget(self.thumbnail_label)
        self.thumbnail_label.hide()
        
        self.preview_error_lbl = QLabel("Couldn't load preview", self.video_frame)
        self.preview_error_lbl.setStyleSheet("color: #FFFFFF; font-size: 16px; font-weight: bold; background-color: rgba(0, 0, 0, 180); padding: 12px 24px;")
        self.preview_error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_error_lbl.hide()

        self.loading_overlay = LoadingOverlay(self.video_frame)

        ed_layout.addWidget(self.video_frame, 1)

        self.meta_container = QWidget()
        meta_layout = QVBoxLayout(self.meta_container)
        meta_layout.setContentsMargins(4, 2, 4, 4)
        meta_layout.setSpacing(4)

        self.meta_title_lbl = QLabel("Media Title")
        self.meta_title_lbl.setStyleSheet("color: #FFFFFF; font-size: 20px; font-weight: bold; border: none;")
        self.meta_title_lbl.setWordWrap(True)

        sub_layout = QHBoxLayout()
        sub_layout.setContentsMargins(0, 0, 0, 0)
        sub_layout.setSpacing(8)

        self.meta_channel_lbl = QLabel("Channel")
        self.meta_channel_lbl.setStyleSheet("color: #DDDDDD; font-size: 14px; border: none;")

        self.meta_dot1_lbl = QLabel("•")
        self.meta_dot1_lbl.setStyleSheet("color: #8A8A8A; font-size: 14px; border: none;")

        self.meta_dur_lbl = QLabel("0:00 mins")
        self.meta_dur_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 14px; border: none;")

        self.meta_dot2_lbl = QLabel("•")
        self.meta_dot2_lbl.setStyleSheet("color: #8A8A8A; font-size: 14px; border: none;")

        self.meta_site_icon = QLabel("▶")
        self.meta_site_icon.setStyleSheet("color: #FF0000; font-size: 14px; border: none;")
        self.meta_site_icon.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.meta_site_icon.mousePressEvent = lambda event: self._open_site_url()

        self.meta_site_lbl = QLabel("YouTube")
        self.meta_site_lbl.setStyleSheet("color: #FFFFFF; font-size: 14px; font-weight: bold; border: none;")
        self.meta_site_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.meta_site_lbl.mousePressEvent = lambda event: self._open_site_url()

        sub_layout.addWidget(self.meta_channel_lbl)
        sub_layout.addWidget(self.meta_dot1_lbl)
        sub_layout.addWidget(self.meta_dur_lbl)
        sub_layout.addWidget(self.meta_dot2_lbl)
        sub_layout.addWidget(self.meta_site_icon)
        sub_layout.addWidget(self.meta_site_lbl)
        sub_layout.addStretch()

        meta_layout.addWidget(self.meta_title_lbl)
        meta_layout.addLayout(sub_layout)

        ed_layout.addWidget(self.meta_container)

        timeline_frame = QFrame()
        timeline_frame.setFixedHeight(54)
        timeline_frame.setStyleSheet("background-color: #1A1A1A;")
        tl_layout = QHBoxLayout(timeline_frame)
        tl_layout.setContentsMargins(8, 0, 12, 0)
        tl_layout.setSpacing(6)

        self.btn_play = QPushButton()
        self.btn_play.setFixedSize(32, 32)
        self.btn_play.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_play.setStyleSheet(f"QPushButton {{ background: transparent; border: none; }}")
        
        icon_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
        self.icon_play = QIcon(os.path.join(icon_dir, "play.png"))
        self.icon_pause = QIcon(os.path.join(icon_dir, "pause.png"))
        
        self.btn_play.setIcon(self.icon_play)
        self.btn_play.clicked.connect(self._toggle_play)
        tl_layout.addWidget(self.btn_play)

        self.time_lbl = QLabel("00:00:00 / 00:00:00")
        self.time_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 13px; font-family: monospace; border: none;")
        tl_layout.addWidget(self.time_lbl)

        self.range_slider = TrimRangeSlider()
        self.range_slider.positionChanged.connect(self._on_slider_position_changed)
        self.range_slider.seekRequested.connect(self._seek_player)
        self.range_slider.trimRangeChanged.connect(self._on_trim_range_changed)
        tl_layout.addWidget(self.range_slider, 1)

        ed_layout.addWidget(timeline_frame)

        dock_frame = QFrame()
        dock_frame.setFixedHeight(96) # Raised from 76 to fit two clean rows
        dock_frame.setStyleSheet("QFrame { background-color: #1A1A1A; }")
        
        grid = QGridLayout(dock_frame)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8) # Raised slightly for row separation

        # --- ROW 0 & 1: TIME CONTROLS (Left Side) ---
        lbl_start = QLabel("Start")
        lbl_start.setStyleSheet("color: #FFFFFF; font-size: 13px;")
        grid.addWidget(lbl_start, 0, 0)

        lbl_end = QLabel("End")
        lbl_end.setStyleSheet("color: #FFFFFF; font-size: 13px;")
        grid.addWidget(lbl_end, 0, 1)

        self.start_input = QLineEdit("00:00:00")
        self.start_input.setFixedSize(80, 28)
        self.start_input.editingFinished.connect(self._on_inputs_edited)
        grid.addWidget(self.start_input, 1, 0)

        self.end_input = QLineEdit("00:00:00")
        self.end_input.setFixedSize(80, 28)
        self.end_input.editingFinished.connect(self._on_inputs_edited)
        grid.addWidget(self.end_input, 1, 1)

        grid.setColumnMinimumWidth(2, 24)

        # --- ROW 0: SETTINGS CONTROLS (Shifted Higher) ---
        mode_frame = QFrame()
        mode_frame.setFixedSize(130, 28)
        mode_frame.setStyleSheet("QFrame { background-color: #252525; border: 1px solid #333333; border-radius: 6px; }")
        m_layout = QHBoxLayout(mode_frame)
        m_layout.setContentsMargins(2, 2, 2, 2)
        m_layout.setSpacing(0)

        self.btn_mode_vid = QPushButton("Video")
        self.btn_mode_aud = QPushButton("Audio")
        for b in (self.btn_mode_vid, self.btn_mode_aud):
            b.setFixedHeight(24)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            
        self.btn_mode_vid.clicked.connect(lambda: self._set_mode(False))
        self.btn_mode_aud.clicked.connect(lambda: self._set_mode(True))
        m_layout.addWidget(self.btn_mode_vid)
        m_layout.addWidget(self.btn_mode_aud)
        
        # MOVED TO ROW 0
        grid.addWidget(mode_frame, 0, 3)

        self.qual_combo = AnimatedComboBox()
        self.qual_combo.setFixedSize(90, 28)
        self.qual_combo.setStyleSheet("""
            QComboBox { 
                background-color: #1A1A1A; 
                color: #FFFFFF; 
                font-size: 12px; 
                border-radius: 6px; 
                padding: 0 10px; 
                border: 1px solid #2A2A2A; 
            }
            QComboBox::drop-down { border: none; width: 0px; }
            QComboBox::down-arrow { image: none; border: none; }
            QComboBox QAbstractItemView { 
                background-color: #181818; color: #FFFFFF; 
                selection-background-color: #B5B5B5; selection-color: #000000; 
                border: 1px solid #FFFFFF; border-radius: 4px; outline: none; padding: 2px 0px;
            }
            QComboBox QAbstractItemView::item { min-height: 26px; padding-left: 10px; color: #FFFFFF; }
            QComboBox QAbstractItemView::item:selected { background-color: #B5B5B5; color: #000000; }
        """)
        self.qual_combo.currentIndexChanged.connect(self._update_est_size)
        
        # MOVED TO ROW 0
        grid.addWidget(self.qual_combo, 0, 4)

        self.est_size_lbl = QLabel("Est. size ~0 MB")
        self.est_size_lbl.setStyleSheet("color: #EEEEEE; font-size: 13px;")
        
        # MOVED TO ROW 0
        grid.addWidget(self.est_size_lbl, 0, 5)

        # --- ROW 1: ACTION BUTTONS (Shifted Lower Right) ---
        spacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        grid.addItem(spacer, 1, 3)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setFixedSize(90, 32)
        self.btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_cancel.setStyleSheet(f"QPushButton {{ background: transparent; border: 1px solid {TEAL_ACCENT}; color: #FFFFFF; border-radius: 16px; font-size: 14px; font-weight: 400; }} QPushButton:hover {{ background: #222; }}")
        self.btn_cancel.clicked.connect(self._show_initial_view)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_download = QPushButton("Download")
        self.btn_download.setFixedSize(100, 32)
        self.btn_download.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_download.setStyleSheet(f"QPushButton {{ background-color: {TEAL_ACCENT}; color: #000000; border-radius: 16px; font-size: 14px; border: none; font-weight: 500; }} QPushButton:hover {{ background-color: #00A892; }}")
        self.btn_download.clicked.connect(self._on_download)
        btn_layout.addWidget(self.btn_download)

        # MOVED TO ROW 1, SPANNING THE LOWER RIGHT COLUMNS
        grid.addLayout(btn_layout, 1, 4, 1, 3)


        ed_layout.addWidget(dock_frame)
        self.layout.addWidget(self.editor_container)

    def _show_initial_view(self):
        # Safely release the network stream immediately to prevent Qt from freezing the main thread during reset
        if hasattr(self, 'player'):
            self.player.pause()
            self.player.setSource(QUrl())
            self.player.stop()
            
        if hasattr(self, 'loading_overlay'):
            self.loading_overlay.stop()
        
        self.preview_failed = False
        if hasattr(self, 'preview_error_lbl'):
            self.preview_error_lbl.hide()
            
        if hasattr(self, 'btn_play') and hasattr(self, 'icon_play'):
            self.btn_play.setIcon(self.icon_play)
            
        if hasattr(self, 'editor_container'):
            self.editor_container.hide()
            self.search_container.show()
            self.title_lbl.show()
            self.url_entry.show()
            self.url_entry.clear()
            self._reset_inputs_style()
            self._clear_processing_slot()

    def _show_initial_view_keep_text(self):
        """Same as _show_initial_view but leaves the pasted link in place -
        used for Cancel (while processing) and Try Again (after a
        failure), where the user almost certainly wants to retry the same
        link rather than retype it. The plain _show_initial_view above
        stays untouched since its other callers (the trim editor's own
        Cancel button, and the post-download reset) are genuine "start
        completely over" actions where clearing makes sense."""
        if hasattr(self, 'player'):
            self.player.pause()
            self.player.setSource(QUrl())
            self.player.stop()

        if hasattr(self, 'loading_overlay'):
            self.loading_overlay.stop()

        self.preview_failed = False
        if hasattr(self, 'preview_error_lbl'):
            self.preview_error_lbl.hide()

        if hasattr(self, 'btn_play') and hasattr(self, 'icon_play'):
            self.btn_play.setIcon(self.icon_play)

        if hasattr(self, 'editor_container'):
            self.editor_container.hide()
            self.search_container.show()
            self.title_lbl.show()
            self.url_entry.show()
            self._reset_inputs_style()
            self._clear_processing_slot()

    def _clear_processing_slot(self):
        while self.proc_layout.count():
            item = self.proc_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_processing_cancel(self):
        if self._proc_cancel_flag is not None: self._proc_cancel_flag[0] = True
        self._proc_run_id += 1
        self._show_initial_view_keep_text()
        self.proc_overlay.dismiss()

    def _on_trim_phase(self, run_id, key):
        if run_id == self._proc_run_id:
            self.proc_overlay.set_phase(key)

    def _process_link(self):
        url = self.url_entry.text().strip()
        if not url: return

        if self._proc_cancel_flag is not None: self._proc_cancel_flag[0] = True
        self._proc_run_id += 1
        run_id = self._proc_run_id
        cancelled = [False]
        self._proc_cancel_flag = cancelled

        host = (urlparse(url if "://" in url else "//" + url).netloc or url).lower()
        if host.startswith("www."): host = host[4:]
        self.proc_overlay.begin(host)

        def _cover():
            if cancelled[0] or run_id != self._proc_run_id: return
            self.title_lbl.hide(); self.url_entry.hide(); self._clear_processing_slot()
        QTimer.singleShot(300, _cover)

        threading.Thread(target=self._scrape_and_load, args=(url, run_id, cancelled), daemon=True).start()

    def _scrape_and_load(self, url, run_id, cancelled):
        if not yt_dlp:
            self.signals.error.emit(run_id, "yt-dlp library missing")
            return

        try:
            self.signals.phase.emit(run_id, "fetch")
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'noplaylist': True,
                'user_agent': DEFAULT_UA
            }

            if "music.youtube.com" in url:
                ydl_opts['extractor_args'] = {'youtube': ['player_client=tv,web_safari']}
                ydl_opts['http_headers'] = {'Accept-Language': 'en-US,en;q=0.9'}
            elif "youtube.com" in url or "youtu.be" in url:
                ydl_opts['extractor_args'] = {'youtube': ['player_client=tv,web_safari']}
                ydl_opts['http_headers'] = {'Accept-Language': 'en-US,en;q=0.9'}

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if cancelled[0]: return

            self.signals.phase.emit(run_id, "process")
            preview_url, preview_height, is_audio_only = select_preview_stream(info, url)

            info['_preview_stream'] = preview_url
            info['_preview_height'] = preview_height
            info['_is_audio_only'] = is_audio_only
            info['_url'] = url
            info['_run_id'] = run_id

            self.signals.phase.emit(run_id, "preview")
            thumb_url = info.get('thumbnail')
            if thumb_url:
                try:
                    req = urllib.request.Request(thumb_url, headers={'User-Agent': DEFAULT_UA})
                    with urllib.request.urlopen(req, timeout=5) as response:
                        info['_thumb_data'] = response.read()
                except Exception:
                    pass
            
            info['_fav_path'] = _fetch_favicon_sync(url)

            if not cancelled[0]: self.signals.loaded.emit(info)

        except Exception as e:
            if not cancelled[0]: self.signals.error.emit(run_id, str(e))

    def _on_scrape_error(self, run_id, msg):
        if run_id != self._proc_run_id: return
        if self._proc_cancel_flag is not None and self._proc_cancel_flag[0]: return
        self.proc_overlay.fail(msg)

    def _on_metadata_loaded(self, info):
        if info.get('_run_id') != self._proc_run_id: return
        if self._proc_cancel_flag is not None and self._proc_cancel_flag[0]: return
        run_id = self._proc_run_id
        def _reveal():
            if run_id != self._proc_run_id: return
            self._reveal_trim_result(info)
            self.proc_overlay.dismiss()
        self.proc_overlay.finish(_reveal)

    def _reveal_trim_result(self, info):
        self.current_info = info
        self.preview_failed = False
        self.preview_error_lbl.hide()
        
        self.search_container.hide()
        self.editor_container.show()

        title = info.get('title', 'Media Preview')
        channel = info.get('uploader') or info.get('channel') or info.get('uploader_id') or 'Unknown'
        
        dur_sec = float(info.get('duration') or 1.0)
        if dur_sec > 0:
            t = int(dur_sec)
            m, s = divmod(t, 60)
            h, m = divmod(m, 60)
            dur_str = f"{h}:{m:02d}:{s:02d} mins" if h else f"{m}:{s:02d} mins"
        else:
            dur_str = "0:00 mins"

        url = info.get('_url', '')
        site = _site_name(url)

        self.meta_title_lbl.setText(title)
        self.meta_channel_lbl.setText(channel)
        self.meta_dur_lbl.setText(dur_str)
        self.meta_site_lbl.setText(site)
        self.meta_site_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.meta_site_icon.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        
        fav_path = info.get('_fav_path')
        if fav_path and os.path.exists(fav_path):
            self.meta_site_icon.clear()
            pix = QPixmap(fav_path).scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.meta_site_icon.setPixmap(pix)
            self.meta_site_icon.setStyleSheet("border: none; background: transparent;")
        else:
            if "Youtube" in site or "YouTube" in site:
                self.meta_site_icon.setText("▶")
                self.meta_site_icon.setStyleSheet("color: #FF0000; font-size: 14px; border: none;")
            else:
                self.meta_site_icon.setText("🌐")
                self.meta_site_icon.setStyleSheet(f"color: {TEAL_ACCENT}; font-size: 14px; border: none;")

        if '_thumb_data' in info:
            pixmap = QPixmap()
            pixmap.loadFromData(info['_thumb_data'])
            self.thumbnail_label.setPixmap(pixmap)
        else:
            self.thumbnail_label.setPixmap(QPixmap())

        self.duration = dur_sec
        self.start_trim = 0.0
        self.end_trim = self.duration

        self.range_slider.set_duration(self.duration)
        self.start_input.setText(format_time_str(0))
        self.end_input.setText(format_time_str(self.duration))
        self.time_lbl.setText(f"{format_time_str(0)} / {format_time_str(self.duration)}")
        self._reset_inputs_style()

        self.qual_combo.clear()
        formats = info.get('formats', [])
        video_formats = [f for f in formats if f.get('vcodec') not in ('none', None)]

        if video_formats:
            seen = set()
            for f in reversed(video_formats):
                h = f.get('height')
                if h and h not in seen:
                    seen.add(h)
                    self.qual_combo.addItem(f"{h}p", f)
        if self.qual_combo.count() == 0:
            self.qual_combo.addItem("Best", None)

        is_pure_audio = info.get('_is_audio_only', False)

        if is_pure_audio:
            self.btn_mode_vid.setEnabled(False)
            self._set_mode(True)
        else:
            self.btn_mode_vid.setEnabled(True)
            self._set_mode(False)

        stream_url = info.get('_preview_stream')
        if stream_url:
            self.loading_overlay.start()
            self.player.setSource(QUrl(stream_url))
            self.player.play()
            self.btn_play.setIcon(self.icon_pause)
        else:
            self._on_player_error(QMediaPlayer.Error.ResourceError, "No stream url available")

        dur_s = round(self.proc_overlay._clock.elapsed() / 1000.0)
        color = ERROR_RED if dur_s >= 60 else TEXT_MAIN
        check_pix = QIcon(str(repo_root / "icons" / "check.png")).pixmap(22, 22)
        self._show_toast(
            f'Finished in <span style="color: {color};">{dur_s}s</span>',
            icon_pixmap=check_pix, rich_text=True
        )

    def _set_mode(self, is_audio):
        self.is_audio_mode = is_audio
        
        act = f"QPushButton {{ background-color: #383838; color: {TEAL_ACCENT}; font-size: 13px; font-weight: 500; border-radius: 4px; border: none; }}"
        inact = f"QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; font-size: 13px; font-weight: 500; border-radius: 4px; border: none; }}"
        
        if is_audio:
            self.btn_mode_aud.setStyleSheet(act)
            self.btn_mode_vid.setStyleSheet(inact)
            self.video_widget.hide()
            self.thumbnail_label.show()
            
            if self.preview_failed:
                self.preview_error_lbl.show()
                self.preview_error_lbl.raise_()
            else:
                self.preview_error_lbl.hide()
        else:
            self.btn_mode_vid.setStyleSheet(act)
            self.btn_mode_aud.setStyleSheet(inact)
            
            if self.preview_failed:
                self.video_widget.hide()
                self.thumbnail_label.show()
                self.preview_error_lbl.show()
                self.preview_error_lbl.raise_()
            else:
                self.video_widget.show()
                self.thumbnail_label.hide()
                self.preview_error_lbl.hide()
                
        self._update_est_size()

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setIcon(self.icon_play)
        else:
            self.player.play()
            self.btn_play.setIcon(self.icon_pause)

    def _seek_player(self, pos_sec):
        self.player.setPosition(int(pos_sec * 1000))

    def _on_slider_position_changed(self, sec):
        if self.range_slider._dragging:
            self.time_lbl.setText(f"{format_time_str(sec)} / {format_time_str(self.duration)}")

    def _on_player_position_changed(self, ms):
        if self.range_slider._dragging:
            return 
        
        sec = ms / 1000.0
        self.range_slider.set_position(sec)
        self.time_lbl.setText(f"{format_time_str(sec)} / {format_time_str(self.duration)}")

        if sec >= self.end_trim:
            self.player.setPosition(int(self.start_trim * 1000))
        elif sec < (self.start_trim - 0.2): 
            self.player.setPosition(int(self.start_trim * 1000))

    def _on_player_duration_changed(self, ms):
        if ms > 0:
            self.duration = ms / 1000.0
            self.range_slider.set_duration(self.duration)

    def _on_trim_range_changed(self, start, end):
        self.start_trim = start
        self.end_trim = end
        self.start_input.setText(format_time_str(start))
        self.end_input.setText(format_time_str(end))
        self._reset_inputs_style()
        self._update_est_size()

    def _on_inputs_edited(self):
        s_text = self.start_input.text().strip()
        e_text = self.end_input.text().strip()
        s = parse_time_str(s_text)
        e = parse_time_str(e_text)
        
        if s < 0 or e < 0:
            self._mark_inputs_invalid()
            self._show_toast_error("Invalid time format")
            return

        if s >= e:
            self._mark_inputs_invalid()
            self._show_toast_error("Start time must be before end time")
            return

        if s >= self.duration or e > self.duration:
            self._mark_inputs_invalid()
            self._show_toast_error("Trim range exceeds media duration")
            return

        self.start_trim = s
        self.end_trim = e

        self.start_input.setText(format_time_str(s))
        self.end_input.setText(format_time_str(e))

        self.range_slider.start_time = s
        self.range_slider.end_time = e
        self.range_slider.update()

        self._reset_inputs_style()
        self._update_est_size()

    def _update_est_size(self):
        if not self.current_info:
            return

        duration_selected = max(0.1, self.end_trim - self.start_trim)
        total_duration = max(1.0, float(self.current_info.get('duration') or self.duration or 1.0))

        fmt = self.qual_combo.currentData()
        est_bytes = 0

        if self.is_audio_mode:
            formats = self.current_info.get('formats') or []
            audio_fmts = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') != 'none']
            best_audio = audio_fmts[0] if audio_fmts else None
            
            if best_audio and (best_audio.get('filesize') or best_audio.get('filesize_approx')):
                full_sz = best_audio.get('filesize') or best_audio.get('filesize_approx')
                est_bytes = (full_sz / total_duration) * duration_selected
            elif best_audio and best_audio.get('abr'):
                abr = best_audio.get('abr')
                est_bytes = (abr * 1000 / 8) * duration_selected
            else:
                est_bytes = (160 * 1000 / 8) * duration_selected
        else:
            if isinstance(fmt, dict):
                full_sz = fmt.get('filesize') or fmt.get('filesize_approx')
                tbr = fmt.get('tbr') or ((fmt.get('vbr') or 0) + (fmt.get('abr') or 0))
                
                if full_sz:
                    est_bytes = (full_sz / total_duration) * duration_selected
                    if fmt.get('acodec') == 'none':
                        est_bytes += (128 * 1000 / 8) * duration_selected
                elif tbr:
                    est_bytes = (tbr * 1000 / 8) * duration_selected
                    if fmt.get('acodec') == 'none':
                        est_bytes += (128 * 1000 / 8) * duration_selected

            if est_bytes <= 0:
                qual_text = self.qual_combo.currentText()
                if "2160" in qual_text or "4k" in qual_text.lower(): bitrate_kbps = 15000
                elif "1440" in qual_text: bitrate_kbps = 9000
                elif "1080" in qual_text: bitrate_kbps = 4500
                elif "720" in qual_text: bitrate_kbps = 2500
                elif "480" in qual_text: bitrate_kbps = 1200
                elif "360" in qual_text: bitrate_kbps = 750
                else: bitrate_kbps = 2500
                
                est_bytes = (bitrate_kbps * 1000 / 8) * duration_selected

        if est_bytes >= 1024 * 1024 * 1024:
            size_gb = est_bytes / (1024 * 1024 * 1024)
            self.est_size_lbl.setText(f"Est. size ~{size_gb:.2f} GB")
        else:
            size_mb = est_bytes / (1024 * 1024)
            self.est_size_lbl.setText(f"Est. size ~{size_mb:.1f} MB")

    def _on_download(self):
        s_text = self.start_input.text().strip()
        e_text = self.end_input.text().strip()
        s = parse_time_str(s_text)
        e = parse_time_str(e_text)

        if s < 0 or e < 0:
            self._mark_inputs_invalid()
            self._show_toast_error("Invalid time format")
            return

        if s >= e:
            self._mark_inputs_invalid()
            self._show_toast_error("Start time must be before end time")
            return

        if s >= self.duration or e > self.duration:
            self._mark_inputs_invalid()
            self._show_toast_error("Trim range exceeds media duration")
            return

        if not self.main_win: return
        
        url = self.current_info.get('_url')
        title = self.current_info.get('title', 'Trimmed Media')
        selected_fmt = self.qual_combo.currentData()
        selected_fid = selected_fmt.get('format_id') if isinstance(selected_fmt, dict) else selected_fmt

        thumb_path = None
        thumb_data = self.current_info.get('_thumb_data')
        thumb_url = self.current_info.get('thumbnail')
        if thumb_data and thumb_url:
            try:
                t_hash = hashlib.md5(thumb_url.encode()).hexdigest()
                candidate = str(thumb_dir / f"{t_hash}.png")
                if not os.path.exists(candidate):
                    with open(candidate, 'wb') as f:
                        f.write(thumb_data)
                thumb_path = candidate
            except Exception:
                thumb_path = None
        
        item = self.main_win.DownloadItem(
            url=url,
            title=f"[Trimmed] {title}",
            channel=self.current_info.get('uploader') or self.current_info.get('channel', 'YouTube'),
            thumb_path=thumb_path,
            out_dir=str(Path(__file__).resolve().parent / "downloads"),
            is_audio=self.is_audio_mode,
            selected_fid=selected_fid,
            duration_str=format_time_str(e - s),
            site_name=_site_name(url),
            thumb_url=thumb_url,
            fav_path=self.current_info.get('_fav_path'),
            start_time=s,
            end_time=e
        )
        
        self.main_win._dl_items.append(item)
        if hasattr(self.main_win, "_dl_item_added"):
            self.main_win._dl_item_added(item)
            
        self.main_win._start_download(item)
        self.main_win._show_tab(2)
        
        # Defer the UI reset logic to safely prevent GUI hangs during the tab switch 
        QTimer.singleShot(50, self._show_initial_view)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TrimLoadTab()
    window.resize(900, 600)
    window.show()
    sys.exit(app.exec())