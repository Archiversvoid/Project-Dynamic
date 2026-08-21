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
    "login_required",
    "login required",
    "please sign in",
    "has been blocked",
    "access denied",
)

_AGE_SIGNALS = (
    "sign in to confirm your age",
    "this video may be inappropriate",
    "age-restricted",
    "age restricted",
    "confirm your age",
    "inappropriate for some users",
)

# One client per strategy — comma-separated is NOT valid yt-dlp syntax
_STRATEGIES = [
    "tv_embedded",
    "tv",
    "android",
    "ios",
    "mweb",
]


def _is_bot(stdout: str, stderr: str) -> bool:
    combined = (stdout + stderr).lower()
    return any(s in combined for s in _BOT_SIGNALS)


def _is_age_gate(stdout: str, stderr: str) -> bool:
    combined = (stdout + stderr).lower()
    return any(s in combined for s in _AGE_SIGNALS)


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

    def find_binary(self, name: str) -> str | None:
        for candidate in [name, name + ".exe"]:
            if (self.script_dir / candidate).exists():
                return str(self.script_dir / candidate)
        return shutil.which(name)

    def _ytdlp(self) -> str:
        p = self.find_binary("yt-dlp")
        if not p:
            raise FileNotFoundError("yt-dlp binary not found.")
        return p

    def _js_args(self) -> list[str]:
        for name in ("deno", "node"):
            p = self.find_binary(name)
            if p:
                return ["--extractor-args", f"youtube:js_runtimes={name}:{p}"]
        return []

    def _no_window(self) -> int:
        return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    def _build_cmd(self, strategy: str, extra_args: list[str], url: str,
                   with_cookies: bool = False) -> list[str]:
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

    def execute_with_failover(
        self,
        url: str,
        extra_args: list[str],
        timeout: int = 60,
        require_hq_formats: bool = True,
    ) -> subprocess.CompletedProcess:
        """
        Run yt-dlp with full strategy rotation + age-gate handling.
        For age-restricted videos: automatically retries with cookies if available.
        """
        _empty = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="All strategies failed."
        )
        last_proc = _empty
        hit_age_gate = False

        # First pass: try all strategies without cookies
        for strategy in _STRATEGIES:
            cmd = self._build_cmd(strategy, extra_args, url, with_cookies=False)
            print(f"[BotGuard] Trying: {strategy}")

            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=timeout, creationflags=self._no_window(),
                )
            except subprocess.TimeoutExpired:
                print(f"[BotGuard] Timeout on '{strategy}', rotating...")
                continue
            except Exception as e:
                print(f"[BotGuard] Error on '{strategy}': {e}")
                continue

            if _is_age_gate(proc.stdout, proc.stderr):
                print(f"[BotGuard] Age-gate on '{strategy}'")
                hit_age_gate = True
                last_proc = proc
                continue  # try next strategy — maybe tv_embedded bypasses it

            if _is_bot(proc.stdout, proc.stderr):
                print(f"[BotGuard] Bot signal on '{strategy}', rotating...")
                last_proc = proc
                continue

            if proc.returncode != 0:
                print(f"[BotGuard] Non-zero exit on '{strategy}', rotating...")
                last_proc = proc
                continue

            if require_hq_formats and not _has_real_formats(proc.stdout):
                print(f"[BotGuard] Only 360p from '{strategy}', rotating...")
                last_proc = proc
                continue

            print(f"[BotGuard] Success: {strategy}")
            return proc

        # Second pass: if age-gate was hit and we have cookies, retry with them
        if hit_age_gate and cookie_file_valid():
            print("[BotGuard] Age-gate detected — retrying with session cookies...")
            for strategy in ["tv_embedded", "android", "ios"]:
                cmd = self._build_cmd(strategy, extra_args, url, with_cookies=True)
                print(f"[BotGuard] Trying with cookies: {strategy}")
                try:
                    proc = subprocess.run(
                        cmd, capture_output=True, text=True,
                        timeout=timeout, creationflags=self._no_window(),
                    )
                except Exception:
                    continue

                if proc.returncode == 0:
                    if not require_hq_formats or _has_real_formats(proc.stdout):
                        print(f"[BotGuard] Success with cookies: {strategy}")
                        return proc

                last_proc = proc

        # If age gate was hit but no cookies — signal it clearly so the UI can help
        if hit_age_gate:
            result = check_and_handle(last_proc.stdout, last_proc.stderr)
            # Attach the age_gate info to the proc so fetcher can surface it
            last_proc._age_gate_info = result
        else:
            last_proc._age_gate_info = None

        print("[BotGuard] All strategies failed.")
        return last_proc

    def iter_download(self, url: str, dl_args: list[str]):
        """
        Generator: download with strategy rotation + age-gate cookie retry.
        Yields progress / merging / done / error / age_gate dicts.
        """
        _progress_re = re.compile(
            r"\[download\]\s+([\d.]+)%\s+of\s+[\d.]+\s*\S+\s+at\s+(\S+)(?:\s+ETA\s+(\S+))?"
        )
        _merge_re = re.compile(r"\[Merger\]|\[ffmpeg\]|Merging formats")

        def _run_proc(strategy, with_cookies):
            cmd = self._build_cmd(strategy, dl_args, url, with_cookies=with_cookies)
            print(f"[BotGuard] Download: {strategy} (cookies={with_cookies})")
            return subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, creationflags=self._no_window(),
            )

        def _stream_proc(proc):
            """Stream proc output, yield events. Returns (bot_hit, age_hit, stdout_buf, stderr_buf)."""
            q = queue.Queue()
            out_buf, err_buf = [], []

            def _read(stream, label):
                for line in stream:
                    q.put((label, line.rstrip()))
                q.put((label, None))

            threading.Thread(target=_read, args=(proc.stdout, "out"), daemon=True).start()
            threading.Thread(target=_read, args=(proc.stderr, "err"), daemon=True).start()

            done = 0
            bot_hit = False
            age_hit = False
            events = []

            while done < 2:
                label, line = q.get()
                if line is None:
                    done += 1
                    continue
                (out_buf if label == "out" else err_buf).append(line)

                if _is_age_gate(line, ""):
                    age_hit = True
                    proc.kill()
                    break
                if _is_bot(line, ""):
                    bot_hit = True
                    proc.kill()
                    break

                m = _progress_re.search(line)
                if m:
                    events.append({"type": "progress", "percent": float(m.group(1)) / 100.0,
                                   "speed": m.group(2), "eta": m.group(3) or ""})
                    continue
                if _merge_re.search(line):
                    events.append({"type": "merging"})

            proc.wait()
            return bot_hit, age_hit, "\n".join(out_buf), "\n".join(err_buf), events

        strategies = list(_STRATEGIES)
        age_hit_global = False

        for strategy in strategies:
            try:
                proc = _run_proc(strategy, with_cookies=False)
            except FileNotFoundError as e:
                yield {"type": "error", "message": str(e)}
                return

            bot_hit, age_hit, stdout, stderr, events = _stream_proc(proc)

            yield from events  # emit progress/merging seen so far

            if age_hit:
                age_hit_global = True
                yield {"type": "progress", "percent": 0.0, "speed": "age-restricted, retrying...", "eta": ""}
                continue

            if bot_hit:
                yield {"type": "progress", "percent": 0.0, "speed": "retrying...", "eta": ""}
                continue

            if proc.returncode == 0:
                yield {"type": "done"}
                return

            yield {"type": "progress", "percent": 0.0, "speed": "retrying...", "eta": ""}

        # Age gate second pass — retry with cookies
        if age_hit_global and cookie_file_valid():
            print("[BotGuard] Download age-gate — retrying with cookies...")
            for strategy in ["tv_embedded", "android", "ios"]:
                try:
                    proc = _run_proc(strategy, with_cookies=True)
                except FileNotFoundError as e:
                    yield {"type": "error", "message": str(e)}
                    return

                bot_hit, age_hit, stdout, stderr, events = _stream_proc(proc)
                yield from events

                if not bot_hit and not age_hit and proc.returncode == 0:
                    yield {"type": "done"}
                    return

        if age_hit_global and not cookie_file_valid():
            # No cookies — tell the UI to show setup instructions
            info = check_and_handle("", "sign in to confirm your age")
            yield {"type": "age_gate", "info": info}
            return

        yield {"type": "error", "message": "Download failed after all retries."}


# Module singleton
guard = BotGuard()