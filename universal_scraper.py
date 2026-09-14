# universal_scraper.py
# --------------------
# Backend scraper and downloader for non-YouTube URLs.

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterator


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
        raise FileNotFoundError("yt-dlp binary not found.")
    return p


def _no_window() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _size_label(f: dict, duration_sec: int, is_video: bool = False) -> str:
    size_bytes = f.get("filesize") or f.get("filesize_approx")
    if not size_bytes and duration_sec:
        height = f.get("height") or 0
        rate = f.get("tbr") or f.get("vbr") or f.get("abr") or 0
        
        if not rate:
            if is_video:
                if height >= 2160: rate = 8000
                elif height >= 1440: rate = 4000
                elif height >= 1080: rate = 1800
                elif height >= 720: rate = 900
                elif height >= 480: rate = 500
                elif height > 0: rate = 300
            else:
                rate = f.get("abr") or 128

        if rate:
            if is_video and f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none"):
                rate += 128
            size_bytes = int(duration_sec * rate * 1000 / 8)

    if not size_bytes:
        return ""
    if size_bytes < 1024 * 1024:
        return f"~{max(1, round(size_bytes / 1024))} KB"
    return f"~{round(size_bytes / (1024 * 1024), 1)} MB"


def _parse_video_formats(formats: list, duration_sec: int) -> list:
    video = [
        f for f in formats
        if f.get("vcodec") not in (None, "none")
        and f.get("ext") not in ("mhtml",)
    ]
    video.sort(key=lambda f: (f.get("height") or 0, f.get("fps") or 0, f.get("tbr") or f.get("vbr") or 0), reverse=True)
    
    seen, result = set(), []
    for f in video:
        height = f.get("height") or 0
        fps = f.get("fps") or 0
        
        key = (height, int(fps)) if height else f.get("format_id")
        if key in seen:
            continue
        seen.add(key)

        height_label = f"{height}p" if height else "Default Video"
        fps_str = f"{int(fps)}fps" if fps > 30 else ""
        label_base = " ".join(p for p in [height_label, fps_str] if p)
        
        size_str = _size_label(f, duration_sec, is_video=True)
        label = f"{label_base} — {size_str}" if size_str else label_base
        result.append({"label": label, "format_id": f.get("format_id"), "filesize": f.get("filesize") or f.get("filesize_approx")})

    return result or [{"label": "Best ", "format_id": None}]


def _parse_audio_formats(formats: list, duration_sec: int) -> list:
    audio = [
        f for f in formats
        if f.get("acodec") not in (None, "none")
        and f.get("ext") not in ("mhtml",)
    ]
    audio.sort(key=lambda f: f.get("abr") or f.get("tbr") or 0, reverse=True)
    
    seen_abr, result = set(), []
    for f in audio:
        abr = int(f.get("abr") or f.get("tbr") or 0)
        if abr and abr in seen_abr:
            continue
        if abr:
            seen_abr.add(abr)
        size_str = _size_label(f, duration_sec, is_video=False)
        label = f"MP3 {abr}kbps" if abr else "Best Audio"
        if size_str:
            label += f" — {size_str}"
        result.append({"label": label, "format_id": f.get("format_id"), "filesize": f.get("filesize") or f.get("filesize_approx")})

    return result or [
        {"label": f"MP3 {br}kbps", "format_id": None}
        for br in (320, 256, 192, 128, 96, 64)
    ]


