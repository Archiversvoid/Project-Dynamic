import sys
import os
import json
import threading
import time
import urllib.request
import webbrowser
import hashlib
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QStackedWidget, 
                             QScrollArea, QFrame, QLineEdit, QProgressBar, 
                             QSpacerItem, QSizePolicy, QComboBox, QGraphicsDropShadowEffect)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QObject, QTimer, QUrl
from PyQt6.QtGui import (QFont, QFontDatabase, QIcon, QPixmap, QImage, QColor, 
                         QPainter, QPainterPath, QCursor, QDesktopServices)

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

# ── Colours ──────────────────────────────────────────────────────────────────
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
    "tidal.com", "deezer.com", "music.apple.com",
    "bandcamp.com", "audiomack.com", "reverbnation.com",
)

def _is_audio_only_url(url):
    return any(d in url.lower() for d in _AUDIO_ONLY_DOMAINS)

def _is_youtube_url(url):
    return any(d in url.lower() for d in
               ("youtube.com","youtu.be","youtube-nocookie.com","music.youtube.com","m.youtube.com"))

def _site_name(url):
    try:
        host = urlparse(url).netloc.lower()
        if "music.youtube.com" in host: return "Youtube Music"
        if "youtu.be" in host or "youtube.com" in host: return "Youtube"
        host = host.replace("www.","").replace("m.","").replace("music.","")
        return host.split(".")[0].title()
    except Exception: return "Web"

