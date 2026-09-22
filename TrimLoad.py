import sys
import os
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
                             QGridLayout, QSpacerItem, QSizePolicy)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import (Qt, QUrl, Signal, QPointF, QRectF, QLineF, QObject, QTimer, 
                          QPropertyAnimation, QSequentialAnimationGroup, QParallelAnimationGroup, 
                          QPoint, QEasingCurve)
from PySide6.QtGui import QFont, QPainter, QColor, QPen, QCursor, QPixmap, QIcon, QDesktopServices

try:
    from PySide6.QtSvgWidgets import QSvgWidget
    from PySide6.QtSvg import QSvgRenderer
    HAS_SVG = True
except ImportError:
    HAS_SVG = False

try:
    import yt_dlp
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
        self.setStyleSheet("background-color: rgba(0, 0, 0, 160); border-radius: 12px;")
        
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
        icon_lbl.setStyleSheet("color: #FF5555; font-size: 15px; font-weight: bold; border: none; background: transparent;")
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


class LocalToastManager(QObject):
    def __init__(self, parent_window):
        super().__init__(parent_window)
        self.win = parent_window
        self.toasts = []

    def show_toast(self, message):
        toast = StandaloneToastWidget(message, parent=self.win)
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
        self.setStyleSheet("background-color: #000000; border-radius: 12px;")
        
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
    seekRequested = Signal(float)  # Emitted only when dragging finishes
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


class TrimSignals(QObject):
    loaded = Signal(dict)
    error = Signal(str)