def fetch_formats(url: str) -> dict:
    """Fetch metadata and formats for non-YouTube sites via yt-dlp."""
    cmd = [
        _ytdlp(), "--dump-json", "--no-playlist", "--no-warnings", "--quiet", url
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=25, creationflags=_no_window())
        if proc.returncode != 0 or not proc.stdout.strip():
            err = proc.stderr.strip() or "Could not fetch media info."
            return {"ok": False, "error": err[:200]}

        info = json.loads(proc.stdout)
        raw_dur = info.get("duration")
        duration = int(raw_dur) if raw_dur is not None else 0
        formats = info.get("formats") or []
        is_live = bool(info.get("is_live")) or info.get("live_status") in ("is_live", "is_upcoming")

        return {
            "ok": True,
            "title": info.get("title") or info.get("description") or "Untitled Media",
            "channel": info.get("uploader") or info.get("uploader_id") or info.get("extractor_key") or "Web",
            "duration": duration,
            "thumbnail": info.get("thumbnail"),
            "is_live": is_live,
            "video_formats": _parse_video_formats(formats, duration),
            "audio_formats": _parse_audio_formats(formats, duration),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Fetch operation timed out."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def scrape_formats(url: str) -> dict:
    return fetch_formats(url)


def download_video(url: str, selected_fid=None, out_dir: str = "downloads",
                   is_audio: bool = False, format_id=None) -> Iterator[dict]:
    """Execute download process and stream progress updates back to the UI."""
    if selected_fid is not None:
        format_id = selected_fid

    os.makedirs(out_dir, exist_ok=True)

    if is_audio:
        fmt = f"{format_id}/bestaudio/best" if format_id else "bestaudio/best"
        extra = [
            "--extract-audio", "--audio-format", "mp3", "--audio-quality", "320K",
            "--embed-thumbnail", "--add-metadata",
            "--convert-thumbnails", "jpg",
            "--postprocessor-args", "ffmpeg:-id3v2_version 3"
        ]
    else:
        fmt = (f"{format_id}+bestaudio/bestvideo+bestaudio/best"
               if format_id else "bestvideo+bestaudio/best")
        extra = ["--merge-output-format", "mp4"]

    cmd = [
        _ytdlp(),
        "--no-playlist", "--no-warnings",
        "--continue",
        "--socket-timeout", "30",
        "--retries", "10",
        "--fragment-retries", "10",
        "--newline", "--progress",
        "-o", os.path.join(out_dir, "%(title)s.%(ext)s"),
        "-f", fmt,
    ] + extra + [url]

    _progress_re = re.compile(
        r"\[download\]\s+([\d.]+)%\s+of\s+~?([\d.]+\s*[a-zA-Z]+)\s+at\s+(\S+)(?:\s+ETA\s+(\S+))?"
    )

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, creationflags=_no_window())
    except FileNotFoundError as e:
        yield {"type": "error", "message": str(e)}
        return

    stream_count = 0
    has_dual_streams = not is_audio and "+" in fmt

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        if line.startswith("[download] Destination:"):
            stream_count += 1

        m = _progress_re.search(line)
        if m:
            try:
                raw_pct = float(m.group(1)) / 100.0
            except (ValueError, AttributeError):
                raw_pct = 0.0

            total_size_str = m.group(2) if len(m.groups()) >= 2 else "Unknown"
            speed_str = m.group(3) if len(m.groups()) >= 3 else ""
            eta_str = m.group(4) if len(m.groups()) >= 4 else ""

            if has_dual_streams:
                current_stream = max(1, stream_count)
                if current_stream == 1:
                    pct = raw_pct * 0.85
                else:
                    pct = 0.85 + (raw_pct * 0.15)
            else:
                pct = raw_pct

            downloaded_str = ""
            if total_size_str and total_size_str != "Unknown":
                try:
                    num_part = re.search(r"[\d.]+", total_size_str)
                    unit_part = re.search(r"[a-zA-Z]+", total_size_str)
                    if num_part and unit_part:
                        tot_val = float(num_part.group(0))
                        unit = unit_part.group(0)
                        dl_val = tot_val * raw_pct
                        downloaded_str = f"{dl_val:.1f}{unit}"
                except Exception:
                    downloaded_str = ""

            yield {
                "type": "progress",
                "percent": min(pct, 1.0),
                "speed": speed_str,
                "eta": eta_str,
                "downloaded": downloaded_str,
                "size": total_size_str
            }
        elif line.startswith(("[Merger]", "[ExtractAudio]", "[ffmpeg]")):
            yield {"type": "merging"}

    proc.wait()

    if proc.returncode == 0:
        yield {"type": "done"}
    else:
        stderr_err = proc.stderr.read().strip() if proc.stderr else ""
        yield {"type": "error", "message": stderr_err[:200] or f"Download failed (exit code {proc.returncode})"}


scrape_download = download_video
fetcher_download = download_video
download = download_video