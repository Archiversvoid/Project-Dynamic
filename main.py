import math
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import urllib.request
from io import BytesIO
import customtkinter as ctk
from PIL import Image

try:
    import yt_dlp
except Exception:
    yt_dlp = None

# ---------------------------------------------------------------------
# Repository Path Setup
# ---------------------------------------------------------------------
repo_root = Path(__file__).resolve().parent
src_dir = repo_root / "src"
if src_dir.exists():
    sys.path.insert(0, str(src_dir))

DownloadManager = None
wrapper_download = None

try:
    from ytdlp_gui.core.download_manager import DownloadManager as _DM
    from ytdlp_gui.core import ytdlp_wrapper as _wrapper_mod

    DownloadManager = _DM
    wrapper_download = _wrapper_mod.download
except Exception:
    try:
        import ytdlp_wrapper as _local_wrapper
        wrapper_download = _local_wrapper.download
    except Exception:
        wrapper_download = None


class SimpleSettings:
    def __init__(self):
        self.settings_dir = Path.home() / ".yt-dlp-gui"

    def get(self, key, default=None):
        return default

    def get_output_directory(self):
        out = repo_root / "downloads"
        out.mkdir(parents=True, exist_ok=True)
        return str(out)


# ======================================================================
# UI INITIALIZATION & THEME SETUP
# ======================================================================
ctk.set_appearance_mode("Dark")

NEON_GREEN = "#0FFF92"
TEAL_ACCENT = "#00BFA5"
BG_DARK = "#121212"
SIDEBAR_DARK = "#181818"
CARD_BG = "#1A1A1A"
CARD_INNER_BG = "#282828"
INPUT_BG = "#333333"
TEXT_MAIN = "#FFFFFF"
TEXT_MUTED = "#8A8A8A"

USE_FETCHER = False  # Set to True if fetch_formats is available


def _find_js_engine():
    """Detect Node.js or Deno installed on the system to execute JS signature challenges."""
    script_dir = Path(__file__).resolve().parent
    bundled_deno = script_dir / "deno.exe"
    bundled_node = script_dir / "node.exe"

    if bundled_node.exists():
        return "node", str(bundled_node)
    if bundled_deno.exists():
        return "deno", str(bundled_deno)

    node_path = shutil.which("node") or shutil.which("node.exe")
    if node_path:
        return "node", node_path

    deno_path = shutil.which("deno") or shutil.which("deno.exe")
    if deno_path:
        return "deno", deno_path

    return None, None


def _get_cookies_args():
    cookie_file = Path(__file__).resolve().parent / "session_cookies.txt"
    if cookie_file.exists():
        return {"cookiefile": str(cookie_file)}

    firefox_path = Path.home() / "AppData" / "Roaming" / "Mozilla" / "Firefox"
    if firefox_path.exists():
        return {"cookiesfrombrowser": ("firefox",)}

    return {}


