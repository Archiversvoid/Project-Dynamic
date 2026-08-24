# universal_scraper.py
# --------------------
# Handles ALL non-YouTube URLs using yt-dlp's built-in extractor system.
# Inspired by dsymbol/yt-dlp-gui — uses --progress-template with __SEP__
# delimiters instead of fragile regex parsing of mixed stderr/stdout.
#
# yt-dlp handles 1800+ sites automatically via site-specific extractors
# and a GenericIE fallback. We don't need YouTube-specific flags here.

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Iterator

_YT_DOMAINS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "music.youtube.com", "m.youtube.com",
)

PROGRESS_SEP = "__SEP__"


def is_youtube_url(url: str) -> bool:
    return any(d in url.lower() for d in _YT_DOMAINS)


def _find_binary(name: str):
    script_dir = Path(__file__).resolve().parent
    for candidate in [name, name + ".exe"]:
        p = script_dir / candidate
        if p.exists():
            return str(p)
    return shutil.which(name)


def _ytdlp() -> str:
    p = _find_binary("yt-dlp")
    if not p:
        raise FileNotFoundError("yt-dlp not found.")
    return p


def _ffprobe_args() -> list:
    p = _find_binary("ffprobe")
    if p:
        return ["--ffprobe-location", p]
    return []


def _cookie_args() -> list:
    cookie_file = Path(__file__).resolve().parent / "session_cookies.txt"
    if cookie_file.exists():
        return ["--cookies", str(cookie_file)]
    return []


def _no_window() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _size_label(size_bytes, duration_sec, tbr, abr) -> str:
    if not size_bytes and duration_sec:
        rate = tbr or abr or 0
        if rate:
            size_bytes = int(duration_sec * rate * 1000 / 8)
    if not size_bytes:
        return ""
    if size_bytes < 1024 * 1024:
        return f"~{max(1, round(size_bytes / 1024))} KB"
    return f"~{round(size_bytes / (1024 * 1024), 1)} MB"


_FALLBACK_VIDEO = [
    {"label": "1080p", "format_id": None},
    {"label": "720p",  "format_id": None},
    {"label": "480p",  "format_id": None},
    {"label": "360p",  "format_id": None},
]
_FALLBACK_AUDIO = [
    {"label": f"MP3 ({br}kbps)", "format_id": None}
    for br in (320, 256, 192, 128, 96, 64)
]


def _parse_video_formats(formats, duration_sec):
    video = [
        f for f in formats
        if f.get("height") and f.get("vcodec") not in (None, "none")
        and f.get("ext") not in ("mhtml",)
    ]
    video.sort(key=lambda f: (f.get("height", 0), f.get("fps") or 0,
                               f.get("tbr") or 0), reverse=True)
    seen, result = set(), []
    for f in video:
        height, fps = f.get("height"), f.get("fps") or 0
        if (height, int(fps)) in seen:
            continue
        seen.add((height, int(fps)))
        fps_str = f"{int(fps)}fps" if fps > 30 else ""
        res = f"4K ({height}p)" if height >= 2160 else f"{height}p"
        label_base = " ".join(p for p in [res, fps_str] if p)
        size_str = _size_label(f.get("filesize") or f.get("filesize_approx"),
                                duration_sec, f.get("tbr"), None)
        label = f"{label_base} — {size_str}" if size_str else label_base
        result.append({"label": label, "format_id": f["format_id"]})
    # If only 1 format returned, show it but also offer fallback resolutions
    return result if result else _FALLBACK_VIDEO


def _parse_audio_formats(formats, duration_sec):
    audio = [
        f for f in formats
        if f.get("vcodec") in (None, "none")
        and f.get("acodec") not in (None, "none")
        and f.get("ext") not in ("mhtml",)
    ]
    audio.sort(key=lambda f: f.get("abr") or 0, reverse=True)
    seen, result = set(), []
    for f in audio:
        abr = int(f.get("abr") or 0)
        ext = (f.get("ext") or "m4a").upper()
        if (abr, ext) in seen:
            continue
        seen.add((abr, ext))
        label_base = f"{ext} ({abr}kbps)" if abr else ext
        size_str = _size_label(f.get("filesize") or f.get("filesize_approx"),
                                duration_sec, None, f.get("abr"))
        label = f"{label_base} — {size_str}" if size_str else label_base
        result.append({"label": label, "format_id": f["format_id"]})
    return result or _FALLBACK_AUDIO


def _is_audio_only(formats) -> bool:
    """Return True only if EVERY format is audio-only with no video stream at all.
    Muxed formats (vcodec != none AND acodec != none) count as video-capable."""
    if not formats:
        return False
    for f in formats:
        vcodec = f.get("vcodec") or "none"
        height = f.get("height") or 0
        # Has a real video stream
        if height > 0 or vcodec not in ("none", ""):
            return False
    return True


