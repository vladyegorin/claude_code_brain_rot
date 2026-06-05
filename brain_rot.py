"""
brain_rot.py — Claude Code Brain Rot plugin

A tiny always-on daemon holds the mpv process handle and reacts to flag files
dropped by the hooks. This makes kill + beep effectively instant (no per-event
process startup, no taskkill scan, no PowerShell launch).

Hook commands:
    python brain_rot.py start    # UserPromptSubmit — start timing, ensure daemon
    python brain_rot.py done     # Stop            — kill video (drops done.flag)
    python brain_rot.py alert    # Notification    — kill video + beep (alert.flag)
    python brain_rot.py notify   # Notification hook — kill+beep only if user attention needed
    python brain_rot.py daemon   # internal — the long-running reactor
"""

import json
import os
import random
import signal
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

STATE_DIR   = os.path.join(os.path.expanduser("~"), ".brainrot")
THINK_FLAG  = os.path.join(STATE_DIR, "think.flag")
DONE_FLAG   = os.path.join(STATE_DIR, "done.flag")
ALERT_FLAG  = os.path.join(STATE_DIR, "alert.flag")
ALIVE_FILE  = os.path.join(STATE_DIR, "daemon.alive")
MPV_MISSING = os.path.join(STATE_DIR, "mpv_missing.txt")
LOG_PATH    = os.path.join(STATE_DIR, "events.log")

IS_WINDOWS = sys.platform == "win32"
DETACHED   = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

POLL_SEC       = 0.03   # daemon loop interval — controls reaction latency
ALIVE_STALE    = 3.0    # daemon considered dead if alive file older than this
DAEMON_IDLE_TTL = 1800  # daemon self-exits after this many idle seconds

SEVERITY_DELAYS = {"off": None, "low": 5, "medium": 2, "high": 1, "max": 0}

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def ensure_state_dir():
    os.makedirs(STATE_DIR, exist_ok=True)

def touch(path, content=""):
    ensure_state_dir()
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        pass

def remove(path):
    try:
        os.remove(path)
    except Exception:
        pass

def log(event):
    """Diagnostic — append a timestamp line matching the cmd %TIME% format."""
    try:
        ensure_state_dir()
        t = time.time()
        stamp = time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 100):02d}"
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {event}\n")
    except Exception:
        pass

def read_config():
    defaults = {
        "severity": "medium",
        "video_folder": "videos",
        "video_width": 320,
        "video_height": 180,
        "corner_padding": 10,
    }
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            defaults.update(json.load(f))
    except Exception:
        pass
    return defaults

# ---------------------------------------------------------------------------
# Video helpers
# ---------------------------------------------------------------------------

def pick_random_video(video_folder):
    if not os.path.isabs(video_folder):
        video_folder = os.path.join(SCRIPT_DIR, video_folder)
    try:
        files = [os.path.join(video_folder, f)
                 for f in os.listdir(video_folder)
                 if f.lower().endswith(".mp4")]
    except Exception:
        return None
    return random.choice(files) if files else None

def pick_videos_for_corners(video_folder, n):
    """Return a list of n video paths — distinct if possible, random repeats otherwise."""
    if not os.path.isabs(video_folder):
        video_folder = os.path.join(SCRIPT_DIR, video_folder)
    try:
        files = [os.path.join(video_folder, f)
                 for f in os.listdir(video_folder)
                 if f.lower().endswith(".mp4")]
    except Exception:
        files = []
    if not files:
        return [None] * n
    if len(files) >= n:
        return random.sample(files, n)
    # fewer videos than corners — cycle a shuffled list so every video is used and
    # we never end up showing the same clip in all corners (guaranteed variety).
    shuffled = files[:]
    random.shuffle(shuffled)
    result = [shuffled[i % len(shuffled)] for i in range(n)]
    random.shuffle(result)
    return result

def geometry_flag(corner, w, h, p):
    return {
        "bottom-right": f"{w}x{h}-{p}-{p}",
        "bottom-left":  f"{w}x{h}+{p}-{p}",
        "top-right":    f"{w}x{h}-{p}+{p}",
        "top-left":     f"{w}x{h}+{p}+{p}",
    }.get(corner, f"{w}x{h}-{p}-{p}")