def fetch_formats(url):
    """Fetch video formats using a local fetcher module or return a safe error."""
    try:
        import importlib

        module = importlib.import_module("fetcher")
        if hasattr(module, "fetch_formats"):
            return module.fetch_formats(url)
        return {"ok": False, "error": "fetcher module does not define fetch_formats()"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fetcher_download(url, selected_fid=None, out_dir=None, is_audio=False):
    """Backward-compatible bridge for fetcher download helpers."""
    try:
        import importlib

        module = importlib.import_module("fetcher")
        candidates = [
            getattr(module, "fetcher_download", None),
            getattr(module, "download", None),
            getattr(module, "download_video", None),
        ]
        fn = next((c for c in candidates if callable(c)), None)
        if fn is None:
            raise AttributeError("fetcher module does not define a download function")

        result = fn(url, selected_fid=selected_fid, out_dir=out_dir, is_audio=is_audio)
        if result is None:
            return []
        if isinstance(result, list):
            return result
        if hasattr(result, "__iter__") and not isinstance(result, (str, bytes, dict)):
            return list(result)
        return [result]
    except Exception as e:
        return [{"type": "error", "message": str(e)}]


class DyanmicPC(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Dynamic")
        self.geometry("960x620")
        self.resizable(True, True)
        self.configure(fg_color=BG_DARK)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._load_icons()
        self._build_sidebar()
        self._build_main_container()

        if DownloadManager:
            try:
                self._settings = SimpleSettings()
                self.download_manager = DownloadManager(self._settings)
            except Exception as e:
                self.download_manager = None
                print("Failed to create DownloadManager:", e)
        else:
            self.download_manager = None

        self._select_tab("Home")

    def _load_icons(self):
        def get_icon(path):
            if os.path.exists(path):
                try:
                    return ctk.CTkImage(
                        light_image=Image.open(path),
                        dark_image=Image.open(path),
                        size=(22, 22),
                    )
                except Exception:
                    return None
            return None

        self.icon_home = get_icon("icons/home.png")
        self.icon_trim = get_icon("icons/scissors.png")
        self.icon_downloads = get_icon("icons/downloads.png")
        self.icon_settings = get_icon("icons/settings.png")

    def _build_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(
            self, width=210, corner_radius=0, fg_color=SIDEBAR_DARK, border_width=0
        )
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_propagate(False)

        self.menu_btn = ctk.CTkLabel(
            self.sidebar_frame, text="≡", text_color=TEXT_MUTED, font=ctk.CTkFont(size=28)
        )
        self.menu_btn.grid(row=0, column=0, padx=25, pady=(20, 30), sticky="w")

        btn_style = {
            "anchor": "w",
            "height": 45,
            "corner_radius": 0,
            "fg_color": "transparent",
            "text_color": TEXT_MUTED,
            "hover_color": "#222222",
            "font": ctk.CTkFont(size=18),
        }

        self.btn_home = ctk.CTkButton(
            self.sidebar_frame, text="  Home", image=self.icon_home, command=lambda: self._select_tab("Home"), **btn_style
        )
        self.btn_home.grid(row=1, column=0, padx=10, pady=5, sticky="ew")

        self.btn_trim = ctk.CTkButton(
            self.sidebar_frame, text="  TrimLoad", image=self.icon_trim, command=lambda: self._select_tab("TrimLoad"), **btn_style
        )
        self.btn_trim.grid(row=2, column=0, padx=10, pady=5, sticky="ew")

        self.btn_downloads = ctk.CTkButton(
            self.sidebar_frame, text="  Downloads", image=self.icon_downloads, command=lambda: self._select_tab("Downloads"), **btn_style
        )
        self.btn_downloads.grid(row=3, column=0, padx=10, pady=5, sticky="ew")

        self.btn_settings = ctk.CTkButton(
            self.sidebar_frame, text="  Settings", image=self.icon_settings, command=lambda: self._select_tab("Settings"), **btn_style
        )
        self.btn_settings.grid(row=4, column=0, padx=10, pady=5, sticky="ew")

    def _build_main_container(self):
        self.main_container = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_DARK)
        self.main_container.grid(row=0, column=1, sticky="nsew")
        self.main_container.grid_columnconfigure(0, weight=1)
        self.main_container.grid_rowconfigure(0, weight=1)

    def _select_tab(self, tab_name):
        inactive_style = {"fg_color": "transparent", "text_color": TEXT_MUTED}
        active_style = {"fg_color": "transparent", "text_color": TEXT_MAIN}

        self.btn_home.configure(**inactive_style)
        self.btn_trim.configure(**inactive_style)
        self.btn_downloads.configure(**inactive_style)
        self.btn_settings.configure(**inactive_style)

        for widget in self.main_container.winfo_children():
            widget.destroy()

        if tab_name == "Home":
            self.btn_home.configure(**active_style)
            self._render_home_view()
        elif tab_name == "TrimLoad":
            self.btn_trim.configure(**active_style)
            self._render_placeholder("TrimLoad Tools")
        elif tab_name == "Downloads":
            self.btn_downloads.configure(**active_style)
            self._render_placeholder("Downloads Library")
        elif tab_name == "Settings":
            self.btn_settings.configure(**active_style)
            self._render_placeholder("Settings")

    def _render_home_view(self):
        # Anchor the main box directly in the middle using relx and rely
        self.home_center_box = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.home_center_box.place(relx=0.5, rely=0.5, anchor="center")

        self.title_label = ctk.CTkLabel(
            self.home_center_box, text="DYNAMIC", font=ctk.CTkFont(family="Roboto", size=68, weight="normal"), text_color=TEXT_MAIN
        )
        self.title_label.pack(pady=(0, 25))

        self.input_container = ctk.CTkFrame(self.home_center_box, fg_color="transparent", width=600, height=54)
        self.input_container.pack()
        self.input_container.pack_propagate(False)

        self.url_entry = ctk.CTkEntry(
            self.input_container,
            placeholder_text="Enter link...",
            placeholder_text_color="#AAAAAA",
            height=54,
            corner_radius=27,
            fg_color=INPUT_BG,
            border_width=0,
            text_color=TEXT_MAIN,
            font=ctk.CTkFont(family="Roboto", size=18),
        )
        self.url_entry.pack(fill="both", expand=True)
        self.url_entry.bind("<Return>", lambda e: self.process_link(self.url_entry.get().strip()))

        self.content_slot = ctk.CTkFrame(self.home_center_box, fg_color="transparent")
        self.content_slot.pack(fill="x")

    def _render_placeholder(self, title_text):
        label = ctk.CTkLabel(
            self.main_container, text=title_text, font=ctk.CTkFont(family="Roboto", size=24, weight="bold"), text_color=TEXT_MAIN
        )
        label.pack(padx=40, pady=40, anchor="w")

    def process_link(self, url):
        if not url:
            return

        self.title_label.pack_forget()
        self.input_container.pack_forget()

        for widget in self.content_slot.winfo_children():
            widget.destroy()

        processing_box = ctk.CTkFrame(self.content_slot, fg_color="transparent")
        processing_box.pack(fill="x", pady=20)

        p_status = ctk.CTkLabel(
            processing_box, text="Processing link...", font=ctk.CTkFont(size=22, weight="bold"), text_color=TEXT_MAIN
        )
        p_status.pack(pady=(10, 15))

        processing_bar = ctk.CTkProgressBar(
            processing_box,
            width=300,
            height=6,
            progress_color=TEAL_ACCENT,
            fg_color="#333333",
            mode="indeterminate"
        )
        processing_bar.pack(pady=(0, 15))
        processing_bar.start()

        # Cancellation state tracker
        is_cancelled = False

        def cancel_processing():
            nonlocal is_cancelled
            is_cancelled = True
            processing_bar.stop()
            self._reset_home_view()

        btn_cancel_processing = ctk.CTkButton(
            processing_box,
            text="Cancel",
            width=120,
            height=36,
            corner_radius=18,
            fg_color="transparent",
            border_width=1,
            border_color=TEAL_ACCENT,
            text_color=TEXT_MAIN,
            hover_color="#222222",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=cancel_processing,
        )
        btn_cancel_processing.pack(pady=(0, 10))

        def fetch_worker():
            if USE_FETCHER:
                result = fetch_formats(url)
                if is_cancelled:
                    return
                if not result["ok"]:
                    age_info = result.get("age_gate")
                    if age_info and not age_info.get("has_cookies"):
                        self.after(0, lambda ai=age_info: (processing_bar.stop(), self._show_age_gate_panel(ai)))
                    else:
                        self.after(0, lambda e=result["error"]: (processing_bar.stop(), self._show_inline_error(e)))
                    return
                info = {
                    "title": result["title"],
                    "uploader": result["channel"],
                    "duration": result["duration"],
                    "thumbnail": result["thumbnail"],
                    "_video_formats": result["video_formats"],
                    "_audio_formats": result["audio_formats"],
                }
            else:
                info = None
                last_err = None
                opts = {
                    "skip_download": True, "quiet": True, "no_warnings": True,
                    "extractor_args": {"youtube": {
                        "player_client": ["tv_embedded"],
                        "formats": ["missing_pot"]}},
                }
                try:
                    if yt_dlp:
                        with yt_dlp.YoutubeDL(opts) as ydl:
                            info = ydl.extract_info(url, download=False)
                except Exception as e:
                    last_err = e

                if is_cancelled:
                    return

                if info is None:
                    err_msg = str(last_err) if last_err else "Failed to fetch video details."
                    self.after(0, lambda: (processing_bar.stop(), self._show_inline_error(err_msg)))
                    return

            thumb_img = None
            thumb_url = info.get("thumbnail")
            if thumb_url:
                try:
                    req = urllib.request.Request(
                        thumb_url,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                    )
                    with urllib.request.urlopen(req) as resp:
                        data = resp.read()
                        img_obj = Image.open(BytesIO(data))
                        thumb_img = ctk.CTkImage(light_image=img_obj, dark_image=img_obj, size=(300, 168))
                except Exception:
                    pass

            if is_cancelled:
                return

            self.after(0, lambda: (processing_bar.stop(), self._render_media_card_ui(url, info, thumb_img)))

        threading.Thread(target=fetch_worker, daemon=True).start()

    def _show_age_gate_panel(self, age_info: dict):
        """Replace content_slot with age-gate setup instructions."""
        for w in self.content_slot.winfo_children():
            w.destroy()

        outer = ctk.CTkFrame(self.content_slot, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(
            outer,
            text=age_info.get("title", "Age-Restricted Video"),
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=TEXT_MAIN,
        ).pack(anchor="w", pady=(0, 4))

        ctk.CTkLabel(
            outer,
            text=age_info.get("subtitle", "One-time cookie setup required."),
            font=ctk.CTkFont(size=13),
            text_color=TEXT_MUTED,
        ).pack(anchor="w", pady=(0, 16))

        for i, step in enumerate(age_info.get("steps", []), 1):
            row = ctk.CTkFrame(outer, fg_color=CARD_BG, corner_radius=10)
            row.pack(fill="x", pady=4, ipady=8)
            ctk.CTkLabel(
                row, text=str(i),
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=TEAL_ACCENT, width=28,
            ).pack(side="left", padx=(14, 8))
            ctk.CTkLabel(
                row, text=step,
                font=ctk.CTkFont(size=12),
                text_color=TEXT_MAIN, justify="left",
                anchor="w", wraplength=520,
            ).pack(side="left", fill="x", expand=True, padx=(0, 14))

        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(fill="x", pady=(18, 0))
        btn_row.grid_columnconfigure((0, 1), weight=1)

        def _open_ext():
            import webbrowser
            webbrowser.open(age_info.get("extension_url", "https://youtube.com"))

        ctk.CTkButton(
            btn_row,
            text=f"Get Extension  ({age_info.get('browser', 'Browser')})",
            height=44, corner_radius=22,
            fg_color=TEAL_ACCENT, hover_color="#00A892", text_color="#000000",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=_open_ext,
        ).grid(row=0, column=0, padx=(0, 8), sticky="ew")

        ctk.CTkButton(
            btn_row, text="Back",
            height=44, corner_radius=22,
            fg_color="transparent", border_width=1, border_color=TEAL_ACCENT,
            hover_color="#222222", text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._reset_home_view,
        ).grid(row=0, column=1, padx=(8, 0), sticky="ew")

    def _show_inline_error(self, err_text):
        for widget in self.content_slot.winfo_children():
            widget.destroy()

        err_box = ctk.CTkFrame(self.content_slot, fg_color="transparent")
        err_box.pack(fill="x", pady=20)

        lbl_err = ctk.CTkLabel(
            err_box, text=f"Error: {err_text[:70]}", font=ctk.CTkFont(size=16, weight="bold"), text_color="#FF5555", wraplength=480
        )
        lbl_err.pack(pady=(0, 15))

        btn_retry = ctk.CTkButton(
            err_box,
            text="Try Again",
            height=38,
            corner_radius=19,
            fg_color=TEAL_ACCENT,
            hover_color="#00A892",
            text_color="#000000",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._reset_home_view,
        )
        btn_retry.pack()

    def _reset_home_view(self):
        # Unplace and re-render clean home layout
        for widget in self.main_container.winfo_children():
            widget.destroy()
        self._render_home_view()

    def _render_media_card_ui(self, url, info, thumb_img):
        for widget in self.content_slot.winfo_children():
            widget.destroy()

        # Swap center container to fill screen space desktop-style
        self.home_center_box.place_forget()
        
        self.desktop_card_frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.desktop_card_frame.pack(fill="both", expand=True, padx=40, pady=40)

        title_text = info.get("title", "Unknown Title")
        channel_text = info.get("uploader") or info.get("channel") or "Unknown Channel"
        duration_sec = info.get("duration", 0)

        if duration_sec:
            mins, secs = divmod(duration_sec, 60)
            hrs, mins = divmod(mins, 60)
            duration_str = f"{hrs}:{mins:02d}:{secs:02d} mins" if hrs else f"{mins}:{secs:02d} mins"
        else:
            duration_str = "0:00 mins"

        format_map = {}

        def extract_dynamic_qualities(formats, is_audio=False):
            nonlocal format_map

            if is_audio:
                audio_fmts = [f for f in formats if f.get("vcodec") == "none" and (f.get("acodec") != "none" or f.get("abr"))]
                audio_fmts.sort(key=lambda x: x.get("abr") or 0, reverse=True)

                options = []
                for f in audio_fmts:
                    ext = f.get("ext", "m4a").upper()
                    abr = int(f.get("abr", 0)) if f.get("abr") else 0
                    fid = f.get("format_id")

                    size_bytes = f.get("filesize") or f.get("filesize_approx")
                    if size_bytes:
                        size_mb = int(size_bytes / (1024 * 1024))
                    elif duration_sec and abr:
                        size_mb = max(1, int((duration_sec * abr) / 8000))
                    else:
                        size_mb = 0

                    label = f"{ext} ({abr}kbps)" if abr else f"{ext}"
                    if size_mb > 0:
                        label += f"--{size_mb} MB"

                    format_map[label] = fid
                    if label not in options:
                        options.append(label)

                return options if options else ["Best Audio"]

            video_fmts = [f for f in formats if f.get("height") and f.get("vcodec") != "none"]
            video_fmts.sort(
                key=lambda x: (
                    x.get("height", 0),
                    x.get("fps", 0) or 0,
                    x.get("tbr", 0) or 0
                ),
                reverse=True
            )

            options = []
            seen_combos = set()

            for f in video_fmts:
                height = f.get("height")
                fps = f.get("fps")
                fid = f.get("format_id")
                format_note = f.get("format_note", "")
                tbr = f.get("tbr") or 0

                is_premium = "Premium" in format_note or "enhanced" in format_note.lower()

                combo_key = (height, fps, is_premium)
                if combo_key in seen_combos:
                    continue
                seen_combos.add(combo_key)

                fps_str = f"{int(fps)}fps" if fps and fps > 30 else ""
                premium_str = "(Enhanced)" if is_premium else ""
                label_p = f"4K ({height}p)" if height >= 2160 else f"{height}p"

                parts = [p for p in [label_p, fps_str, premium_str] if p]
                res_title = " ".join(parts)

                size_bytes = f.get("filesize") or f.get("filesize_approx")
                if size_bytes:
                    size_mb = int(size_bytes / (1024 * 1024))
                elif duration_sec and tbr:
                    size_mb = max(1, int((duration_sec * tbr) / 8000))
                else:
                    size_mb = 0

                label = f"{res_title}--{size_mb} MB" if size_mb > 0 else res_title

                format_map[label] = fid
                options.append(label)

            return options if options else ["Best Quality"]

        if info.get("_video_formats"):
            _vf = info["_video_formats"]
            _af = info["_audio_formats"]
            video_qualities = [f["label"] for f in _vf]
            audio_qualities = [f["label"] for f in _af]
            for f in _vf:
                format_map[f["label"]] = f["format_id"]
            for f in _af:
                format_map[f["label"]] = f["format_id"]
        else:
            formats = info.get("formats", []) if info else []
            video_qualities = extract_dynamic_qualities(formats, is_audio=False)
            audio_qualities = extract_dynamic_qualities(formats, is_audio=True)

        card_container = ctk.CTkFrame(
            self.desktop_card_frame, fg_color=CARD_BG, corner_radius=18, border_width=1, border_color="#2A2A2A"
        )
        card_container.pack(fill="both", expand=True)

        # Header: Thumbnail LEFT, Details RIGHT
        header_row = ctk.CTkFrame(card_container, fg_color="transparent")
        header_row.pack(fill="x", padx=24, pady=(24, 16))

        if thumb_img:
            thumb_label = ctk.CTkLabel(header_row, image=thumb_img, text="", corner_radius=12)
            thumb_label.pack(side="left")
        else:
            thumb_placeholder = ctk.CTkFrame(header_row, width=300, height=168, fg_color=CARD_INNER_BG, corner_radius=12)
            thumb_placeholder.pack(side="left")

        info_box = ctk.CTkFrame(header_row, fg_color="transparent")
        info_box.pack(side="left", fill="both", expand=True, padx=(24, 0))

        lbl_title = ctk.CTkLabel(
            info_box, text=title_text, font=ctk.CTkFont(size=24, weight="bold"), text_color=TEXT_MAIN, anchor="w", wraplength=500, justify="left"
        )
        lbl_title.pack(fill="x", pady=(0, 8))

        lbl_channel = ctk.CTkLabel(
            info_box, text=channel_text, font=ctk.CTkFont(size=16), text_color=TEXT_MUTED, anchor="w"
        )
        lbl_channel.pack(fill="x")

        lbl_duration = ctk.CTkLabel(
            info_box, text=duration_str, font=ctk.CTkFont(size=14), text_color=TEXT_MUTED, anchor="w"
        )
        lbl_duration.pack(fill="x", pady=(6, 0))

        # Mode Selection Switcher
        mode_var = ctk.StringVar(value="Video")
        segment_frame = ctk.CTkFrame(card_container, fg_color="transparent")
        segment_frame.pack(fill="x", padx=24, pady=(10, 16))
        segment_frame.grid_columnconfigure((0, 1), weight=1)

        btn_mode_video = ctk.CTkButton(
            segment_frame,
            text="Video",
            fg_color="#2B2B2B",
            text_color=TEAL_ACCENT,
            hover_color="#333333",
            corner_radius=10,
            height=44,
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        btn_mode_video.grid(row=0, column=0, padx=(0, 8), sticky="ew")

        btn_mode_audio = ctk.CTkButton(
            segment_frame,
            text="Audio",
            fg_color="#0A0A0A",
            text_color=TEAL_ACCENT,
            hover_color="#181818",
            corner_radius=10,
            height=44,
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        btn_mode_audio.grid(row=0, column=1, padx=(8, 0), sticky="ew")

        # Quality Title & Dropdown
        lbl_qual = ctk.CTkLabel(card_container, text="Quality", font=ctk.CTkFont(size=14), text_color=TEXT_MUTED, anchor="w")
        lbl_qual.pack(fill="x", padx=24, pady=(0, 6))

        quality_menu = ctk.CTkOptionMenu(
            card_container,
            values=video_qualities if video_qualities else ["Best Quality"],
            fg_color="#121212",
            button_color="#181818",
            button_hover_color="#252525",
            text_color=TEAL_ACCENT,
            dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT_MAIN,
            height=46,
            corner_radius=10,
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        quality_menu.set(video_qualities[0] if video_qualities else "Best Quality")
        quality_menu.pack(fill="x", padx=24, pady=(0, 20))

        def set_mode(mode):
            mode_var.set(mode)
            if mode == "Video":
                btn_mode_video.configure(fg_color="#2B2B2B")
                btn_mode_audio.configure(fg_color="#0A0A0A")
                quality_menu.configure(state="normal", values=video_qualities if video_qualities else ["Best Quality"])
                quality_menu.set(video_qualities[0] if video_qualities else "Best Quality")
            else:
                btn_mode_audio.configure(fg_color="#2B2B2B")
                btn_mode_video.configure(fg_color="#0A0A0A")
                quality_menu.configure(state="normal", values=audio_qualities if audio_qualities else ["Best Audio"])
                quality_menu.set(audio_qualities[0] if audio_qualities else "Best Audio")

        btn_mode_video.configure(command=lambda: set_mode("Video"))
        btn_mode_audio.configure(command=lambda: set_mode("Audio"))

        # Bottom Action Bar
        actions_frame = ctk.CTkFrame(card_container, fg_color="transparent")
        actions_frame.pack(fill="x", side="bottom", padx=24, pady=24)
        actions_frame.grid_columnconfigure((0, 1), weight=1)

        btn_cancel = ctk.CTkButton(
            actions_frame,
            text="Cancel",
            height=48,
            corner_radius=24,
            fg_color="transparent",
            border_width=1,
            border_color=TEAL_ACCENT,
            text_color=TEXT_MAIN,
            hover_color="#222222",
            font=ctk.CTkFont(size=16, weight="bold"),
            command=self._reset_home_view,
        )
        btn_cancel.grid(row=0, column=0, padx=(0, 10), sticky="ew")

        btn_dl = ctk.CTkButton(
            actions_frame,
            text="Download",
            height=48,
            corner_radius=24,
            fg_color=TEAL_ACCENT,
            hover_color="#00A892",
            text_color="#000000",
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        btn_dl.grid(row=0, column=1, padx=(10, 0), sticky="ew")

        progress_bar = ctk.CTkProgressBar(card_container, height=6, progress_color=TEAL_ACCENT, fg_color="#333333")
        dl_status = ctk.CTkLabel(card_container, text="", font=ctk.CTkFont(size=13), text_color=TEXT_MUTED)

        def start_download():
            btn_dl.configure(state="disabled")
            btn_cancel.configure(state="disabled")
            
            progress_bar.pack(fill="x", side="bottom", padx=24, pady=(0, 8))
            progress_bar.set(0)
            dl_status.pack(fill="x", side="bottom", padx=24, pady=(0, 12))

            selected_mode = mode_var.get()
            selected_qual_label = quality_menu.get()
            selected_fid = format_map.get(selected_qual_label)

            def worker():
                try:
                    os.makedirs("downloads", exist_ok=True)
                    if selected_mode == "Audio":
                        fmt_str = f"{selected_fid}+bestaudio/bestaudio/best" if selected_fid else "bestaudio/best"
                        fmt_opts = {
                            "format": fmt_str,
                            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"}]
                        }
                    else:
                        fmt_str = f"{selected_fid}+bestaudio/bestvideo+bestaudio/best" if selected_fid else "bestvideo+bestaudio/best"
                        fmt_opts = {
                            "format": fmt_str,
                            "merge_output_format": "mp4",
                        }

                    def _dl_progress_hook(d):
                        if d.get('status') == 'downloading':
                            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
                            done = d.get('downloaded_bytes', 0)
                            speed = d.get('_speed_str', '')
                            pct = done / total
                            self.after(0, lambda p=pct, s=speed: (
                                progress_bar.set(p),
                                dl_status.configure(text=f"Downloading: {int(p*100)}%  {s}")
                            ))

                    out_dir = str(Path(__file__).resolve().parent / "downloads")
                    is_audio = (selected_mode == "Audio")

                    def _finish(msg, color=None):
                        kw = {"text": msg}
                        if color:
                            kw["text_color"] = color
                        try:
                            dl_status.configure(**kw)
                            btn_cancel.configure(state="normal")
                        except Exception:
                            pass

                    if USE_FETCHER:
                        for evt in fetcher_download(url, selected_fid, out_dir, is_audio=is_audio):
                            if evt["type"] == "progress":
                                pct = evt["percent"]
                                spd = evt.get("speed", "")
                                eta = evt.get("eta", "")
                                eta_str = f"  ETA {eta}" if eta else ""
                                self.after(0, lambda p=pct, s=spd, e=eta_str: (
                                    progress_bar.set(p),
                                    dl_status.configure(text=f"Downloading: {int(p*100)}%  {s}{e}")
                                ))
                            elif evt["type"] == "merging":
                                self.after(0, lambda: (
                                    progress_bar.set(1.0),
                                    dl_status.configure(text="Merging video + audio...")
                                ))
                            elif evt["type"] == "done":
                                self.after(0, lambda: _finish("✔ Download Complete!", TEAL_ACCENT))
                            elif evt["type"] == "age_gate":
                                ai = evt.get("info", {})
                                self.after(0, lambda a=ai: self._show_age_gate_panel(a))
                            elif evt["type"] == "error":
                                msg = evt["message"]
                                self.after(0, lambda m=msg: _finish(f"Error: {m[:80]}", "#FF5555"))
                    else:
                        dl_opts = {
                            "outtmpl": str(Path(out_dir) / "%(title)s.%(ext)s"),
                            "progress_hooks": [_dl_progress_hook],
                            "quiet": True, "no_warnings": True,
                            "extractor_args": {"youtube": {"player_client": ["tv_embedded"],
                                                           "formats": ["missing_pot"]}},
                        }
                        dl_opts.update(fmt_opts)
                        with yt_dlp.YoutubeDL(dl_opts) as ydl:
                            ydl.download([url])
                        self.after(0, lambda: _finish("✔ Download Complete!", TEAL_ACCENT))
                except Exception as e:
                    self.after(0, lambda err=str(e): _finish(f"Error: {err[:80]}", "#FF5555"))
                finally:
                    self.after(0, lambda: btn_dl.configure(state="normal"))

            threading.Thread(target=worker, daemon=True).start()

        btn_dl.configure(command=start_download)


if __name__ == "__main__":
    app = DyanmicPC()
    app.mainloop()