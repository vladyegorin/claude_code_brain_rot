"""
brain_rot.py — Claude Code Brain Rot plugin

A tiny always-on daemon holds the mpv process handle and reacts to flag files
dropped by the hooks. This makes kill + beep effectively instant (no per-event
process startup, no taskkill scan, no PowerShell launch).

Hook commands:
    python brain_rot.py start    # UserPromptSubmit — start timing, ensure daemon
    python brain_rot.py alert    # kill video + beep (drops alert.flag)
    python brain_rot.py notify   # Notification hook — kill+beep only if user attention needed
    python brain_rot.py daemon   # internal — the long-running reactor
"""

import json
import os
import random
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
        f"--start={random.randint(5, 85)}%",  # jump in at a random spot, not the start
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
        self.procs = []          # live mpv Popen handles (objects)
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
        # called by daemon when think flag is found
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
        # loop that runs every 30ms. polling flags and acting accordingly
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
                if read_config().get("severity") == "off":
                    log("DAEMON-alert ignored (severity=off)")
                else:
                    had_video = bool(self.procs)
                    self.launch_at = None
                    self.kill_video()
                    beep()
                    log(f"DAEMON-alert (killed={had_video}, beeped)")
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
    # Clear any stale kill/beep flag so a leftover never fires a spurious beep
    # on the next turn (e.g. an alert.flag the daemon missed while it was down).
    # called on every submitted prompt (UserPromptSubmit hook)
    remove(ALERT_FLAG)
    ensure_daemon()
    touch(THINK_FLAG)

def cmd_alert():
    touch(ALERT_FLAG)

# Notification event JSON shape is uncertain until verified against a live payload.
# The raw stdin is logged to events.log on first invocation so the exact fields can
# be confirmed.  Fields inspected here are based on Claude Code hook documentation
# and are best-effort until a real payload is observed.
_notify_logged = False

def cmd_notify():
    global _notify_logged
    if read_config().get("severity") == "off":
        return

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
    Daemon().loop()

def _kill_daemon_and_videos():
    """Kill any running daemon + mpv windows. Cross-platform."""
    import signal as _signal
    if IS_WINDOWS:
        try:
            import ctypes
            PROCESS_TERMINATE = 0x0001
            k32 = ctypes.windll.kernel32
            ps = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*brain_rot.py daemon*' } | Select-Object -ExpandProperty ProcessId"],
                capture_output=True, text=True)
            for pid in ps.stdout.split():
                try:
                    h = k32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
                    k32.TerminateProcess(h, 1)
                    k32.CloseHandle(h)
                except Exception:
                    pass
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"name='mpv.exe'\" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                capture_output=True)
        except Exception:
            pass
    else:
        subprocess.run(["pkill", "-f", "brain_rot.py daemon"], capture_output=True)
        subprocess.run(["pkill", "mpv"], capture_output=True)

def cmd_restart():
    _kill_daemon_and_videos()
    remove(ALIVE_FILE)
    print("Brain rot restarted — daemon will respawn on your next message.")

def cmd_enable():
    cfg = read_config()
    cfg["severity"] = "max"
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"Failed to write config: {e}")
        return
    _kill_daemon_and_videos()
    remove(ALIVE_FILE)
    print("Brain rot enabled (severity=max) — will start on your next message.")

def cmd_disable():
    cfg = read_config()
    cfg["severity"] = "off"
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"Failed to write config: {e}")
        return
    _kill_daemon_and_videos()
    remove(ALIVE_FILE)
    print("Brain rot disabled.")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        return
    cmd = sys.argv[1].lower()
    if   cmd == "start":   cmd_start()
    elif cmd == "alert":   cmd_alert()
    elif cmd == "notify":  cmd_notify()
    elif cmd == "daemon":  cmd_daemon()
    elif cmd == "restart": cmd_restart()
    elif cmd == "enable":  cmd_enable()
    elif cmd == "disable": cmd_disable()

if __name__ == "__main__":
    main()