def find_mpv():
    candidates = ["mpv"]
    if IS_WINDOWS:
        candidates += [
            r"C:\Program Files\MPV Player\mpv.exe",
            r"C:\Program Files\mpv\mpv.exe",
            r"C:\Program Files (x86)\mpv\mpv.exe",
            os.path.join(os.path.expanduser("~"), "scoop", "shims", "mpv.exe"),
        ]
    for c in candidates:
        try:
            if subprocess.run([c, "--version"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0:
                return c
        except Exception:
            continue
    return None

def spawn_mpv(mpv_bin, video_path, geometry):
    """Launch mpv and return the Popen object (handle held by the daemon)."""
    cmd = [
        mpv_bin,
        "--no-terminal", "--really-quiet",
        "--mute=yes",                   # videos play silently
        "--no-audio",                   # don't even open an audio output
        "--no-border",
        "--loop-file=inf",
        "--ontop",                      # stays visible on top (works even when
                                        # launched by the background daemon)
        "--no-input-default-bindings",  # F can't fullscreen, no key hijacking
        f"--geometry={geometry}",
        video_path,
    ]
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if IS_WINDOWS:
        kwargs["creationflags"] = DETACHED
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(cmd, **kwargs)
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Win32 overlay — make mpv topmost + non-activatable so it never steals focus
# ---------------------------------------------------------------------------

def set_overlay(pid):
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        GWL_EXSTYLE, WS_EX_TOPMOST, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x8, 0x08000000, 0x80
        HWND_TOPMOST, SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = -1, 0x2, 0x1, 0x10

        def _cb(hwnd, _):
            buf = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(buf))
            if buf.value == pid and user32.IsWindowVisible(hwnd):
                style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                      style | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Beep — in-process, instant (no PowerShell startup)
# ---------------------------------------------------------------------------

def beep():
    if IS_WINDOWS:
        try:
            import winsound
            winsound.Beep(880, 200)
            return
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    else:
        try:
            subprocess.run(["paplay", "/usr/share/sounds/freedesktop/stereo/bell.oga"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# The daemon — holds video handles, reacts to flag files within one poll
# ---------------------------------------------------------------------------

class Daemon:
    def __init__(self):
        self.procs = []          # live mpv Popen handles
        self.launch_at = None    # epoch time to launch video, or None
        self.last_active = time.time()
        self.mpv_bin = find_mpv()

    def kill_video(self):
        for p in self.procs:
            try:
                p.kill()
            except Exception:
                pass
        self.procs = []

    def launch_video(self):
        self.kill_video()
        if not self.mpv_bin:
            touch(MPV_MISSING,
                  "mpv not found.\nWindows: winget install mpv\n"
                  "macOS: brew install mpv\nLinux: sudo apt install mpv\n")
            return
        cfg = read_config()
        w = cfg.get("video_width", 320)
        h = cfg.get("video_height", 180)
        p = cfg.get("corner_padding", 10)
        severity = cfg.get("severity", "medium")
        # bottom-left dropped: overlaps the terminal input field
        corners = (["bottom-right", "top-right", "top-left"]
                   if severity == "max" else ["bottom-right"])
        videos = pick_videos_for_corners(cfg.get("video_folder", "videos"), len(corners))
        for corner, video in zip(corners, videos):
            if not video:
                continue
            proc = spawn_mpv(self.mpv_bin, video, geometry_flag(corner, w, h, p))
            if proc:
                self.procs.append(proc)
        # NOTE: no window-style manipulation. --focus-on=never keeps focus on the
        # terminal; the window stays a normal, visible, clickable window.

    def handle_think(self):
        cfg = read_config()
        delay = SEVERITY_DELAYS.get(cfg.get("severity", "medium"))
        if delay is None:          # severity == off
            self.launch_at = None
            self.kill_video()
            return
        # prune dead handles before deciding whether a video is live
        self.procs = [p for p in self.procs if p.poll() is None]
        if self.procs:
            # video already playing — re-arm is a no-op (no flicker)
            return
        if self.launch_at is not None:
            # launch already scheduled — don't reschedule
            return
        self.launch_at = time.time() + delay

    def loop(self):
        ensure_state_dir()
        last_alive = 0
        while True:
            now = time.time()

            # heartbeat
            if now - last_alive > 0.5:
                touch(ALIVE_FILE, str(int(now)))
                last_alive = now

            # react to flags (check kill/beep first for lowest latency)
            if os.path.exists(ALERT_FLAG):
                remove(ALERT_FLAG)
                had_video = bool(self.procs)
                self.launch_at = None
                self.kill_video()
                beep()
                log(f"DAEMON-alert (killed={had_video}, beeped)")
                self.last_active = now

            if os.path.exists(DONE_FLAG):
                remove(DONE_FLAG)
                had_video = bool(self.procs)
                self.launch_at = None
                self.kill_video()
                log(f"DAEMON-done (killed={had_video})")
                self.last_active = now

            if os.path.exists(THINK_FLAG):
                remove(THINK_FLAG)
                self.handle_think()
                log(f"DAEMON-think (scheduled launch_at=+{(self.launch_at - now):.1f}s)"
                    if self.launch_at else "DAEMON-think (off, no launch)")
                self.last_active = now

            # drop dead handles before checking for live video
            self.procs = [p for p in self.procs if p.poll() is None]

            # scheduled video launch — only if no video is already alive
            if self.launch_at is not None and now >= self.launch_at:
                self.launch_at = None
                if not self.procs:
                    self.launch_video()
                    log(f"DAEMON-launch (video shown, procs={len(self.procs)})")
                    self.last_active = now
                else:
                    log("DAEMON-launch skipped (video already live)")

            # self-exit if idle a long time
            if now - self.last_active > DAEMON_IDLE_TTL and not self.procs:
                self.kill_video()
                return

            time.sleep(POLL_SEC)


def threading_timer(delay, fn):
    import threading
    return threading.Timer(delay, fn)

# ---------------------------------------------------------------------------
# Daemon lifecycle
# ---------------------------------------------------------------------------

def daemon_alive():
    try:
        return (time.time() - os.path.getmtime(ALIVE_FILE)) < ALIVE_STALE
    except Exception:
        return False

def ensure_daemon():
    if daemon_alive():
        return
    ensure_state_dir()
    touch(ALIVE_FILE, str(int(time.time())))  # claim immediately to avoid double-spawn
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if IS_WINDOWS:
        kwargs["creationflags"] = DETACHED
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "daemon"], **kwargs)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_start():
    # Non-latency-critical: ensure the daemon is up, then signal a new turn.
    # Clear any stale kill/beep flags so a leftover never fires a spurious beep
    # on the next turn (e.g. an alert.flag the daemon missed while it was down).
    remove(ALERT_FLAG)
    remove(DONE_FLAG)
    ensure_daemon()
    touch(THINK_FLAG)

def cmd_done():
    touch(DONE_FLAG)

def cmd_alert():
    touch(ALERT_FLAG)

# Notification event JSON shape is uncertain until verified against a live payload.
# The raw stdin is logged to events.log on first invocation so the exact fields can
# be confirmed.  Fields inspected here are based on Claude Code hook documentation
# and are best-effort until a real payload is observed.
_notify_logged = False

def cmd_notify():
    global _notify_logged
    raw = sys.stdin.read()

    # Always log the first payload so we can verify the JSON shape.
    if not _notify_logged:
        log(f"NOTIFY-raw {raw!r}")
        _notify_logged = True

    if not raw.strip():
        # Empty stdin — default to alerting (better to nudge than miss a prompt).
        log("NOTIFY-empty-stdin -> alert")
        cmd_alert()
        return

    try:
        data = json.loads(raw)
    except Exception:
        log("NOTIFY-parse-error -> alert")
        cmd_alert()
        return

    # Inspect likely fields.  Shape TBD — verify against events.log after first run.
    # Candidates: data["message"], data["notification_type"], data["hook_event_name"]
    message   = str(data.get("message", "")).lower()
    notif_type = str(data.get("notification_type", "")).lower()
    hook_name  = str(data.get("hook_event_name", "")).lower()
    combined   = " ".join([message, notif_type, hook_name])

    # Only a permission prompt is a true "must act now" signal. The Stop hook
    # already beeps the moment Claude finishes, so the idle_prompt notification
    # (fired 60s after Stop, "Claude is waiting for your input") would just
    # double-beep with nothing to act on — ignore everything except permission.
    attention = "permission" in combined

    if not attention:
        log(f"NOTIFY-ignored (not a permission prompt: {combined!r})")
        return

    log(f"NOTIFY-alert (combined={combined!r})")
    cmd_alert()

def cmd_daemon():
    # Guard against duplicate daemons racing in.
    Daemon().loop()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        return
    cmd = sys.argv[1].lower()
    if   cmd == "start":  cmd_start()
    elif cmd == "done":   cmd_done()
    elif cmd == "alert":  cmd_alert()
    elif cmd == "notify": cmd_notify()
    elif cmd == "daemon": cmd_daemon()

if __name__ == "__main__":
    main()
