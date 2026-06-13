# Claude Code Brain Rot

Plays brainrot videos in the corners of your screen while Claude is working. Kills them and beeps when Claude finishes or needs your attention.

> **Note:** This currently only works when Claude Code is opened inside the directory where this repo is cloned. A global install (works in any project) is in progress.

---

## How it works

Claude Code supports lifecycle hooks - shell commands that fire on events like "user sent a message" or "Claude finished responding." This project wires into those hooks.

When you send a message, a hook boots a background daemon. The daemon polls a folder of flag files every 30ms. Other hooks create those flag files - one to say "start a video," one to say "kill it and beep." The daemon reacts instantly. When Claude finishes, the flag goes down, the video dies, and you hear a beep.

No background service, no startup entry. The daemon spawns on your first message and quietly exits after 30 minutes of inactivity.

---

## Requirements

- Python 3.8+
- [mpv](https://mpv.io/) — the video player

---

## Install

### Windows

```powershell
git clone https://github.com/you/claude-code-brain-rot
cd claude-code-brain-rot
.\install.ps1
```

If you don't have mpv:
```powershell
winget install mpv
```

### macOS

```bash
git clone https://github.com/you/claude-code-brain-rot
cd claude-code-brain-rot
./install.sh
```

If you don't have mpv:
```bash
brew install mpv
```

### Linux

```bash
git clone https://github.com/you/claude-code-brain-rot
cd claude-code-brain-rot
./install.sh
```

If you don't have mpv:
```bash
sudo apt install mpv
```

The installer checks your Python version, writes `.claude/settings.json` with the correct paths for your machine, and creates the `~/.brainrot/` state directory.

---

## Adding videos

Drop `.mp4` files into the `videos/` folder.  The plugin picks a random one each time (or multiple for max severity). The more you add, the more variety you get.

---

## Commands

All commands are available as slash commands inside Claude Code.

### `/brainrot-severity <level>`

Controls how aggressive the videos are.

| Level | Delay | Corners |
|-------|-------|---------|
| `off` | — | none — completely silent |
| `low` | 5s | 1 |
| `medium` | 2s | 1 |
| `high` | 1s | 1 |
| `max` | instant | 3 |

```
/brainrot-severity max
/brainrot-severity off
```

### `/brainrot-beep <on|off>`

Toggles the alert beep when Claude finishes. Videos still stop either way.

```
/brainrot-beep off
```

### `/brainrot-enable`

Restores severity to `max` and restarts the daemon.

### `/brainrot-disable`

Sets severity to `off` and kills everything. Acts as if the plugin isn't there.

### `/brainrot-restart`

Kills the daemon and any playing videos. Useful after editing `brain_rot.py` — the daemon picks up code changes only on restart. It respawns automatically on your next message.

---

## Config

`config.json` in the repo root. You can edit it directly or use the slash commands.

```json
{
  "severity": "medium",
  "video_folder": "videos",
  "video_width": 320,
  "video_height": 180,
  "corner_padding": 10,
  "beep": true,
  "urls": []
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `severity` | `"medium"` | Severity level (see above) |
| `video_folder` | `"videos"` | Path to your `.mp4` files |
| `video_width` | `320` | Width of each video window in pixels |
| `video_height` | `180` | Height of each video window in pixels |
| `corner_padding` | `10` | Gap from screen edge in pixels |
| `beep` | `true` | Whether to beep when Claude finishes |

---

## Custom URLs (WIP)

> This feature is not yet implemented. The groundwork is in place — the `urls` key in `config.json` is reserved for it.

The plan is to let you add YouTube/TikTok/etc. URLs as an alternative to local files. mpv supports URL playback natively and can pull streams via [yt-dlp](https://github.com/yt-dlp/yt-dlp).

---

## Notes

- Hooks only fire when Claude Code is opened inside the cloned repo folder. Your other projects are unaffected.
- The daemon runs as a normal user process — no admin rights, no services, nothing persistent across reboots.
- If mpv is not found, the plugin logs a message to `~/.brainrot/mpv_missing.txt` and does nothing. No crash, no noise.
- After editing `brain_rot.py`, run `/brainrot-restart` to reload the daemon.
