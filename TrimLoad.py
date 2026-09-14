import sys
import os
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QLineEdit, QComboBox, 
                             QProgressBar, QFrame)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import Qt, QUrl, Signal, QPointF, QRectF, QObject
from PySide6.QtGui import QFont, QPainter, QColor, QPen, QCursor, QPixmap, QIcon

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

# UI Theme Constants
TEAL_ACCENT   = "#00BFA5"
BG_DARK       = "#121212"
CARD_BG       = "#1E1E1E"
INPUT_BG      = "#2A2A2A"
TEXT_MAIN     = "#FFFFFF"
TEXT_MUTED    = "#8A8A8A"
ERROR_RED     = "#FF5555"

# Global Headers
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


def format_time_str(seconds):
    seconds = max(0, float(seconds))
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_time_str(time_str):
    try:
        parts = list(map(int, time_str.split(":")))
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 1:
            return parts[0]
    except Exception:
        pass
    return 0.0


def select_preview_stream(info, url):
    """
    Selects preview stream URL prioritizing QtMultimedia (WMF on Windows) compatibility.
    Handles YouTube, YT Music, X (Twitter), Bilibili, SoundCloud, and direct links.
    Returns: (url, height, is_audio_only)
    """
    if not isinstance(info, dict):
        return None, None, False

    formats = info.get('formats') or []

    # If format list is missing, check direct URL
    if not formats:
        stream_url = info.get('url')
        if stream_url:
            vcodec = info.get('vcodec')
            is_audio = vcodec == 'none'
            return stream_url, 0, is_audio
        return None, None, False

    valid = [f for f in formats if f.get('url') and str(f.get('url')).startswith('http')]

    # 1. YT Music Specific Logic (Force audio only)
    if "music.youtube.com" in url:
        auds = [f for f in valid if f.get('acodec') not in ('none', None) and f.get('vcodec') in ('none', None)]
        if auds:
            m4a = [a for a in auds if a.get('ext') == 'm4a']
            best = sorted(m4a or auds, key=lambda x: x.get('abr') or 0, reverse=True)[0]
            return best.get('url'), 0, True

    # 2. Extract muxed streams (Audio + Video) and Audio-only streams
    auds_only = [f for f in valid if f.get('acodec') not in ('none', None) and f.get('vcodec') in ('none', None)]
    muxed = [f for f in valid if f.get('vcodec') not in ('none', None) and f.get('acodec') not in ('none', None)]

    # X (Twitter), Bilibili, TikTok workaround: If no explicit muxed streams, 
    # some M3U8 or MP4 streams contain both but acodec is reported as None.
    if not muxed:
        for f in valid:
            vcodec = f.get('vcodec')
            acodec = f.get('acodec')
            ext = f.get('ext')
            proto = f.get('protocol')
            
            if vcodec not in ('none', None):
                if acodec in ('none', None) and (ext == 'mp4' or 'm3u8' in str(proto)):
                    muxed.append(f)

    # 3. Handle Audio-Only fallback (SoundCloud, pure audio)
    if not muxed:
        if auds_only:
            m4a = [a for a in auds_only if a.get('ext') in ('m4a', 'mp4')]
            best = sorted(m4a or auds_only, key=lambda x: x.get('abr') or 0, reverse=True)[0]
            return best.get('url'), 0, True
        else:
            vids_only = [f for f in valid if f.get('vcodec') not in ('none', None)]
            if vids_only:
                best = sorted(vids_only, key=lambda x: x.get('height') or 0, reverse=True)[0]
                return best.get('url'), best.get('height') or 0, False
            
            fallback = [f for f in valid]
            if fallback:
                return fallback[0].get('url'), 0, True
            return None, None, True

    # 4. Select best muxed stream for preview
    def score(f):
        s = 0
        h = f.get('height') or 0
        vcodec = (f.get('vcodec') or '').lower()
        ext = (f.get('ext') or '').lower()
        
        # Favor 360p - 480p for stable playback
        if 360 <= h <= 480: s += 1000
        elif 240 <= h < 360: s += 500
        elif 480 < h <= 720: s += 800
        elif h > 720: s += 200
        
        # Native WMF compatibility
        if 'avc' in vcodec or 'h264' in vcodec: s += 500
        if ext == 'mp4': s += 200
        return s

    best_muxed = max(muxed, key=score)
    return best_muxed.get('url'), best_muxed.get('height') or 0, False


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
    trimRangeChanged = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.duration = 1.0
        self.start_time = 0.0
        self.end_time = 1.0
        self.current_time = 0.0
        self._dragging = None

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
        cy = h // 2
        
        track_rect = QRectF(10, cy - 4, w - 20, 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#262626"))
        painter.drawRoundedRect(track_rect, 4, 4)
        
        usable_w = w - 20
        if usable_w > 0 and self.duration > 0:
            start_x = 10 + (self.start_time / self.duration) * usable_w
            end_x = 10 + (self.end_time / self.duration) * usable_w
            
            range_rect = QRectF(start_x, cy - 4, max(0, end_x - start_x), 8)
            painter.setBrush(QColor(TEAL_ACCENT))
            painter.drawRect(range_rect)
            
            painter.setPen(QPen(QColor(TEAL_ACCENT), 3))
            painter.drawLine(int(start_x), int(cy - 12), int(start_x), int(cy + 12))
            painter.drawLine(int(end_x), int(cy - 12), int(end_x), int(cy + 12))
            
            pos_x = 10 + (self.current_time / self.duration) * usable_w
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(QPointF(pos_x, cy), 6, 6)

    def mousePressEvent(self, event):
        x = event.position().x()
        usable_w = self.width() - 20
        if usable_w <= 0: return
        
        click_time = max(0.0, min(((x - 10) / usable_w) * self.duration, self.duration))
        start_x = 10 + (self.start_time / self.duration) * usable_w
        end_x = 10 + (self.end_time / self.duration) * usable_w
        
        if abs(x - start_x) < 14:
            self._dragging = 'start'
        elif abs(x - end_x) < 14:
            self._dragging = 'end'
        else:
            self._dragging = 'position'
            self.current_time = click_time
            self.positionChanged.emit(self.current_time)
        self.update()

    def mouseMoveEvent(self, event):
        if not self._dragging: return
        x = event.position().x()
        usable_w = self.width() - 20
        if usable_w <= 0: return
        
        t = max(0.0, min(((x - 10) / usable_w) * self.duration, self.duration))
        
        if self._dragging == 'start':
            self.start_time = min(t, self.end_time - 0.5)
            self.trimRangeChanged.emit(self.start_time, self.end_time)
        elif self._dragging == 'end':
            self.end_time = max(t, self.start_time + 0.5)
            self.trimRangeChanged.emit(self.start_time, self.end_time)
        elif self._dragging == 'position':
            self.current_time = t
            self.positionChanged.emit(self.current_time)
        self.update()

    def mouseReleaseEvent(self, event):
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

        self._setup_player()
        self._build_ui()
        self._show_initial_view()

    def _setup_player(self):
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(1.0)
        
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        
        self.player.positionChanged.connect(self._on_player_position_changed)
        self.player.durationChanged.connect(self._on_player_duration_changed)

    def _build_ui(self):
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet(f"background-color: {BG_DARK};")

        self.search_container = QWidget()
        sc_layout = QVBoxLayout(self.search_container)
        sc_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_lbl = QLabel("TRIMLOAD")
        self.title_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 64px; font-weight: 300; letter-spacing: 2px;")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sc_layout.addWidget(self.title_lbl)
        sc_layout.addSpacing(20)

        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("Enter link...")
        self.url_entry.setFixedSize(580, 50)
        self.url_entry.setStyleSheet(f"QLineEdit {{ background-color: {INPUT_BG}; color: {TEXT_MAIN}; border-radius: 25px; padding: 0 20px; font-size: 16px; border: none; }}")
        self.url_entry.returnPressed.connect(self._process_link)
        sc_layout.addWidget(self.url_entry, alignment=Qt.AlignmentFlag.AlignCenter)

        self.processing_slot = QWidget()
        self.proc_layout = QVBoxLayout(self.processing_slot)
        sc_layout.addWidget(self.processing_slot)

        self.layout.addWidget(self.search_container)

        self.editor_container = QWidget()
        ed_layout = QVBoxLayout(self.editor_container)
        ed_layout.setContentsMargins(20, 20, 20, 20)
        ed_layout.setSpacing(12)

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

        self.title_overlay = QLabel("Media Title", self.video_frame)
        self.title_overlay.setStyleSheet("color: #FFFFFF; font-size: 24px; font-weight: bold; background: transparent; padding: 16px;")
        self.title_overlay.move(10, 10)
        self.title_overlay.raise_()

        ed_layout.addWidget(self.video_frame, 1)

        timeline_frame = QFrame()
        timeline_frame.setFixedHeight(48)
        timeline_frame.setStyleSheet("background-color: #1A1A1A; border-radius: 8px;")
        tl_layout = QHBoxLayout(timeline_frame)
        tl_layout.setContentsMargins(12, 0, 12, 0)
        tl_layout.setSpacing(12)

        # Apply Play/Pause Icon Updates
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
        self.range_slider.positionChanged.connect(self._seek_player)
        self.range_slider.trimRangeChanged.connect(self._on_trim_range_changed)
        tl_layout.addWidget(self.range_slider, 1)

        ed_layout.addWidget(timeline_frame)

        dock_frame = QFrame()
        dock_frame.setFixedHeight(56)
        dock_layout = QHBoxLayout(dock_frame)
        dock_layout.setContentsMargins(0, 0, 0, 0)
        dock_layout.setSpacing(12)

        dock_layout.addWidget(QLabel("Start Trim", styleSheet=f"color: {TEXT_MUTED}; font-size: 12px;"))
        self.start_input = QLineEdit("00:00:00")
        self.start_input.setFixedSize(85, 36)
        self.start_input.setStyleSheet(f"background-color: {CARD_BG}; color: {TEXT_MAIN}; border: 1px solid #333; border-radius: 6px; padding: 4px;")
        self.start_input.editingFinished.connect(self._on_inputs_edited)
        dock_layout.addWidget(self.start_input)

        dock_layout.addWidget(QLabel("End Trim", styleSheet=f"color: {TEXT_MUTED}; font-size: 12px;"))
        self.end_input = QLineEdit("00:00:00")
        self.end_input.setFixedSize(85, 36)
        self.end_input.setStyleSheet(f"background-color: {CARD_BG}; color: {TEXT_MAIN}; border: 1px solid #333; border-radius: 6px; padding: 4px;")
        self.end_input.editingFinished.connect(self._on_inputs_edited)
        dock_layout.addWidget(self.end_input)

        mode_frame = QFrame()
        mode_frame.setFixedHeight(36)
        mode_frame.setStyleSheet("background-color: #222222; border-radius: 8px;")
        m_layout = QHBoxLayout(mode_frame)
        m_layout.setContentsMargins(2, 2, 2, 2)
        m_layout.setSpacing(2)

        self.btn_mode_vid = QPushButton("Video")
        self.btn_mode_aud = QPushButton("Audio")
        for b in (self.btn_mode_vid, self.btn_mode_aud):
            b.setFixedHeight(32)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self.btn_mode_vid.clicked.connect(lambda: self._set_mode(False))
        self.btn_mode_aud.clicked.connect(lambda: self._set_mode(True))
        m_layout.addWidget(self.btn_mode_vid)
        m_layout.addWidget(self.btn_mode_aud)
        dock_layout.addWidget(mode_frame)

        self.qual_combo = QComboBox()
        self.qual_combo.setFixedHeight(36)
        self.qual_combo.setStyleSheet(f"QComboBox {{ background-color: {CARD_BG}; color: {TEAL_ACCENT}; border: 1px solid #333; border-radius: 6px; padding: 0 10px; font-weight: bold; }} QComboBox QAbstractItemView {{ background: #202020; color: #FFF; }}")
        self.qual_combo.currentIndexChanged.connect(self._update_est_size)
        dock_layout.addWidget(self.qual_combo)

        self.est_size_lbl = QLabel("Est. size ~0 MB")
        self.est_size_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px;")
        dock_layout.addWidget(self.est_size_lbl)

        dock_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setFixedSize(100, 40)
        self.btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_cancel.setStyleSheet(f"QPushButton {{ background: transparent; border: 1px solid {TEAL_ACCENT}; color: {TEXT_MAIN}; border-radius: 20px; font-weight: bold; }} QPushButton:hover {{ background: #222; }}")
        self.btn_cancel.clicked.connect(self._show_initial_view)
        dock_layout.addWidget(self.btn_cancel)

        self.btn_download = QPushButton("Download")
        self.btn_download.setFixedSize(120, 40)
        self.btn_download.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_download.setStyleSheet(f"QPushButton {{ background-color: {TEAL_ACCENT}; color: #000; border-radius: 20px; font-weight: bold; border: none; }} QPushButton:hover {{ background-color: #00A892; }}")
        self.btn_download.clicked.connect(self._on_download)
        dock_layout.addWidget(self.btn_download)

        ed_layout.addWidget(dock_frame)
        self.layout.addWidget(self.editor_container)

    def _show_initial_view(self):
        self.player.stop()
        self.btn_play.setIcon(self.icon_play)
        self.editor_container.hide()
        self.search_container.show()
        self.title_lbl.show()
        self.url_entry.show()
        self.url_entry.clear()
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

            # Dynamically split extraction logic to prevent scraper clashes
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

            # Download thumbnail bytes in background thread
            thumb_url = info.get('thumbnail')
            if thumb_url:
                try:
                    req = urllib.request.Request(thumb_url, headers={'User-Agent': DEFAULT_UA})
                    with urllib.request.urlopen(req, timeout=5) as response:
                        info['_thumb_data'] = response.read()
                except Exception:
                    pass

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
        self.search_container.hide()
        self.editor_container.show()

        title = info.get('title', 'Media Preview')
        self.title_overlay.setText(title)
        
        if '_thumb_data' in info:
            pixmap = QPixmap()
            pixmap.loadFromData(info['_thumb_data'])
            self.thumbnail_label.setPixmap(pixmap)
        else:
            self.thumbnail_label.setPixmap(QPixmap())

        self.duration = float(info.get('duration') or 1.0)
        self.start_trim = 0.0
        self.end_trim = self.duration

        self.range_slider.set_duration(self.duration)
        self.start_input.setText(format_time_str(0))
        self.end_input.setText(format_time_str(self.duration))
        self.time_lbl.setText(f"{format_time_str(0)} / {format_time_str(self.duration)}")

        self.qual_combo.clear()
        formats = info.get('formats', [])
        video_formats = [f for f in formats if f.get('vcodec') not in ('none', None)]
        
        if video_formats:
            seen = set()
            for f in reversed(video_formats):
                h = f.get('height')
                if h and h not in seen:
                    seen.add(h)
                    self.qual_combo.addItem(f"{h}p", f.get('format_id'))
        if self.qual_combo.count() == 0:
            self.qual_combo.addItem("Best", None)

        if info.get('_is_audio_only', False):
            self.btn_mode_vid.setEnabled(False)
            self._set_mode(True)
        else:
            self.btn_mode_vid.setEnabled(True)
            self._set_mode(False)

        stream_url = info.get('_preview_stream')
        if stream_url:
            self.player.setSource(QUrl(stream_url))
            self.player.play()
            self.btn_play.setIcon(self.icon_pause)

    def _set_mode(self, is_audio):
        self.is_audio_mode = is_audio
        act = f"QPushButton {{ background-color: #333; color: {TEAL_ACCENT}; font-weight: bold; border-radius: 6px; border: none; }}"
        inact = f"QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; font-weight: bold; border-radius: 6px; border: none; }}"
        
        if is_audio:
            self.btn_mode_aud.setStyleSheet(act)
            self.btn_mode_vid.setStyleSheet(inact)
            self.video_widget.hide()
            self.thumbnail_label.show()
            self.title_overlay.raise_()
        else:
            self.btn_mode_vid.setStyleSheet(act)
            self.btn_mode_aud.setStyleSheet(inact)
            self.video_widget.show()
            self.thumbnail_label.hide()
            self.title_overlay.raise_()
            
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

    def _on_player_position_changed(self, ms):
        sec = ms / 1000.0
        self.range_slider.set_position(sec)
        self.time_lbl.setText(f"{format_time_str(sec)} / {format_time_str(self.duration)}")

        if sec >= self.end_trim or sec < self.start_trim:
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
        self._update_est_size()

    def _on_inputs_edited(self):
        s = parse_time_str(self.start_input.text())
        e = parse_time_str(self.end_input.text())
        
        if s < e and e <= self.duration:
            self.start_trim = s
            self.end_trim = e
            self.range_slider.start_time = s
            self.range_slider.end_time = e
            self.range_slider.update()
            self._update_est_size()

    def _update_est_size(self):
        duration_selected = max(1.0, self.end_trim - self.start_trim)
        bitrate_kbps = 320 if self.is_audio_mode else 2500
        
        qual_text = self.qual_combo.currentText()
        if "1080" in qual_text: bitrate_kbps = 4500
        elif "720" in qual_text: bitrate_kbps = 2500
        elif "480" in qual_text: bitrate_kbps = 1200
        elif "360" in qual_text: bitrate_kbps = 750

        size_mb = (bitrate_kbps * 1000 * duration_selected) / (8 * 1024 * 1024)
        self.est_size_lbl.setText(f"Est. size ~{round(size_mb, 1)} MB")

    def _on_download(self):
        if not self.main_win: return
        
        url = self.current_info.get('_url')
        title = self.current_info.get('title', 'Trimmed Media')
        selected_fid = self.qual_combo.currentData()
        
        item = self.main_win.DownloadItem(
            url=url,
            title=f"[Trimmed] {title}",
            channel=self.current_info.get('uploader', 'YouTube'),
            thumb_path=None,
            out_dir=str(Path(__file__).resolve().parent / "downloads"),
            is_audio=self.is_audio_mode,
            selected_fid=selected_fid,
            duration_str=format_time_str(self.end_trim - self.start_trim),
            site_name="YouTube",
            start_time=self.start_trim,
            end_time=self.end_trim
        )
        
        self.main_win._dl_items.append(item)
        self.main_win._start_download(item)
        self.main_win._show_tab(2)
        self._show_initial_view()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TrimLoadTab()
    window.resize(900, 600)
    window.show()
    sys.exit(app.exec())