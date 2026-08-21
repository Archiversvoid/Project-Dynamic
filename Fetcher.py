# fetcher.py
# ----------
# Calls yt-dlp via bot_guard middleware.
# bot_guard handles strategy rotation, bot detection, and retry logic.

import json
import os
import re
from pathlib import Path
from typing import Iterator

from bot_guard import guard


# ---------------------------------------------------------------------------
# Size formatting
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Format parsing
# ---------------------------------------------------------------------------

def _parse_video_formats(formats: list, duration_sec: int) -> list[dict]:
    video = [
        f for f in formats
        if f.get("height")
        and f.get("vcodec") not in (None, "none")
        and f.get("ext") not in ("mhtml",)
    ]
    video.sort(
        key=lambda f: (f.get("height", 0), f.get("fps") or 0, f.get("tbr") or 0),
        reverse=True,
    )
    seen, result = set(), []
    for f in video:
        height, fps = f.get("height"), f.get("fps") or 0
        if (height, int(fps)) in seen:
            continue
        seen.add((height, int(fps)))
        fps_str = f"{int(fps)}fps" if fps > 30 else ""
        res_label = f"4K ({height}p)" if height >= 2160 else f"{height}p"
        label_base = " ".join(p for p in [res_label, fps_str] if p)
        size_str = _size_label(
            f.get("filesize") or f.get("filesize_approx"),
            duration_sec, f.get("tbr"), None,
        )
        label = f"{label_base} — {size_str}" if size_str else label_base
        result.append({"label": label, "format_id": f["format_id"]})
    return result or [{"label": "Best Quality", "format_id": None}]


def _parse_audio_formats(formats: list, duration_sec: int) -> list[dict]:
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
        size_str = _size_label(
            f.get("filesize") or f.get("filesize_approx"),
            duration_sec, None, f.get("abr"),
        )
        label = f"{label_base} — {size_str}" if size_str else label_base
        result.append({"label": label, "format_id": f["format_id"]})
    return result or [{"label": "Best Audio", "format_id": None}]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_formats(url: str) -> dict:
    """
    Fetch video metadata + full format list via bot_guard.
    Automatically rotates strategies until one returns >360p formats.
    """
    proc = guard.execute_with_failover(
        url,
        extra_args=["--dump-json", "--quiet"],
        timeout=60,
        require_hq_formats=True,
    )

    if not proc or proc.returncode != 0:
        err = re.sub(r"\x1b\[[0-9;]*m", "", (proc.stderr if proc else "").strip())
        age_info = getattr(proc, "_age_gate_info", None)
        if age_info and age_info.get("type") == "age_restricted":
            return {"ok": False, "error": age_info["message"], "age_gate": age_info}
        return {"ok": False, "error": err[:200] or "yt-dlp returned an error"}

    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "Could not parse yt-dlp metadata"}

    duration = info.get("duration") or 0
    formats = info.get("formats") or []

    return {
        "ok":            True,
        "title":         info.get("title", "Unknown Title"),
        "channel":       info.get("uploader") or info.get("channel") or "Unknown",
        "duration":      duration,
        "thumbnail":     info.get("thumbnail"),
        "video_formats": _parse_video_formats(formats, duration),
        "audio_formats": _parse_audio_formats(formats, duration),
    }


def download_video(
    url: str,
    format_id: str | None,
    out_dir: str,
    is_audio: bool = False,
) -> Iterator[dict]:
    """
    Download with automatic strategy rotation on bot detection.
    Delegates entirely to guard.iter_download.
    """
    os.makedirs(out_dir, exist_ok=True)

    if is_audio:
        fmt = f"{format_id}/bestaudio/best" if format_id else "bestaudio/best"
        extra = ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "320K"]
    else:
        fmt = (
            f"{format_id}+bestaudio/bestvideo+bestaudio/best"
            if format_id else "bestvideo+bestaudio/best"
        )
        extra = ["--merge-output-format", "mp4"]

    dl_args = [
        "--newline",
        "--progress",
        "-o", os.path.join(out_dir, "%(title)s.%(ext)s"),
        "-f", fmt,
    ] + extra

    yield from guard.iter_download(url, dl_args)


# Aliases for compatibility with main.py bridge
fetcher_download = download_video
download = download_video