class TrimLoadTab(QWidget):
    def __init__(self, main_win=None):
        super().__init__()
        self.main_win = main_win
        self.signals = TrimSignals()
        self.signals.loaded.connect(self._on_metadata_loaded)
        self.signals.error.connect(self._show_error)
        
        self.current_info = {}
        self.duration = 1.0
        self.start_trim = 0.0
        self.end_trim = 1.0
        self.is_audio_mode = False
        self.preview_failed = False 

        self._setup_player()
        self._build_ui()
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
        if self.main_win and hasattr(self.main_win, "toast_mgr"):
            self.main_win.toast_mgr.show_toast(msg)
        else:
            if not hasattr(self, "local_toast_mgr"):
                self.local_toast_mgr = LocalToastManager(self)
            self.local_toast_mgr.show_toast(msg)

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
        self.video_frame.setStyleSheet("background-color: #000000; border-radius: 12px;")
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
        self.preview_error_lbl.setStyleSheet("color: #FFFFFF; font-size: 16px; font-weight: bold; background-color: rgba(0, 0, 0, 180); border-radius: 8px; padding: 12px 24px;")
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
        timeline_frame.setStyleSheet("background-color: #1A1A1A; border-radius: 8px;")
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
        dock_frame.setFixedHeight(76)
        dock_frame.setStyleSheet("QFrame { background-color: #1A1A1A; border-radius: 12px; }")
        
        grid = QGridLayout(dock_frame)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)

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
        
        grid.addWidget(mode_frame, 1, 3)

        grid.setColumnMinimumWidth(4, 16)

        self.qual_combo = QComboBox()
        self.qual_combo.setFixedSize(90, 28)
        self.qual_combo.setStyleSheet(f"QComboBox {{ background-color: #252525; color: {TEAL_ACCENT}; border: 1px solid #333333; border-radius: 6px; padding: 0 10px; font-weight: 400; font-size: 13px; }} QComboBox::drop-down {{ border: none; }} QComboBox QAbstractItemView {{ background: #202020; color: #FFF; }}")
        self.qual_combo.currentIndexChanged.connect(self._update_est_size)
        grid.addWidget(self.qual_combo, 1, 5)

        self.est_size_lbl = QLabel("Est. size ~0 MB")
        self.est_size_lbl.setStyleSheet("color: #EEEEEE; font-size: 13px;")
        grid.addWidget(self.est_size_lbl, 1, 6)

        spacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        grid.addItem(spacer, 1, 7)

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

        grid.addLayout(btn_layout, 1, 8)

        ed_layout.addWidget(dock_frame)
        self.layout.addWidget(self.editor_container)

    def _show_initial_view(self):
        self.player.stop()
        self.loading_overlay.stop()
        
        self.preview_failed = False
        if hasattr(self, 'preview_error_lbl'):
            self.preview_error_lbl.hide()
            
        self.btn_play.setIcon(self.icon_play)
        self.editor_container.hide()
        self.search_container.show()
        self.title_lbl.show()
        self.url_entry.show()
        self.url_entry.clear()
        self._reset_inputs_style()
        self._clear_processing_slot()

    def _clear_processing_slot(self):
        while self.proc_layout.count():
            item = self.proc_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _process_link(self):
        url = self.url_entry.text().strip()
        if not url: return

        self.title_lbl.hide()
        self.url_entry.hide()
        self._clear_processing_slot()

        lbl = QLabel("Processing link...")
        lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 20px; font-weight: bold;")
        self.proc_layout.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        bar = QProgressBar()
        bar.setFixedSize(280, 6)
        bar.setTextVisible(False)
        bar.setRange(0, 0)
        bar.setStyleSheet(f"QProgressBar {{ background-color: #333; border: none; border-radius: 3px; }} QProgressBar::chunk {{ background-color: {TEAL_ACCENT}; }}")
        self.proc_layout.addWidget(bar, alignment=Qt.AlignmentFlag.AlignCenter)

        btn = QPushButton("Cancel")
        btn.setFixedSize(110, 34)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setStyleSheet(f"QPushButton {{ border: 1px solid {TEAL_ACCENT}; border-radius: 17px; color: {TEXT_MAIN}; background: transparent; font-weight: bold; }}")
        btn.clicked.connect(self._show_initial_view)
        self.proc_layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        threading.Thread(target=self._scrape_and_load, args=(url,), daemon=True).start()

    def _scrape_and_load(self, url):
        if not yt_dlp:
            self.signals.error.emit("yt-dlp library missing")
            return

        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'noplaylist': True,
                'user_agent': DEFAULT_UA
            }

            if "music.youtube.com" in url:
                ydl_opts['extractor_args'] = {'youtube': ['player_client=android']}
            elif "youtube.com" in url or "youtu.be" in url:
                ydl_opts['extractor_args'] = {'youtube': ['lang=en', 'player_client=web']}
                ydl_opts['http_headers'] = {'Accept-Language': 'en-US,en;q=0.9'}

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            preview_url, preview_height, is_audio_only = select_preview_stream(info, url)

            info['_preview_stream'] = preview_url
            info['_preview_height'] = preview_height
            info['_is_audio_only'] = is_audio_only
            info['_url'] = url

            thumb_url = info.get('thumbnail')
            if thumb_url:
                try:
                    req = urllib.request.Request(thumb_url, headers={'User-Agent': DEFAULT_UA})
                    with urllib.request.urlopen(req, timeout=5) as response:
                        info['_thumb_data'] = response.read()
                except Exception:
                    pass
            
            info['_fav_path'] = _fetch_favicon_sync(url)

            self.signals.loaded.emit(info)

        except Exception as e:
            self.signals.error.emit(str(e))

    def _show_error(self, msg):
        self._clear_processing_slot()
        lbl = QLabel(f"Error: {msg}")
        lbl.setStyleSheet(f"color: {ERROR_RED}; font-size: 14px; font-weight: bold;")
        self.proc_layout.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        btn = QPushButton("Try Again")
        btn.setFixedSize(110, 34)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setStyleSheet(f"QPushButton {{ background-color: {TEAL_ACCENT}; color: #000; border-radius: 17px; font-weight: bold; }}")
        btn.clicked.connect(self._show_initial_view)
        self.proc_layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def _on_metadata_loaded(self, info):
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
            start_time=s,
            end_time=e
        )
        
        self.main_win._dl_items.append(item)
        if hasattr(self.main_win, "_dl_data_changed"):
            self.main_win._dl_data_changed()
        self.main_win._start_download(item)
        self.main_win._show_tab(2)
        self._show_initial_view()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TrimLoadTab()
    window.resize(900, 600)
    window.show()
    sys.exit(app.exec())