def scrape_formats(url: str) -> dict:
    """
    Fetch metadata for any non-YouTube URL.
    Uses yt-dlp's built-in extractor — no YouTube-specific flags.
    """
    cmd = [
        _ytdlp(), "--dump-json", "--no-playlist", "--no-warnings", "--quiet",
        "--no-check-certificate",   # PH and some adult sites use cert issues
        "--user-agent", (            # generic UA avoids some site-level blocks
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    ]
    cmd.extend(_ffprobe_args())
    cmd.extend(_cookie_args())
    cmd.append(url)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=60, creationflags=_no_window(),
        )
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Timed out fetching video info"}

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        return {"ok": False, "error": err[:200] or "yt-dlp returned an error"}

    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "Could not parse yt-dlp output"}

    duration = int(info.get("duration") or 0)
    formats = info.get("formats") or []
    audio_only = _is_audio_only(formats)

    return {
        "ok":            True,
        "title":         info.get("title") or info.get("fulltitle") or "Unknown Title",
        "channel":       (info.get("uploader") or info.get("channel")
                          or info.get("creator") or info.get("artist") or "Unknown"),
        "duration":      duration,
        "thumbnail":     info.get("thumbnail"),
        "video_formats": [] if audio_only else _parse_video_formats(formats, duration),
        "audio_formats": _parse_audio_formats(formats, duration),
        "audio_only":    audio_only,
    }


def scrape_download(url: str, format_id=None, out_dir: str = "downloads",
                    is_audio: bool = False, selected_fid=None) -> Iterator[dict]:
    """
    Download from any non-YouTube URL.
    Uses --progress-template with __SEP__ delimiters (dsymbol/yt-dlp-gui approach)
    — clean, reliable, no dual-stream threading needed.
    """
    if selected_fid is not None:
        format_id = selected_fid

    os.makedirs(out_dir, exist_ok=True)

    if is_audio:
        fmt = f"{format_id}/bestaudio/best" if format_id else "bestaudio/best"
        extra = ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "320K"]
    else:
        fmt = (f"{format_id}+bestaudio/bestvideo+bestaudio/best"
               if format_id else "bestvideo+bestaudio/best")
        extra = ["--merge-output-format", "mp4"]

    # --progress-template gives us structured output on stdout.
    # Much more reliable than parsing free-text progress lines.
    progress_tmpl = (
        f"%(progress.status)s{PROGRESS_SEP}"
        f"%(progress._total_bytes_estimate_str)s{PROGRESS_SEP}"
        f"%(progress._percent_str)s{PROGRESS_SEP}"
        f"%(progress._speed_str)s{PROGRESS_SEP}"
        f"%(progress._eta_str)s"
    )

    cmd = [
        _ytdlp(),
        "--no-playlist", "--no-warnings",
        "--newline", "--progress",
        "--progress-template", progress_tmpl,
        "-o", os.path.join(out_dir, "%(title)s.%(ext)s"),
        "-f", fmt,
    ]
    cmd.extend(extra)
    cmd.extend(_ffprobe_args())
    cmd.extend(_cookie_args())
    cmd.append(url)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=_no_window(),
        )
    except FileNotFoundError as e:
        yield {"type": "error", "message": str(e)}
        return

    # stderr reader — only for detecting errors, not progress
    err_lines = []
    def _read_err():
        for line in proc.stderr:
            err_lines.append(line.rstrip())
    threading.Thread(target=_read_err, daemon=True).start()

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue

        if PROGRESS_SEP in line:
            parts = [p.strip() for p in line.split(PROGRESS_SEP)]
            if len(parts) >= 5:
                status, total_bytes, percent_str, speed, eta = parts[:5]
                try:
                    percent = float(percent_str.replace("%", "").strip()) / 100.0
                except (ValueError, AttributeError):
                    percent = 0.0
                yield {
                    "type":    "progress",
                    "percent": min(percent, 1.0),
                    "speed":   speed if speed != "N/A" else "",
                    "eta":     eta if eta != "N/A" else "",
                    "size":    total_bytes if total_bytes != "N/A" else "",
                }
            continue

        if line.startswith(("[Merger]", "[ExtractAudio]", "[ffmpeg]")):
            yield {"type": "merging"}

    proc.wait()

    if proc.returncode == 0:
        yield {"type": "done"}
    else:
        err_msg = "\n".join(err_lines).strip()
        yield {"type": "error", "message": err_msg[:200] or f"yt-dlp exited with code {proc.returncode}"}


# Aliases for compatibility
fetch_formats = scrape_formats
download_video = scrape_download
fetcher_download = scrape_download
download = scrape_download