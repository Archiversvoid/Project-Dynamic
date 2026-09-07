# Fetcher.py
# ----------
# Calls yt-dlp directly as a subprocess for YouTube.

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

_SEP = "__SEP__"


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


def _js_args() -> list:
    for name in ("deno", "node"):
        p = _find_binary(name)
        if p:
            return ["--extractor-args", f"youtube:js_runtimes={name}:{p}"]
    return []


def _cookie_args() -> list:
    cookie_file = Path.home() / "Downloads" / "dynamic_cookies.txt"
    if cookie_file.exists() and cookie_file.stat().st_size > 500:
        return ["--cookies", str(cookie_file)]
    return []


def _browser_cookie_args() -> list:
    if sys.platform != "win32":
        return []
    for browser, path in [
        ("edge",   Path.home() / "AppData/Local/Microsoft/Edge"),
        ("chrome", Path.home() / "AppData/Local/Google/Chrome"),
    ]:
        if path.exists():
            return ["--cookies-from-browser", browser]
    return []


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
        if f.get("height")
        and f.get("vcodec") not in (None, "none")
        and f.get("ext") not in ("mhtml",)
    ]
    video.sort(key=lambda f: (f.get("height", 0), f.get("fps") or 0,
                               f.get("tbr") or f.get("vbr") or 0), reverse=True)
    seen, result = set(), []
    for f in video:
        height, fps = f.get("height"), f.get("fps") or 0
        if (height, int(fps)) in seen:
            continue
        seen.add((height, int(fps)))
        fps_str = f"{int(fps)}fps" if fps > 30 else ""
        res_label = f"4K ({height}p)" if height >= 2160 else f"{height}p"
        label_base = " ".join(p for p in [res_label, fps_str] if p)
        size_str = _size_label(f, duration_sec, is_video=True)
        label = f"{label_base} — {size_str}" if size_str else label_base
        result.append({"label": label, "format_id": f["format_id"], "filesize": f.get("filesize") or f.get("filesize_approx")})
    return result or [{"label": "Best Quality", "format_id": None}]


def _parse_audio_formats(formats: list, duration_sec: int) -> list:
    audio = [
        f for f in formats
        if f.get("vcodec") in (None, "none")
        and f.get("acodec") not in (None, "none")
        and f.get("ext") not in ("mhtml",)
    ]
    audio.sort(key=lambda f: f.get("abr") or f.get("tbr") or 0, reverse=True)
    seen_abr, result = set(), []
    for f in audio:
        abr = int(f.get("abr") or f.get("tbr") or 0)
        if not abr or abr in seen_abr:
            continue
        seen_abr.add(abr)
        size_str = _size_label(f, duration_sec, is_video=False)
        label = f"MP3 {abr}kbps"
        if size_str:
            label += f" — {size_str}"
        result.append({"label": label, "format_id": f["format_id"], "filesize": f.get("filesize") or f.get("filesize_approx")})
    return result or [
        {"label": f"MP3 {br}kbps", "format_id": None}
        for br in (320, 256, 192, 128, 96, 64)
    ]


