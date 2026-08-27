# universal_scraper.py
# --------------------
# Handles ALL non-YouTube URLs. yt-dlp's 1800+ site extractors + GenericIE
# fallback do the heavy lifting. No YouTube-specific flags here.
#
# Fixes vs previous version:
# - _is_audio_only correctly handles muxed formats (checks height AND vcodec)
# - Hardcoded fallback qualities when site returns 0 formats
# - Normalised audio labels (no MPA/mp4a/m4a confusion)
# - PH/adult sites: referer header + --no-check-certificate
# - --progress-template __SEP__ approach for reliable progress parsing
# - Audio tab always shows real bitrate options or hardcoded fallback list

import json
import os
import queue
import re
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

_ADULT_DOMAINS = (
    "pornhub.com", "xvideos.com", "xhamster.com", "redtube.com",
    "youporn.com", "spankbang.com", "eporner.com", "xnxx.com",
    "tnaflix.com", "tube8.com",
)

_SEP = "__SEP__"

# Hardcoded fallbacks — shown when a site returns no parseable formats
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


def is_youtube_url(url: str) -> bool:
    return any(d in url.lower() for d in _YT_DOMAINS)


def _is_adult_url(url: str) -> bool:
    return any(d in url.lower() for d in _ADULT_DOMAINS)


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
    return ["--ffprobe-location", p] if p else []


def _cookie_args() -> list:
    cookie_file = Path.home() / "Downloads" / "dynamic_cookies.txt"
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


def _normalize_audio_label(ext: str, acodec: str, abr: int) -> str:
    """Convert codec/ext names to clean readable labels."""
    ext = (ext or "").lower()
    acodec = (acodec or "").lower()
    if "mp4a" in acodec or "aac" in acodec or ext in ("m4a", "mp4a"):
        fmt = "AAC"
    elif "mp3" in acodec or ext in ("mp3", "mpa"):
        fmt = "MP3"
    elif "opus" in acodec or ext == "opus":
        fmt = "Opus"
    elif "vorbis" in acodec or ext in ("ogg", "oga"):
        fmt = "OGG"
    elif "flac" in acodec or ext == "flac":
        fmt = "FLAC"
    elif ext == "webm":
        fmt = "WebM"
    else:
        fmt = ext.upper() if ext else "Audio"
    return f"{fmt} ({abr}kbps)" if abr else fmt


def _has_video_stream(f: dict) -> bool:
    """True if this format has a real video track."""
    vcodec = (f.get("vcodec") or "none").lower().strip()
    height = f.get("height") or 0
    return height > 0 or vcodec not in ("none", "", "null")


def _is_audio_only(formats: list) -> bool:
    """True only when no format has any video stream at all."""
    if not formats:
        return False
    return not any(_has_video_stream(f) for f in formats)


def _parse_video_formats(formats: list, duration_sec: int) -> list:
    video = [f for f in formats if f and _has_video_stream(f) and f.get("ext") != "mhtml"]
    video.sort(key=lambda f: (f.get("height") or 0, f.get("fps") or 0,
                               f.get("tbr") or 0), reverse=True)
    seen, result = set(), []
    for f in video:
        height = f.get("height") or 0
        fps = f.get("fps") or 0
        if (height, int(fps)) in seen:
            continue
        seen.add((height, int(fps)))
        fps_str = f"{int(fps)}fps" if fps > 30 else ""
        res = f"4K ({height}p)" if height >= 2160 else (f"{height}p" if height else "Best")
        label_base = " ".join(p for p in [res, fps_str] if p)
        size_str = _size_label(f.get("filesize") or f.get("filesize_approx"),
                                duration_sec, f.get("tbr"), None)
        label = f"{label_base} — {size_str}" if size_str else label_base
        result.append({"label": label, "format_id": f.get("format_id")})

    return result if result else _FALLBACK_VIDEO


def _parse_audio_formats(formats: list, duration_sec: int) -> list:
    audio = [
        f for f in formats
        if (f.get("vcodec") or "none").lower() in ("none", "", "null")
        and (f.get("acodec") or "none").lower() not in ("none", "", "null")
        and f.get("ext") != "mhtml"
    ]
    audio.sort(key=lambda f: f.get("abr") or 0, reverse=True)
    seen, result = set(), []
    for f in audio:
        abr = int(f.get("abr") or 0)
        label_base = _normalize_audio_label(f.get("ext") or "", f.get("acodec") or "", abr)
        if label_base in seen:
            continue
        seen.add(label_base)
        size_str = _size_label(f.get("filesize") or f.get("filesize_approx"),
                                duration_sec, None, f.get("abr"))
        label = f"{label_base} — {size_str}" if size_str else label_base
        result.append({"label": label, "format_id": f.get("format_id")})

    return result if result else _FALLBACK_AUDIO


def _base_cmd(url: str) -> list:
    cmd = [
        _ytdlp(),
        "--no-playlist", "--no-warnings",
        "--no-check-certificate",
        "--user-agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    ]
    if _is_adult_url(url):
        cmd += ["--add-header", f"Referer:{url}",
                "--add-header", "Accept-Language:en-US,en;q=0.9"]
    cmd.extend(_ffprobe_args())
    cmd.extend(_cookie_args())
    return cmd


def scrape_formats(url: str) -> dict:
    cmd = _base_cmd(url) + ["--dump-json", "--quiet", url]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=25, creationflags=_no_window())
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Timed out fetching video info"}

    if proc.returncode != 0:
        err = re.sub(r"\x1b\[[0-9;]*m", "", (proc.stderr or "").strip())
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

    progress_tmpl = (
        f"%(progress.status)s{_SEP}"
        f"%(progress._total_bytes_estimate_str)s{_SEP}"
        f"%(progress._percent_str)s{_SEP}"
        f"%(progress._speed_str)s{_SEP}"
        f"%(progress._eta_str)s"
    )

    cmd = _base_cmd(url) + [
        "--newline", "--progress",
        "--progress-template", progress_tmpl,
        "--concurrent-fragments", "4",  # parallel fragment downloads = faster
        "-o", os.path.join(out_dir, "%(title)s.%(ext)s"),
        "-f", fmt,
    ] + extra + [url]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, creationflags=_no_window())
    except FileNotFoundError as e:
        yield {"type": "error", "message": str(e)}
        return

    err_lines = []
    def _read_err():
        for line in proc.stderr:
            err_lines.append(line.rstrip())
    threading.Thread(target=_read_err, daemon=True).start()

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        if _SEP in line:
            parts = [p.strip() for p in line.split(_SEP)]
            if len(parts) >= 5:
                _, _, pct_str, speed, eta = parts[:5]
                try:
                    pct = float(pct_str.replace("%", "").strip()) / 100.0
                except (ValueError, AttributeError):
                    pct = 0.0
                yield {"type": "progress", "percent": min(pct, 1.0),
                       "speed": speed if speed != "N/A" else "",
                       "eta":   eta   if eta   != "N/A" else ""}
        elif line.startswith(("[Merger]", "[ExtractAudio]", "[ffmpeg]")):
            yield {"type": "merging"}

    proc.wait()
    if proc.returncode == 0:
        yield {"type": "done"}
    else:
        err = "\n".join(err_lines).strip()
        yield {"type": "error", "message": err[:200] or f"yt-dlp exited {proc.returncode}"}


# Aliases
fetch_formats = scrape_formats
download_video = scrape_download
fetcher_download = scrape_download
download = scrape_download