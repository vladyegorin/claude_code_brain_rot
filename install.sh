#!/bin/sh
set -e

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
SETTINGS_DIR="$REPO_ROOT/.claude"
SETTINGS_FILE="$SETTINGS_DIR/settings.json"

echo ""
echo "Claude Code Brain Rot — Installer"
echo ""

# ── Python check ──────────────────────────────────────────────────────────────
printf "Checking Python... "
if command -v python3 >/dev/null 2>&1; then
    PY_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PY_CMD="python"
else
    echo "NOT FOUND"
    echo "  Install Python 3.8+:"
    echo "    macOS:  brew install python3"
    echo "    Linux:  sudo apt install python3"
    exit 1
fi

PY_VERSION=$($PY_CMD --version 2>&1 | sed 's/Python //')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 8 ]; }; then
    echo "FAIL ($PY_VERSION)"
    echo "  Python 3.8+ required."
    exit 1
fi
echo "OK ($PY_VERSION)"

# ── mpv check ─────────────────────────────────────────────────────────────────
printf "Checking mpv... "
if command -v mpv >/dev/null 2>&1; then
    echo "OK"
else
    echo "NOT FOUND"
    echo "  Videos won't play until mpv is installed."
    if [ "$(uname)" = "Darwin" ]; then
        echo "  Install: brew install mpv"
    else
        echo "  Install: sudo apt install mpv"
    fi
    echo "  Then re-run this installer (or just start using Claude Code — it will warn)."
fi

# ── Write settings.json ───────────────────────────────────────────────────────
printf "Writing .claude/settings.json... "
mkdir -p "$SETTINGS_DIR"

cat > "$SETTINGS_FILE" <<EOF
{
  "permissions": {
    "allow": [
      "Bash(python brain_rot.py *)",
      "Bash(python3 brain_rot.py *)"
    ]
  },
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "$PY_CMD brain_rot.py start" }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          { "type": "command", "command": "$PY_CMD scripts/think.py" }
        ]
      }
    ],
    "Notification": [
      {
        "hooks": [
          { "type": "command", "command": "$PY_CMD brain_rot.py notify" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "$PY_CMD scripts/notify.py" }
        ]
      }
    ]
  }
}
EOF
echo "OK"

# ── Create state directory ────────────────────────────────────────────────────
printf "Creating ~/.brainrot state directory... "
mkdir -p "$HOME/.brainrot"
echo "OK"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "Done!"
echo "Open Claude Code in this folder and start a conversation."
echo "Try /brainrot-severity max for the full experience."
echo ""