def fetch_formats(url: str) -> dict:
    last_err = ""

    def _run_fetch(extra_args, timeout=15):
        cmd = [
            _ytdlp(), "--dump-json", "--no-playlist", "--no-warnings", "--quiet",
            "--extractor-args", "youtube:player_client=tv_embedded",
            "--extractor-args", "youtube:formats=missing_pot",
        ] + _js_args() + extra_args + [url]
        try:
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, creationflags=_no_window())
        except subprocess.TimeoutExpired:
            return None
        except FileNotFoundError as e:
            return e

    def _parse_proc(proc):
        if proc is None or isinstance(proc, Exception):
            return None
        if proc.returncode != 0:
            return None
        try:
            info = json.loads(proc.stdout)
            raw_dur = info.get("duration")
            duration = int(raw_dur) if raw_dur is not None else 0
            formats = info.get("formats") or []
            is_live = bool(info.get("is_live")) or info.get("live_status") in ("is_live", "is_upcoming")
            return {
                "ok":            True,
                "title":         info.get("title", "Unknown Title"),
                "channel":       info.get("uploader") or info.get("channel") or "Unknown",
                "duration":      duration,
                "is_live":       is_live,
                "thumbnail":     info.get("thumbnail"),
                "video_formats": _parse_video_formats(formats, duration),
                "audio_formats": _parse_audio_formats(formats, duration),
            }
        except Exception:
            return None

    proc = _run_fetch([])
    result = _parse_proc(proc)
    if result:
        return result

    combined = ((proc.stdout if proc and not isinstance(proc, Exception) else "") +
                (proc.stderr if proc and not isinstance(proc, Exception) else "")).lower()
    is_age = any(s in combined for s in ("confirm your age", "age-restricted",
                                          "login_required", "sign in to confirm"))

    if is_age:
        for extra in [_cookie_args(), _browser_cookie_args()]:
            if not extra:
                continue
            p2 = _run_fetch(extra, timeout=20)
            r2 = _parse_proc(p2)
            if r2:
                return r2
        try:
            from age_gate import check_and_handle, get_setup_instructions
            age_info = check_and_handle(
                proc.stdout if proc and not isinstance(proc, Exception) else "",
                proc.stderr if proc and not isinstance(proc, Exception) else "")
            if not age_info.get("instructions"):
                age_info["instructions"] = get_setup_instructions()
            return {"ok": False, "error": age_info.get("message", "Age-restricted"),
                    "age_gate": age_info}
        except Exception:
            pass
        return {"ok": False, "error": "Age-restricted. Export cookies and try again.",
                "age_gate": {"type": "age_restricted", "has_cookies": bool(_cookie_args()),
                             "instructions": None, "message": "Age-restricted."}}

    proc2 = _run_fetch(["--extractor-args", "youtube:player_client=web"], timeout=15)
    result2 = _parse_proc(proc2)
    if result2:
        return result2

    last_err = re.sub(r"\x1b\[[0-9;]*m", "",
                      (proc.stderr if proc and not isinstance(proc, Exception) else "").strip())
    return {"ok": False, "error": last_err[:200] or "Could not fetch video info"}


def download_video(url: str, format_id=None, out_dir: str = "downloads",
                   is_audio: bool = False, selected_fid=None) -> Iterator[dict]:
    if selected_fid is not None:
        format_id = selected_fid

    os.makedirs(out_dir, exist_ok=True)

    if is_audio:
        fmt = f"{format_id}/bestaudio/best" if format_id else "bestaudio/best"
        extra = [
            "--extract-audio", "--audio-format", "mp3", "--audio-quality", "320K",
            "--embed-thumbnail",
            "--add-metadata",
            "--convert-thumbnails", "jpg",
            "--postprocessor-args", "ffmpeg:-id3v2_version 3",
        ]
    else:
        fmt = (f"{format_id}+bestaudio/bestvideo+bestaudio/best"
               if format_id else "bestvideo+bestaudio/best")
        extra = ["--merge-output-format", "mp4"]

    _progress_re = re.compile(
        r"\[download\]\s+([\d.]+)%\s+of\s+[\d.~]+\s*\S+\s+at\s+(\S+)(?:\s+ETA\s+(\S+))?"
    )

    def _build_dl_cmd(client, use_cookies):
        c = [
            _ytdlp(),
            "--no-playlist", "--no-warnings",
            "--extractor-args", f"youtube:player_client={client}",
            "--extractor-args", "youtube:formats=missing_pot",
            "--newline", "--progress",
            "-o", os.path.join(out_dir, "%(title)s.%(ext)s"),
            "-f", fmt,
        ]
        c.extend(extra)
        c.extend(_js_args())
        if use_cookies:
            c.extend(_cookie_args())
        c.append(url)
        return c

    dl_attempts = [
        ("tv_embedded", False),
        ("web",         False),
        ("tv_embedded", True),
    ]

    for client, use_cookies in dl_attempts:
        cmd = _build_dl_cmd(client, use_cookies)
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

        got_progress = False
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            m = _progress_re.search(line)
            if m:
                try:
                    pct = float(m.group(1)) / 100.0
                except (ValueError, AttributeError):
                    pct = 0.0
                got_progress = True
                yield {"type": "progress", "percent": min(pct, 1.0),
                       "speed": m.group(2) or "",
                       "eta":   m.group(3) or ""}
            elif line.startswith(("[Merger]", "[ExtractAudio]", "[ffmpeg]")):
                yield {"type": "merging"}

        proc.wait()

        if proc.returncode == 0:
            yield {"type": "done"}
            return

        err = "\n".join(err_lines).strip()
        if any(s in err.lower() for s in ("403", "forbidden", "sign in", "bot")):
            if got_progress:
                yield {"type": "error", "message": err[:200]}
                return
            yield {"type": "progress", "percent": 0.0, "speed": f"retrying ({client})...", "eta": ""}
            continue

        yield {"type": "error", "message": err[:200] or f"yt-dlp exited {proc.returncode}"}
        return

    yield {"type": "error", "message": "Download failed after all attempts"}


fetcher_download = download_video
download = download_video