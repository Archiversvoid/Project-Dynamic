# Fetcher.py
# ----------
# Calls yt-dlp directly as a subprocess for YouTube.
# No bot_guard strategy rotation — that was causing slow processing and frozen downloads.
# Uses tv_embedded client (fastest, full format list, no PO token needed).

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


def _parse_video_formats(formats: list, duration_sec: int) -> list:
    video = [
        f for f in formats
        if f.get("height")
        and f.get("vcodec") not in (None, "none")
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
        res_label = f"4K ({height}p)" if height >= 2160 else f"{height}p"
        label_base = " ".join(p for p in [res_label, fps_str] if p)
        size_str = _size_label(f.get("filesize") or f.get("filesize_approx"),
                                duration_sec, f.get("tbr"), None)
        label = f"{label_base} — {size_str}" if size_str else label_base
        result.append({"label": label, "format_id": f["format_id"]})
    return result or [{"label": "Best Quality", "format_id": None}]


def _parse_audio_formats(formats: list, duration_sec: int) -> list:
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
    return result or [{"label": "Best Audio", "format_id": None}]


def fetch_formats(url: str) -> dict:
    """
    Fetch YouTube video metadata + full format list.
    Uses tv_embedded client directly — fastest client that returns full
    DASH format list without needing a PO token.
    Falls back to web client if tv_embedded returns no high-res formats.
    """
    # (client, with_cookies)
    attempts = [
        ("tv_embedded", False),
        ("tv_embedded", True),
        ("web",         False),
        ("web",         True),
        ("android",     False),
    ]
    last_err = ""
    for client, use_cookies in attempts:
        cmd = [
            _ytdlp(),
            "--dump-json", "--no-playlist", "--no-warnings", "--quiet",
            "--extractor-args", f"youtube:player_client={client}",
            "--extractor-args", "youtube:formats=missing_pot",
        ]
        cmd.extend(_js_args())
        if use_cookies:
            cmd.extend(_cookie_args())
        cmd.append(url)

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=20, creationflags=_no_window())
        except subprocess.TimeoutExpired:
            continue
        except FileNotFoundError as e:
            return {"ok": False, "error": str(e)}

        combined = (proc.stdout + proc.stderr).lower()
        last_err = re.sub(r"\x1b\[[0-9;]*m", "", (proc.stderr or "").strip())

        # Age-restricted check
        if any(s in combined for s in ("confirm your age", "age-restricted",
                "login_required", "sign in to confirm", "inappropriate for some")):
            # Try with cookies immediately before giving up
            cookie_list = _cookie_args()
            if cookie_list and not use_cookies:
                cmd_ck = [
                    _ytdlp(), "--dump-json", "--no-playlist", "--no-warnings", "--quiet",
                    "--extractor-args", f"youtube:player_client={client}",
                    "--extractor-args", "youtube:formats=missing_pot",
                ] + _js_args() + cookie_list + [url]
                try:
                    p2 = subprocess.run(cmd_ck, capture_output=True, text=True,
                                        timeout=20, creationflags=_no_window())
                    if p2.returncode == 0:
                        info2 = json.loads(p2.stdout)
                        raw2 = info2.get("duration")
                        dur2 = int(raw2) if raw2 is not None else 0
                        fmts2 = info2.get("formats") or []
                        return {
                            "ok": True,
                            "title":         info2.get("title", "Unknown Title"),
                            "channel":       info2.get("uploader") or info2.get("channel") or "Unknown",
                            "duration":      dur2,
                            "thumbnail":     info2.get("thumbnail"),
                            "video_formats": _parse_video_formats(fmts2, dur2),
                            "audio_formats": _parse_audio_formats(fmts2, dur2),
                        }
                except Exception:
                    pass
            # Cookies didn't work or not available — show setup instructions
            try:
                from age_gate import check_and_handle
                age_info = check_and_handle(proc.stdout, proc.stderr)
                if age_info.get("type") == "age_restricted":
                    return {"ok": False, "error": age_info["message"], "age_gate": age_info}
            except ImportError:
                pass
            return {"ok": False, "error": "Age-restricted video. Cookie setup required."}

        if proc.returncode != 0:
            continue  # 403 or other — try next attempt

        try:
            info = json.loads(proc.stdout)
        except json.JSONDecodeError:
            continue

        raw_dur = info.get("duration")
        duration = int(raw_dur) if raw_dur is not None else 0
        formats = info.get("formats") or []

        # Make sure we got real high-res formats, not just 360p fallback
        has_hq = any((f.get("height") or 0) > 360 and f.get("vcodec") not in (None, "none")
                     for f in formats)
        if not has_hq and client != "android":
            continue  # try next client for better formats

        return {
            "ok":            True,
            "title":         info.get("title", "Unknown Title"),
            "channel":       info.get("uploader") or info.get("channel") or "Unknown",
            "duration":      duration,
            "thumbnail":     info.get("thumbnail"),
            "video_formats": _parse_video_formats(formats, duration),
            "audio_formats": _parse_audio_formats(formats, duration),
        }

    return {"ok": False, "error": last_err[:200] if last_err else "Could not fetch video info after all attempts"}


def download_video(url: str, format_id=None, out_dir: str = "downloads",
                   is_audio: bool = False, selected_fid=None) -> Iterator[dict]:
    """
    Download YouTube video directly — no strategy rotation overhead.
    Uses __SEP__ progress template for clean progress parsing.
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

    progress_tmpl = (
        f"%(progress.status)s{_SEP}"
        f"%(progress._total_bytes_estimate_str)s{_SEP}"
        f"%(progress._percent_str)s{_SEP}"
        f"%(progress._speed_str)s{_SEP}"
        f"%(progress._eta_str)s"
    )

    def _build_dl_cmd(client, use_cookies):
        c = [
            _ytdlp(),
            "--no-playlist", "--no-warnings",
            "--extractor-args", f"youtube:player_client={client}",
            "--extractor-args", "youtube:formats=missing_pot",
            "--newline", "--progress",
            "--progress-template", progress_tmpl,
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
        ("tv_embedded", True),
        ("web",         False),
        ("web",         True),
        ("android",     False),
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
            if _SEP in line:
                parts = [p.strip() for p in line.split(_SEP)]
                if len(parts) >= 5:
                    _, _, pct_str, speed, eta = parts[:5]
                    try:
                        pct = float(pct_str.replace("%", "").strip()) / 100.0
                    except (ValueError, AttributeError):
                        pct = 0.0
                    got_progress = True
                    yield {"type": "progress", "percent": min(pct, 1.0),
                           "speed": speed if speed != "N/A" else "",
                           "eta":   eta   if eta   != "N/A" else ""}
            elif line.startswith(("[Merger]", "[ExtractAudio]", "[ffmpeg]")):
                yield {"type": "merging"}

        proc.wait()

        if proc.returncode == 0:
            yield {"type": "done"}
            return

        err = "\n".join(err_lines).strip()
        # 403 or bot check — try next client silently
        if any(s in err.lower() for s in ("403", "forbidden", "sign in", "bot")):
            if got_progress:
                # Already started downloading — don't retry, just report error
                yield {"type": "error", "message": err[:200]}
                return
            yield {"type": "progress", "percent": 0.0, "speed": f"retrying ({client})...", "eta": ""}
            continue

        yield {"type": "error", "message": err[:200] or f"yt-dlp exited {proc.returncode}"}
        return

    yield {"type": "error", "message": "Download failed after all attempts"}


# Aliases
fetcher_download = download_video
download = download_video