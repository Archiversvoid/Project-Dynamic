# bot_guard.py
# ------------
# Anti-bot + age-gate middleware for yt-dlp subprocess calls.

import json
import queue
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from age_gate import is_age_restricted, get_cookie_args, check_and_handle, cookie_file_valid

_BOT_SIGNALS = (
    "sign in to confirm",
    "confirm you're not",
    "not a bot",
    "http error 429",
    "too many requests",
    "login required",
    "please sign in",
    "has been blocked",
    "access denied",
)

# One client per entry — comma-separated is NOT valid yt-dlp syntax
_STRATEGIES = ["tv_embedded", "tv", "android", "ios", "mweb"]


def _is_bot(stdout: str, stderr: str) -> bool:
    combined = (stdout + stderr).lower()
    return any(s in combined for s in _BOT_SIGNALS)


def _is_age(stdout: str, stderr: str) -> bool:
    return is_age_restricted(stdout, stderr)


def _has_real_formats(stdout: str) -> bool:
    try:
        info = json.loads(stdout)
        return any(
            f.get("height", 0) > 360 and f.get("vcodec") not in (None, "none")
            for f in (info.get("formats") or [])
        )
    except Exception:
        return False


class BotGuard:
    def __init__(self):
        self.script_dir = Path(__file__).resolve().parent

    def find_binary(self, name: str):
        for candidate in [name, name + ".exe"]:
            p = self.script_dir / candidate
            if p.exists():
                return str(p)
        return shutil.which(name)

    def _ytdlp(self) -> str:
        p = self.find_binary("yt-dlp")
        if not p:
            raise FileNotFoundError("yt-dlp binary not found.")
        return p

    def _js_args(self) -> list:
        for name in ("deno", "node"):
            p = self.find_binary(name)
            if p:
                return ["--extractor-args", f"youtube:js_runtimes={name}:{p}"]
        return []

    def _no_window(self) -> int:
        return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    def _build_cmd(self, strategy: str, extra_args: list, url: str,
                   with_cookies: bool = False) -> list:
        cmd = [
            self._ytdlp(),
            "--no-playlist",
            "--no-warnings",
            "--extractor-args", f"youtube:player_client={strategy}",
            "--extractor-args", "youtube:formats=missing_pot",
        ]
        cmd.extend(self._js_args())
        if with_cookies:
            cmd.extend(get_cookie_args())
        cmd.extend(extra_args)
        cmd.append(url)
        return cmd

    def execute_with_failover(self, url: str, extra_args: list,
                              timeout: int = 60,
                              require_hq_formats: bool = True):
        empty = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="All strategies failed."
        )
        last_proc = empty
        hit_age = False

        # Pass 1: try each strategy without cookies
        for strategy in _STRATEGIES:
            cmd = self._build_cmd(strategy, extra_args, url, with_cookies=False)
            print(f"[BotGuard] Trying: {strategy}")
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=timeout, creationflags=self._no_window())
            except subprocess.TimeoutExpired:
                print(f"[BotGuard] Timeout on '{strategy}'")
                continue
            except Exception as e:
                print(f"[BotGuard] Error on '{strategy}': {e}")
                continue

            if _is_age(proc.stdout, proc.stderr):
                print(f"[BotGuard] Age-gate on '{strategy}'")
                hit_age = True
                last_proc = proc
                continue

            if _is_bot(proc.stdout, proc.stderr):
                print(f"[BotGuard] Bot signal on '{strategy}'")
                last_proc = proc
                continue

            if proc.returncode != 0:
                print(f"[BotGuard] Non-zero exit on '{strategy}'")
                last_proc = proc
                continue

            if require_hq_formats and not _has_real_formats(proc.stdout):
                print(f"[BotGuard] Only 360p from '{strategy}'")
                last_proc = proc
                continue

            print(f"[BotGuard] Success: {strategy}")
            return proc

        # Pass 2: age-gate hit — retry with cookies if available
        if hit_age and cookie_file_valid():
            print("[BotGuard] Age-gate — retrying with cookies...")
            for strategy in ["tv_embedded", "android", "ios"]:
                cmd = self._build_cmd(strategy, extra_args, url, with_cookies=True)
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True,
                                          timeout=timeout, creationflags=self._no_window())
                except Exception:
                    continue
                if proc.returncode == 0:
                    if not require_hq_formats or _has_real_formats(proc.stdout):
                        print(f"[BotGuard] Success with cookies: {strategy}")
                        return proc
                last_proc = proc

        # Attach age_gate info so fetcher can surface it
        last_proc._age_gate_info = (
            check_and_handle(last_proc.stdout, last_proc.stderr)
            if hit_age else None
        )
        print("[BotGuard] All strategies failed.")
        return last_proc

    def iter_download(self, url: str, dl_args: list):
        # dsymbol/yt-dlp-gui approach: --progress-template puts structured
        # __SEP__-delimited data on stdout. No regex, no dual-stream confusion.
        _SEP = "__SEP__"
        _merge_starts = ("[Merger]", "[ExtractAudio]", "[ffmpeg]")

        def _run(strategy, with_cookies):
            cmd = self._build_cmd(strategy, dl_args, url, with_cookies=with_cookies)
            print(f"[BotGuard] Download: {strategy} cookies={with_cookies}")
            return subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, creationflags=self._no_window(),
            )

        def _stream(proc):
            # Read stderr in background (for bot/age detection only)
            err_buf = []
            def _read_err():
                for line in proc.stderr:
                    err_buf.append(line.rstrip())
            threading.Thread(target=_read_err, daemon=True).start()

            bot_hit, age_hit, events, stdout_buf = False, False, [], []

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                stdout_buf.append(line)

                # Check for bot/age signals on stdout too
                if _is_age(line, ""):
                    age_hit = True
                    proc.kill()
                    break
                if _is_bot(line, ""):
                    bot_hit = True
                    proc.kill()
                    break

                if _SEP in line:
                    parts = [p.strip() for p in line.split(_SEP)]
                    if len(parts) >= 5:
                        _, _, percent_str, speed, eta = parts[:5]
                        try:
                            pct = float(percent_str.replace("%", "").strip()) / 100.0
                        except (ValueError, AttributeError):
                            pct = 0.0
                        events.append({
                            "type":    "progress",
                            "percent": min(pct, 1.0),
                            "speed":   speed if speed != "N/A" else "",
                            "eta":     eta   if eta   != "N/A" else "",
                        })
                    continue

                if line.startswith(_merge_starts):
                    events.append({"type": "merging"})

            proc.wait()
            # Also check stderr for bot signals we might have missed
            err_text = "\n".join(err_buf)
            if not bot_hit and _is_bot("", err_text):
                bot_hit = True
            if not age_hit and _is_age("", err_text):
                age_hit = True
            return bot_hit, age_hit, "\n".join(stdout_buf), err_text, events

        age_hit_global = False

        for strategy in _STRATEGIES:
            try:
                proc = _run(strategy, with_cookies=False)
            except FileNotFoundError as e:
                yield {"type": "error", "message": str(e)}
                return

            bot_hit, age_hit, stdout, stderr, events = _stream(proc)
            yield from events

            if age_hit:
                age_hit_global = True
                yield {"type": "progress", "percent": 0.0,
                       "speed": "age-restricted, retrying...", "eta": ""}
                continue

            if bot_hit:
                yield {"type": "progress", "percent": 0.0,
                       "speed": "retrying...", "eta": ""}
                continue

            if proc.returncode == 0:
                yield {"type": "done"}
                return

            yield {"type": "progress", "percent": 0.0, "speed": "retrying...", "eta": ""}

        # Age gate — retry with cookies
        if age_hit_global and cookie_file_valid():
            print("[BotGuard] Download age-gate — retrying with cookies...")
            for strategy in ["tv_embedded", "android", "ios"]:
                try:
                    proc = _run(strategy, with_cookies=True)
                except FileNotFoundError as e:
                    yield {"type": "error", "message": str(e)}
                    return
                bot_hit, age_hit, stdout, stderr, events = _stream(proc)
                yield from events
                if not bot_hit and not age_hit and proc.returncode == 0:
                    yield {"type": "done"}
                    return

        if age_hit_global and not cookie_file_valid():
            info = check_and_handle("", "sign in to confirm your age")
            yield {"type": "age_gate", "info": info}
            return

        yield {"type": "error", "message": "Download failed after all retries."}


guard = BotGuard()