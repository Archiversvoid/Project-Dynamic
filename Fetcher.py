import sys
import re
import math
import subprocess
import threading
from pathlib import Path

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QLineEdit, QProgressBar, QFrame, 
                             QComboBox, QSizePolicy)
from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QPoint, QPropertyAnimation, QSequentialAnimationGroup, QTimer
from PyQt6.QtGui import QColor, QPainter, QCursor, QFont

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    HAS_MULTIMEDIA = True
except ImportError:
    HAS_MULTIMEDIA = False

try:
    from universal_scraper import fetch_formats as scrape_formats
except ImportError:
    scrape_formats = None

def jiggle_widget(widget):
    if hasattr(widget, "_jiggle_anim") and widget._jiggle_anim.state() == QSequentialAnimationGroup.State.Running:
        return
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

def format_time(seconds):
    h, m = divmod(int(seconds), 3600)
    m, s = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def parse_time(t_str):
    match = re.match(r"^(\d{1,2}):([0-5]\d):([0-5]\d)$", t_str.strip())
    if match:
        return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3))
    return -1

def fetch_formats(url):
    if scrape_formats:
        return scrape_formats(url)
    return {"ok": False, "error": "Scraper function unmapped"}

class TrimSlider(QWidget):
    start_changed = pyqtSignal(float)
    end_changed = pyqtSignal(float)
    seek_changed = pyqtSignal(float)

    def __init__(self, duration, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.duration = max(duration, 1.0)
        self.start_sec = 0.0
        self.end_sec = self.duration
        self.current_sec = 0.0
        self.dragging = None
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def update_positions(self, start, end):
        self.start_sec = max(0.0, min(start, self.duration))
        self.end_sec = max(self.start_sec, min(end, self.duration))
        self.update()

    def update_current(self, current):
        if self.dragging != "seek":
            self.current_sec = max(0.0, min(current, self.duration))
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w, h = self.width(), self.height()
        track_h, track_y = 6, (h - 6) // 2

        painter.setBrush(QColor("#333333"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, track_y, w, track_h, 3, 3)

        x1 = int((self.start_sec / self.duration) * w)
        x2 = int((self.end_sec / self.duration) * w)
        painter.setBrush(QColor("#00BFA5"))
        painter.drawRoundedRect(x1, track_y, max(0, x2 - x1), track_h, 3, 3)

        painter.drawRect(max(0, x1 - 2), track_y - 6, 4, track_h + 12)
        painter.drawRect(min(w - 4, x2 - 2), track_y - 6, 4, track_h + 12)

        seek_x = int((self.current_sec / self.duration) * w)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QPoint(seek_x, h // 2), 7, 7)

    def mousePressEvent(self, event):
        x, w = event.position().x(), self.width()
        x1 = (self.start_sec / self.duration) * w
        x2 = (self.end_sec / self.duration) * w
        seek_x = (self.current_sec / self.duration) * w

        if abs(x - seek_x) < 12: self.dragging = "seek"
        elif abs(x - x1) < 12: self.dragging = "start"
        elif abs(x - x2) < 12: self.dragging = "end"
        else:
            self.dragging = "seek"
            self._handle_mouse_move(x, w)

    def mouseMoveEvent(self, event):
        if self.dragging:
            self._handle_mouse_move(event.position().x(), self.width())

    def mouseReleaseEvent(self, event):
        self.dragging = None

    def _handle_mouse_move(self, x, w):
        val = max(0.0, min((x / w) * self.duration, self.duration))
        if self.dragging == "start":
            self.start_sec = min(val, self.end_sec)
            self.start_changed.emit(self.start_sec)
        elif self.dragging == "end":
            self.end_sec = max(val, self.start_sec)
            self.end_changed.emit(self.end_sec)
        elif self.dragging == "seek":
            self.current_sec = val
            self.seek_changed.emit(self.current_sec)
        self.update()