def _fetch_favicon_sync(url):
    """Fetches favicon in background to avoid blocking the main UI thread."""
    try:
        parsed = urlparse(url)
        host = parsed.netloc or "unknown"
        filepath = favicon_dir / f"{host}.png"
        if filepath.exists():
            return str(filepath)
            
        # Using DuckDuckGo instead of Google to eliminate the invisible padding/borders
        furl = f"https://icons.duckduckgo.com/ip3/{parsed.netloc}.ico"
        req = urllib.request.Request(furl, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as r:
            data = r.read()
        with open(filepath, 'wb') as f:
            f.write(data)
        return str(filepath)
    except Exception:
        return None

def fetch_formats(url):
    try:
        import importlib
        module_name = "Fetcher" if _is_youtube_url(url) else "universal_scraper"
        module = importlib.import_module(module_name)
        for fn in ("fetch_formats","scrape_formats"):
            if hasattr(module, fn):
                return getattr(module, fn)(url)
        return {"ok": False, "error": f"{module_name} has no fetch function"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def fetcher_download(url, selected_fid=None, out_dir=None, is_audio=False):
    try:
        import importlib
        module_name = "Fetcher" if _is_youtube_url(url) else "universal_scraper"
        module = importlib.import_module(module_name)
        candidates = [getattr(module, n, None) for n in ("fetcher_download","scrape_download","download_video","download")]
        fn = next((c for c in candidates if callable(c)), None)
        if not fn: raise AttributeError(f"{module_name} has no download function")
        result = fn(url, selected_fid=selected_fid, out_dir=out_dir, is_audio=is_audio)
        if result is None: return []
        if isinstance(result, list): return result
        if hasattr(result,"__iter__") and not isinstance(result,(str,bytes,dict)): return list(result)
        return [result]
    except Exception as e:
        return [{"type":"error","message":str(e)}]

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

# ── Custom Scroll Area ────────────────────────────────────────────────────────
class ModernScrollArea(QScrollArea):
    """A scroll area that only displays its scrollbar on hover."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QScrollArea { background-color: transparent; }")
        self.verticalScrollBar().setStyleSheet("QScrollBar:vertical { width: 0px; background: transparent; }")
        
    def enterEvent(self, event):
        self.verticalScrollBar().setStyleSheet("""
            QScrollBar:vertical { 
                width: 8px; 
                background: transparent; 
                margin: 0px; 
            }
            QScrollBar::handle:vertical { 
                background-color: #555555; 
                border-radius: 4px; 
                min-height: 30px; 
            }
            QScrollBar::handle:vertical:hover { 
                background-color: #777777; 
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { 
                height: 0px; 
                border: none; 
                background: none; 
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { 
                background: none; 
            }
        """)
        super().enterEvent(event)
        
    def leaveEvent(self, event):
        self.verticalScrollBar().setStyleSheet("QScrollBar:vertical { width: 0px; background: transparent; }")
        super().leaveEvent(event)

# ── Signals ──────────────────────────────────────────────────────────────────
class AppSignals(QObject):
    update_progress = pyqtSignal(object)
    refresh_card = pyqtSignal(object)
    home_result = pyqtSignal(dict, str, object, object)
    home_error = pyqtSignal(str)

# ── Download record ───────────────────────────────────────────────────────────
class DownloadItem:
    _counter = int(time.time())
    def __init__(self, url, title, channel, thumb_path, out_dir, is_audio, selected_fid, duration_str, site_name, thumb_url=None, fav_path=None):
        DownloadItem._counter += 1
        self.id = DownloadItem._counter
        self.url, self.title, self.channel = url, title, channel
        self.thumb_url, self.thumb_path = thumb_url, thumb_path
        self.fav_path = fav_path
        self.out_dir, self.is_audio, self.selected_fid = out_dir, is_audio, selected_fid
        self.duration_str, self.site_name = duration_str, site_name
        
        self.status = "active"
        self.percent = 0.0
        self.speed = ""
        self.eta = ""
        self.downloaded = ""
        self.total_size = ""
        self.error_msg = ""
        self.timestamp = datetime.now().strftime("%H:%M")
        self._thread = None
        self._cancelled = False
        self._trash_ready = False

# ── Main app ──────────────────────────────────────────────────────────────────
class DynamicPC(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dynamic")
        self.resize(1000, 660)
        self.setMinimumSize(860, 560)
        self.setStyleSheet(f"QMainWindow {{ background-color: {BG_DARK}; }}")

        self.signals = AppSignals()
        self.signals.update_progress.connect(self._update_card_progress)
        self.signals.refresh_card.connect(self._refresh_single_card)
        self.signals.home_result.connect(self._render_media_card_ui)
        self.signals.home_error.connect(self._show_error)

        font_path = str(repo_root / "assets" / "Roboto-Regular.ttf")
        if os.path.exists(font_path):
            QFontDatabase.addApplicationFont(font_path)
            
        app_font = QFont("Roboto", 10)
        app_font.setStyleHint(QFont.StyleHint.SansSerif)
        QApplication.setFont(app_font)

        self._dl_items = []
        self._dl_cards = {}
        self._dl_tab_filter = "All"
        self._last_url = ""

        self._load_icons()
        self._load_downloads()
        self._setup_ui()
        self._show_tab(0) 

    def _open_qurl(self, qurl):
        """Helper to open QUrl, explicitly returning None to prevent PyQt6 sipBadCatcherResult crashes"""
        QDesktopServices.openUrl(qurl)

    def _load_icons(self):
        self._icons = {}
        for name in ["home", "scissors", "downloads", "settings", 
                     "pause", "play", "trash", "trash red", "retry", "check"]:
            path = repo_root / "icons" / f"{name}.png"
            if path.exists():
                self._icons[name] = QIcon(str(path))
            else:
                self._icons[name] = QIcon()

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
                            url=d["url"], title=d["title"], channel=d["channel"],
                            thumb_path=d.get("thumb_path"), out_dir=d["out_dir"], 
                            is_audio=d["is_audio"], selected_fid=d["selected_fid"], 
                            duration_str=d["duration_str"], site_name=_site_name(d["url"]), 
                            thumb_url=d.get("thumb_url"), fav_path=fav_p
                        )
                        it.status = d["status"]
                        it.percent = d.get("percent", 0.0)
                        it.total_size = d.get("total_size", "")
                        it.downloaded = d.get("downloaded", "")
                        self._dl_items.append(it)
            except Exception: pass

    def closeEvent(self, event):
        data = []
        for item in self._dl_items:
            data.append({
                "url": item.url, "title": item.title, "channel": item.channel,
                "out_dir": item.out_dir, "is_audio": item.is_audio,
                "selected_fid": item.selected_fid, "duration_str": item.duration_str,
                "status": "paused" if item.status == "active" else item.status,
                "percent": item.percent, "total_size": item.total_size,
                "downloaded": item.downloaded, "thumb_path": item.thumb_path,
                "thumb_url": item.thumb_url, "fav_path": getattr(item, 'fav_path', None)
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

        # Sidebar
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
        nav_items = [("Home", "home", 0), ("TrimLoad", "scissors", 1), 
                     ("Downloads", "downloads", 2), ("Settings", "settings", 3)]
        
        for name, icon_name, idx in nav_items:
            btn = QPushButton(f"  {name}")
            btn.setIcon(self._icons.get(icon_name, QIcon()))
            btn.setIconSize(QSize(22, 22))
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet(f"""
                QPushButton {{
                    text-align: left; padding: 10px; border-radius: 10px;
                    background-color: transparent; color: {TEXT_MUTED}; font-size: 16px; font-weight: 500;
                }}
                QPushButton:hover {{ background-color: #222222; }}
            """)
            btn.clicked.connect(lambda checked, i=idx: self._show_tab(i))
            sidebar_layout.addWidget(btn)
            self.nav_btns.append(btn)
            
        sidebar_layout.addStretch()
        main_layout.addWidget(self.sidebar)

        # Stacked Widget
        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"background-color: {BG_DARK};")
        
        self.tab_home = QWidget()
        self._build_home_tab()
        self.stack.addWidget(self.tab_home)

        self.tab_trim = QWidget()
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
            color = TEXT_MAIN if i == idx else TEXT_MUTED
            btn.setStyleSheet(f"""
                QPushButton {{
                    text-align: left; padding: 10px; border-radius: 10px;
                    background-color: transparent; color: {color}; font-size: 16px; font-weight: 500;
                }}
                QPushButton:hover {{ background-color: #222222; }}
            """)
        if idx == 2: self._refresh_dl_list()

    # ── Home Tab ─────────────────────────────────────────────────────────────────
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
        self.url_entry.setStyleSheet(f"""
            QLineEdit {{
                background-color: {INPUT_BG}; color: {TEXT_MAIN}; border-radius: 27px;
                padding: 0 20px; font-size: 18px; border: none;
            }}
        """)
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
        self.title_lbl.hide()
        self.url_entry.hide()
        self._clear_home_slot()

        lbl = QLabel("Processing link...")
        lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 22px; font-weight: bold;")
        self.home_slot_layout.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        bar = QProgressBar()
        bar.setFixedSize(300, 6)
        bar.setTextVisible(False)
        bar.setRange(0, 0)
        bar.setStyleSheet(f"QProgressBar {{ background-color: #333; border: none; }} QProgressBar::chunk {{ background-color: {TEAL_ACCENT}; }}")
        self.home_slot_layout.addWidget(bar, alignment=Qt.AlignmentFlag.AlignCenter)
        
        cancelled = [False]
        def _cancel():
            cancelled[0] = True
            self.title_lbl.show()
            self.url_entry.show()
            self._clear_home_slot()

        btn = QPushButton("Cancel")
        btn.setFixedSize(120, 36)
        btn.setStyleSheet(f"QPushButton {{ border: 1px solid {TEAL_ACCENT}; border-radius: 18px; color: {TEXT_MAIN}; background: transparent; font-weight: bold; }}")
        btn.clicked.connect(_cancel)
        self.home_slot_layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        def _worker():
            res = fetch_formats(url)
            if cancelled[0]: return
            if not res["ok"]:
                self.signals.home_error.emit(res["error"])
                return

            force_audio = _is_audio_only_url(url) or res.get("audio_only", False)
            info = {
                "title": res["title"], "uploader": res["channel"], "duration": res["duration"],
                "_video_formats": [] if force_audio else res.get("video_formats",[]),
                "_audio_formats": res.get("audio_formats",[]), "_audio_only": force_audio,
                "thumbnail": res.get("thumbnail")
            }
            
            thumb_path = None
            if info["thumbnail"]:
                try:
                    req = urllib.request.Request(info["thumbnail"], headers={"User-Agent":"Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=8) as r:
                        data = r.read()
                    t_hash = hashlib.md5(info["thumbnail"].encode()).hexdigest()
                    thumb_path = str(thumb_dir / f"{t_hash}.png")
                    with open(thumb_path, 'wb') as f: f.write(data)
                except Exception: pass
            
            # Fetch favicon synchronously within this worker thread (won't freeze UI)
            fav_path = _fetch_favicon_sync(url)

            if not cancelled[0]:
                self.signals.home_result.emit(info, url, thumb_path, fav_path)

        threading.Thread(target=_worker, daemon=True).start()

    def _show_error(self, msg):
        self._clear_home_slot()
        lbl = QLabel(f"Error: {msg[:80]}")
        lbl.setStyleSheet(f"color: {ERROR_RED}; font-size: 15px; font-weight: bold;")
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
        dur_sec = info.get("duration", 0)
        audio_only = info.get("_audio_only", False)
        thumb_url = info.get("thumbnail")

        if dur_sec:
            t = int(dur_sec)
            m, s = divmod(t, 60); h, m = divmod(m, 60)
            dur_str = f"{h}:{m:02d}:{s:02d} mins" if h else f"{m}:{s:02d} mins"
        else:
            dur_str = "0:00 mins"

        format_map = {}
        vf = info.get("_video_formats", [])
        af = info.get("_audio_formats", [])
        video_q = [f["label"] for f in vf]
        audio_q = [f["label"] for f in af]
        for f in vf: format_map[f["label"]] = f["format_id"]
        for f in af: format_map[f["label"]] = f["format_id"]
        if not audio_q:
            audio_q = [f"MP3 {br}kbps" for br in (320, 256, 192, 128, 96, 64)]

        site = _site_name(url)

        outer = QFrame()
        outer.setStyleSheet(f"QFrame {{ background-color: {CARD_BG}; border: none; border-radius: 12px; }}")
        o_layout = QVBoxLayout(outer)
        o_layout.setContentsMargins(30, 30, 30, 30)
        o_layout.setSpacing(25)

        # Header: Thumbnail & Meta side-by-side
        hdr_layout = QHBoxLayout()
        hdr_layout.setContentsMargins(0, 0, 0, 0)
        hdr_layout.setSpacing(30)

        thumb_lbl = QLabel()
        thumb_lbl.setFixedSize(320, 180)
        thumb_lbl.setStyleSheet(f"background-color: {CARD_INNER_BG}; border-radius: 8px;")
        if thumb_path and os.path.exists(thumb_path):
            pix = QPixmap(thumb_path).scaled(320, 180, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            thumb_lbl.setPixmap(get_rounded_pixmap(pix, 8))
        hdr_layout.addWidget(thumb_lbl)

        meta_layout = QVBoxLayout()
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(12)
        
        t_lbl = QLabel(title_text)
        t_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 34px; border: none;")
        t_lbl.setWordWrap(True)
        meta_layout.addWidget(t_lbl)
        
        c_lbl = QLabel(channel_text)
        c_lbl.setStyleSheet(f"color: #DDDDDD; font-size: 18px; border: none;")
        meta_layout.addWidget(c_lbl)
        
        dur_site_layout = QHBoxLayout()
        dur_site_layout.setSpacing(10)
        
        d_lbl = QLabel(dur_str)
        d_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 16px; border: none;")
        dur_site_layout.addWidget(d_lbl)
        
        dot_lbl = QLabel(" • ")
        dot_lbl.setStyleSheet(f"color: #DDDDDD; font-size: 16px; border: none;")
        dur_site_layout.addWidget(dot_lbl)
        
        # Apply Favicon if downloaded successfully, fallback to text logo
        if fav_path and os.path.exists(fav_path):
            fav_pix = QPixmap(fav_path)
            fav_lbl = QLabel()
            fav_lbl.setStyleSheet("border: none; background: transparent;")
            fav_lbl.setPixmap(fav_pix.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            dur_site_layout.addWidget(fav_lbl)
        else:
            site_icon = QLabel("▶" if "Youtube" in site else "🌐")
            site_icon.setStyleSheet(f"color: {'#FF0000' if 'Youtube' in site else TEAL_ACCENT}; font-size: 18px; border: none;")
            dur_site_layout.addWidget(site_icon)
            
        # Interactive Original Url Link (Changed to TEXT_MUTED)
        site_lbl = QLabel(site)
        site_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        site_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 16px; border: none;")
        site_lbl.mousePressEvent = lambda e: self._open_qurl(QUrl(url))
        dur_site_layout.addWidget(site_lbl)
        
        dur_site_layout.addStretch()
        
        meta_layout.addLayout(dur_site_layout)
        meta_layout.addStretch()
        
        hdr_layout.addLayout(meta_layout)
        hdr_layout.addStretch()
        o_layout.addLayout(hdr_layout)

        # Segmented Control (Video / Audio Box)
        seg_frame = QFrame()
        seg_frame.setStyleSheet(f"QFrame {{ background-color: #222222; border-radius: 12px; }}")
        seg_frame.setFixedWidth(300)
        seg_layout = QHBoxLayout(seg_frame)
        seg_layout.setContentsMargins(4, 4, 4, 4)
        seg_layout.setSpacing(4)
        
        bv = QPushButton("Video")
        bv.setFixedHeight(40)
        bv.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        
        ba = QPushButton("Audio")
        ba.setFixedHeight(40)
        ba.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        
        def update_btn_styles(mode):
            active_style = f"QPushButton {{ background-color: #333333; color: {TEAL_ACCENT}; font-size: 18px; border-radius: 8px; border: none; }}"
            inactive_style = f"QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; font-size: 18px; border-radius: 8px; border: none; }} QPushButton:hover {{ background-color: #2A2A2A; }}"
            
            if mode == "Video":
                bv.setStyleSheet(active_style)
                ba.setStyleSheet(inactive_style)
            else:
                bv.setStyleSheet(inactive_style)
                ba.setStyleSheet(active_style)
        
        if audio_only:
            bv.setDisabled(True)
            bv.setStyleSheet(f"QPushButton {{ background-color: transparent; color: #444; font-size: 18px; border-radius: 8px; border: none; }}")
            ba.setStyleSheet(f"QPushButton {{ background-color: #333333; color: {TEAL_ACCENT}; font-size: 18px; border-radius: 8px; border: none; }}")
        else:
            update_btn_styles("Video")
        
        seg_layout.addWidget(bv)
        seg_layout.addWidget(ba)
        o_layout.addWidget(seg_frame)

        # Bottom Section (Quality combo left + Buttons right)
        bot_layout = QHBoxLayout()
        bot_layout.setSpacing(40)
        
        # Left side: Quality Label + ComboBox
        qual_layout = QVBoxLayout()
        qual_layout.setSpacing(8)
        
        ql = QLabel("Quality" if not audio_only else "Bitrate")
        ql.setStyleSheet(f"color: #DDDDDD; font-size: 16px; border: none;")
        qual_layout.addWidget(ql)
        
        qm = QComboBox()
        qm.setFixedSize(300, 48)
        qm.setStyleSheet(f"""
            QComboBox {{
                background-color: #222222;
                color: {TEAL_ACCENT};
                font-size: 16px;
                border-radius: 12px;
                padding: 0 15px;
                border: none;
            }}
            QComboBox::drop-down {{
                border: none;
            }}
            QComboBox QAbstractItemView {{
                background-color: {CARD_BG};
                color: {TEXT_MAIN};
                selection-background-color: #333333;
                outline: none;
                border: 1px solid #333333;
                border-radius: 6px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 32px;
                padding-left: 8px;
            }}
            QScrollBar:vertical {{
                width: 8px;
                background: {CARD_BG};
                border: none;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background-color: #555555;
                border-radius: 4px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: {TEAL_ACCENT};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                border: none;
                background: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)
        init_q = audio_q if audio_only else (video_q or ["Best Quality"])
        qm.addItems(init_q)
        qual_layout.addWidget(qm)
        qual_layout.addStretch()
        
        bot_layout.addLayout(qual_layout)
        bot_layout.addStretch()

        # Right side: Download & Cancel Buttons Stacked
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(12)
        
        btn_dl = QPushButton("Download")
        btn_dl.setFixedSize(220, 48)
        btn_dl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_dl.setStyleSheet(f"QPushButton {{ background-color: {TEAL_ACCENT}; color: #000000; font-size: 16px; font-weight: bold; border-radius: 24px; border: none; }} QPushButton:hover {{ background-color: #00A892; }}")
        
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setFixedSize(220, 48)
        btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_cancel.setStyleSheet(f"QPushButton {{ background-color: transparent; border: 2px solid #444444; color: {TEXT_MAIN}; font-size: 16px; font-weight: bold; border-radius: 24px; }} QPushButton:hover {{ background-color: #222222; }}")
        
        btn_layout.addWidget(btn_dl)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addStretch()
        
        bot_layout.addLayout(btn_layout)
        o_layout.addLayout(bot_layout)

        current_mode = ["Audio" if audio_only else "Video"]

        def set_mode(mode):
            if audio_only and mode == "Video": return
            current_mode[0] = mode
            qm.clear()
            if mode == "Video":
                update_btn_styles("Video")
                ql.setText("Quality")
                qm.addItems(video_q or ["Best Quality"])
            else:
                update_btn_styles("Audio")
                ql.setText("Bitrate")
                qm.addItems(audio_q or ["Best Audio"])

        bv.clicked.connect(lambda: set_mode("Video"))
        ba.clicked.connect(lambda: set_mode("Audio"))

        def _cancel():
            self.title_lbl.show()
            self.url_entry.show()
            self.url_entry.clear()
            self._clear_home_slot()
        
        btn_cancel.clicked.connect(_cancel)
        
        def _on_download():
            is_audio = (current_mode[0] == "Audio")
            fid = format_map.get(qm.currentText())
            out_dir = str(repo_root / "downloads")
            os.makedirs(out_dir, exist_ok=True)

            item = DownloadItem(
                url=url, title=title_text, channel=channel_text,
                thumb_path=thumb_path, out_dir=out_dir, is_audio=is_audio, 
                selected_fid=fid, duration_str=dur_str, site_name=site,
                thumb_url=thumb_url, fav_path=fav_path
            )
            self._dl_items.append(item)
            self._start_download(item)
            self._show_tab(2)
            _cancel()
            
        btn_dl.clicked.connect(_on_download)
        
        self.home_slot_layout.addWidget(outer, alignment=Qt.AlignmentFlag.AlignTop)

    # ── Downloads Tab ────────────────────────────────────────────────────────────
    def _build_downloads_tab(self):
        layout = QVBoxLayout(self.tab_dl)
        layout.setContentsMargins(32, 28, 32, 16)

        lbl = QLabel("Downloads")
        lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 32px; font-weight: bold;")
        layout.addWidget(lbl)

        # Filter Tabs Row
        self.filter_container = QFrame()
        self.filter_container.setStyleSheet(f"""
            QFrame {{
                background-color: transparent;
            }}
        """)
        self.filter_layout = QHBoxLayout(self.filter_container)
        self.filter_layout.setContentsMargins(0, 6, 0, 6)
        self.filter_layout.setSpacing(10)
        layout.addWidget(self.filter_container, alignment=Qt.AlignmentFlag.AlignLeft)

        # Swap to the modern hover-activated scroll area
        self.scroll = ModernScrollArea()
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 10, 0, 10)
        self.scroll_layout.setSpacing(16)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll)

    def _build_filters(self):
        while self.filter_layout.count():
            item = self.filter_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
                
        filters = ["All", "Active", "Paused", "Done", "Failed"]
        counts = {
            "All": len(self._dl_items),
            "Active": sum(1 for d in self._dl_items if d.status=="active"),
            "Paused": sum(1 for d in self._dl_items if d.status=="paused"),
            "Done": sum(1 for d in self._dl_items if d.status=="done"),
            "Failed": sum(1 for d in self._dl_items if d.status=="failed"),
        }

        for i, f_name in enumerate(filters):
            is_active = (self._dl_tab_filter == f_name)
            
            btn_frame = QFrame()
            btn_frame.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            bg_col = TEAL_ACCENT if is_active else "transparent"
            btn_frame.setStyleSheet(f"QFrame {{ background-color: {bg_col}; border-radius: 14px; }}")
            
            h = QHBoxLayout(btn_frame)
            h.setContentsMargins(12, 4, 12, 4)
            h.setSpacing(6)
            
            text_lbl = QLabel(f_name)
            t_col = "#FFFFFF" if is_active else TEXT_MUTED
            text_lbl.setStyleSheet(f"color: {t_col}; font-size: 14px; font-weight: {'bold' if is_active else 'normal'};")
            h.addWidget(text_lbl)
            
            badge = QLabel(str(min(counts[f_name], 99)))
            badge.setFixedSize(20, 20)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            b_bg = "#FFFFFF" if is_active else "#333333"
            b_col = "#000000" if is_active else TEXT_MUTED
            badge.setStyleSheet(f"background-color: {b_bg}; color: {b_col}; border-radius: 10px; font-size: 11px; font-weight: bold;")
            h.addWidget(badge)
            
            btn_frame.mousePressEvent = lambda e, f=f_name: self._set_dl_filter(f)
            self.filter_layout.addWidget(btn_frame)
            
            if i < len(filters) - 1:
                sep = QLabel("|")
                sep.setStyleSheet(f"color: #333333; font-size: 16px;")
                self.filter_layout.addWidget(sep)

    def _set_dl_filter(self, f):
        self._dl_tab_filter = f
        self._refresh_dl_list()

    def _refresh_dl_list(self):
        self._build_filters()
        
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            
        filtered = [d for d in reversed(self._dl_items) if self._dl_tab_filter=="All" or d.status==self._dl_tab_filter.lower()]
        
        for item in filtered:
            self.scroll_layout.addWidget(self._build_dl_card_widget(item))
            
        if not filtered:
            empty = QLabel("No downloads here")
            empty.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 15px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.scroll_layout.addWidget(empty)
            
        self.scroll_layout.addStretch()

    def _build_dl_card_widget(self, item: DownloadItem):
        card = QFrame()
        card.setFixedHeight(180)
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {CARD_BG};
                border: 1px solid #2A2A2A;
                border-radius: 14px;
            }}
        """)
        main_h = QHBoxLayout(card)
        main_h.setContentsMargins(16, 16, 16, 16)
        main_h.setSpacing(20)

        # Thumbnail
        thumb_lbl = QLabel()
        thumb_lbl.setFixedSize(240, 135)
        thumb_lbl.setStyleSheet(f"background-color: {CARD_INNER_BG}; border-radius: 8px; border: none;")
        if item.thumb_path and os.path.exists(item.thumb_path):
            pix = QPixmap(item.thumb_path).scaled(240, 135, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            thumb_lbl.setPixmap(get_rounded_pixmap(pix, 8))
        main_h.addWidget(thumb_lbl)

        # Right Content
        right_v = QVBoxLayout()
        right_v.setContentsMargins(0, 0, 0, 0)
        right_v.setSpacing(4)
        
        # Title & Trash
        title_h = QHBoxLayout()
        title_lbl = QLabel(item.title)
        title_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 20px; font-weight: bold; border: none;")
        title_lbl.setWordWrap(True)
        title_h.addWidget(title_lbl, 1)

        trash_btn = QPushButton()
        trash_btn.setIcon(self._icons.get("trash", QIcon()))
        trash_btn.setIconSize(QSize(22, 22))
        trash_btn.setFixedSize(32, 32)
        trash_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        trash_btn.setStyleSheet(f"QPushButton {{ background: transparent; border: none; }} QPushButton:hover {{ background: #2A1A1A; border-radius: 8px; }}")
        
        def _trash_click():
            if item._trash_ready:
                if item in self._dl_items: self._dl_items.remove(item)
                item._cancelled = True
                self._refresh_dl_list()
            else:
                item._trash_ready = True
                trash_btn.setIcon(self._icons.get("trash red", QIcon()))
                QTimer.singleShot(3000, lambda: _reset_trash())
                
        def _reset_trash():
            item._trash_ready = False
            try:
                trash_btn.setIcon(self._icons.get("trash", QIcon()))
            except RuntimeError:
                pass # widget might be destroyed

        trash_btn.clicked.connect(_trash_click)
        title_h.addWidget(trash_btn)
        right_v.addLayout(title_h)

        # Channel & Duration & Site Metadata 
        meta_h = QHBoxLayout()
        ch_lbl = QLabel(item.channel)
        ch_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 14px; border: none;")
        meta_h.addWidget(ch_lbl)
        
        dur_lbl = QLabel(f" •  {item.duration_str}")
        dur_lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 14px; border: none;")
        meta_h.addWidget(dur_lbl)
        
        dot_lbl = QLabel(" • ")
        dot_lbl.setStyleSheet(f"color: #DDDDDD; font-size: 14px; border: none;")
        meta_h.addWidget(dot_lbl)

        # Append favicon explicitly if retrieved
        if item.fav_path and os.path.exists(item.fav_path):
            fav_pix = QPixmap(item.fav_path)
            fav_lbl = QLabel()
            fav_lbl.setStyleSheet("border: none; background: transparent;")
            fav_lbl.setPixmap(fav_pix.scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            meta_h.addWidget(fav_lbl)

        # Clickable Origin Url (Changed to TEXT_MUTED)
        site_lbl = QLabel(item.site_name)
        site_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        site_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 14px; border: none;")
        site_lbl.mousePressEvent = lambda e, u=item.url: self._open_qurl(QUrl(u))
        meta_h.addWidget(site_lbl)
        
        meta_h.addStretch()
        right_v.addLayout(meta_h)
        
        right_v.addStretch()

        # Error display logic
        if item.status == "failed":
            err_frame = QFrame()
            err_frame.setStyleSheet(f"background-color: {ERROR_BG}; border: 1px solid #4A2020; border-radius: 8px;")
            err_layout = QVBoxLayout(err_frame)
            err_layout.setContentsMargins(10, 6, 10, 6)
            err_lbl = QLabel(item.error_msg[:120])
            err_lbl.setStyleSheet("color: #FF8888; font-size: 11px; font-family: Courier; border: none;")
            err_layout.addWidget(err_lbl)
            right_v.addWidget(err_frame)

            retry_h = QHBoxLayout()
            retry_h.setContentsMargins(0, 4, 0, 0)
            retry_btn = QPushButton(" Retry")
            retry_btn.setIcon(self._icons.get("retry", QIcon()))
            retry_btn.setIconSize(QSize(18, 18))
            retry_btn.setFixedSize(90, 32)
            retry_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            retry_btn.setStyleSheet(f"QPushButton {{ background-color: #2A2A2A; color: {TEXT_MAIN}; font-weight: bold; border-radius: 16px; border: none; }} QPushButton:hover {{ background-color: #333333; }}")
            
            def _retry():
                item.status = "active"
                item.error_msg = ""
                item.percent = 0.0
                self._start_download(item)
                self._refresh_single_card(item)

            retry_btn.clicked.connect(_retry)
            retry_h.addWidget(retry_btn)
            retry_h.addStretch()
            right_v.addLayout(retry_h)
            
        else:
            # Progress Section for active/paused/done
            prog_h = QHBoxLayout()
            prog_h.setSpacing(12)
            
            ctrl_btn = QPushButton()
            ctrl_btn.setFixedSize(36, 36)
            ctrl_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            ctrl_btn.setStyleSheet(f"QPushButton {{ background: transparent; border: none; }} QPushButton:hover {{ background: #252525; border-radius: 8px; }}")
            
            if item.status == "active":
                ctrl_btn.setIcon(self._icons.get("pause", QIcon()))
                ctrl_btn.setIconSize(QSize(24, 24))
                def _pause():
                    item.status = "paused"
                    item._cancelled = True
                    item.speed = "--"
                    item.eta = "--"
                    self._refresh_single_card(item)
                ctrl_btn.clicked.connect(_pause)
            elif item.status == "paused":
                ctrl_btn.setIcon(self._icons.get("play", QIcon()))
                ctrl_btn.setIconSize(QSize(24, 24))
                def _resume():
                    item.status = "active"
                    item._cancelled = False
                    item.speed = "Resuming..."
                    item.eta = "--"
                    self._start_download(item)
                    self._refresh_single_card(item)
                ctrl_btn.clicked.connect(_resume)
            elif item.status == "done":
                ctrl_btn.setIcon(self._icons.get("check", QIcon()))
                ctrl_btn.setIconSize(QSize(24, 24))
            
            prog_h.addWidget(ctrl_btn)

            bar_v = QVBoxLayout()
            bar_v.setSpacing(6)
            
            pb = QProgressBar()
            pb.setFixedHeight(4)
            pb.setTextVisible(False)
            pb.setMaximum(1000)
            pb.setValue(int(item.percent * 1000))
            pb.setStyleSheet(f"""
                QProgressBar {{ background-color: #333333; border: none; border-radius: 2px; }}
                QProgressBar::chunk {{ background-color: {TEAL_ACCENT}; border-radius: 2px; }}
            """)
            bar_v.addWidget(pb)
            card.prog_bar = pb

            stats_h = QHBoxLayout()
            dl_str = item.downloaded or "0MB"
            tot_str = item.total_size or "Unknown"
            pct_val = item.percent * 100
            spd = item.speed or "0Mb/s"
            eta = item.eta or "--:--"
            
            stat_txt = f"{dl_str} / {tot_str} ({pct_val:.1f}%)   {spd}   ETA: {eta}"
            if item.status == "done": stat_txt = "Download complete"
            elif item.status == "paused": stat_txt = f"Paused - {int(item.percent*100)}%"
            
            stat_lbl = QLabel(stat_txt)
            stat_col = TEAL_ACCENT if item.status == "done" else (TEXT_MAIN if item.status == 'active' else TEXT_MUTED)
            stat_weight = "bold" if item.status == "done" else "normal"
            stat_lbl.setStyleSheet(f"color: {stat_col}; font-size: 12px; font-weight: {stat_weight}; border: none;")
            stats_h.addWidget(stat_lbl)
            card.stat_lbl = stat_lbl

            path_lbl = QLabel(item.out_dir)
            path_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            path_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; border: none;")
            path_lbl.mousePressEvent = lambda e: self._open_qurl(QUrl.fromLocalFile(item.out_dir))
            stats_h.addWidget(path_lbl, alignment=Qt.AlignmentFlag.AlignRight)
            
            bar_v.addLayout(stats_h)
            prog_h.addLayout(bar_v)
            right_v.addLayout(prog_h)

        main_h.addLayout(right_v)
        self._dl_cards[item.id] = card
        return card

    def _start_download(self, item: DownloadItem):
        def _worker():
            evts = fetcher_download(item.url, item.selected_fid, item.out_dir, item.is_audio)
            for evt in evts:
                if item._cancelled: break
                t = evt.get("type")
                if t == "progress":
                    item.percent, item.speed, item.eta = evt.get("percent",0), evt.get("speed",""), evt.get("eta","")
                    item.downloaded, item.total_size = evt.get("downloaded",""), evt.get("size","")
                    self.signals.update_progress.emit(item)
                elif t == "done":
                    item.status, item.percent = "done", 1.0
                    self.signals.refresh_card.emit(item)
                elif t == "error":
                    item.status, item.error_msg = "failed", evt.get("message","Unknown error")
                    self.signals.refresh_card.emit(item)
        item._thread = threading.Thread(target=_worker, daemon=True)
        item._thread.start()

    def _update_card_progress(self, item):
        if item.id not in self._dl_cards: return
        card = self._dl_cards[item.id]
        if hasattr(card, "prog_bar"): card.prog_bar.setValue(int(item.percent * 1000))
        if hasattr(card, "stat_lbl"):
            dl_str, tot_str = item.downloaded or "0MB", item.total_size or "Unknown"
            pct_val, spd, eta = item.percent * 100, item.speed or "0Mb/s", item.eta or "--:--"
            txt = f"{dl_str} / {tot_str} ({pct_val:.1f}%)   {spd}   ETA: {eta}"
            if item.status == "done": txt = "Download complete"
            elif item.status == "paused": txt = f"Paused - {int(item.percent*100)}%"
            card.stat_lbl.setText(txt)

    def _refresh_single_card(self, item):
        if item.id in self._dl_cards:
            self._dl_cards[item.id].deleteLater()
            del self._dl_cards[item.id]
        self._refresh_dl_list()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DynamicPC()
    window.show()
    sys.exit(app.exec())