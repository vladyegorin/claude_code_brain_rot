#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$brainRotPy  = Join-Path $repoRoot "brain_rot.py"
$thinkBat    = Join-Path $repoRoot "scripts\think.bat"
$notifyBat   = Join-Path $repoRoot "scripts\notify.bat"
$settingsDir = Join-Path $repoRoot ".claude"
$settingsFile = Join-Path $settingsDir "settings.json"

Write-Host ""
Write-Host "Claude Code Brain Rot — Windows Installer" -ForegroundColor Cyan
Write-Host ""

# ── Python check ─────────────────────────────────────────────────────────────
Write-Host "Checking Python..." -NoNewline
try {
    $pyOut = & python --version 2>&1
    $pyVersion = ($pyOut -replace "Python ", "").Trim()
    $parts = $pyVersion.Split(".")
    if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 8)) {
        Write-Host " FAIL" -ForegroundColor Red
        Write-Host "  Python 3.8+ required (found $pyVersion)."
        Write-Host "  Install: winget install Python.Python.3"
        exit 1
    }
    Write-Host " OK ($pyVersion)" -ForegroundColor Green
} catch {
    Write-Host " NOT FOUND" -ForegroundColor Red
    Write-Host "  Install Python: winget install Python.Python.3"
    exit 1
}

# ── mpv check ─────────────────────────────────────────────────────────────────
Write-Host "Checking mpv..." -NoNewline
$mpvCandidates = @(
    "mpv",
    "C:\Program Files\MPV Player\mpv.exe",
    "C:\Program Files\mpv\mpv.exe",
    "C:\Program Files (x86)\mpv\mpv.exe",
    (Join-Path $env:USERPROFILE "scoop\shims\mpv.exe")
)
$mpvFound = $false
foreach ($c in $mpvCandidates) {
    try {
        $result = & $c --version 2>&1
        if ($LASTEXITCODE -eq 0) { $mpvFound = $true; break }
    } catch {}
}
if ($mpvFound) {
    Write-Host " OK" -ForegroundColor Green
} else {
    Write-Host " NOT FOUND" -ForegroundColor Yellow
    Write-Host "  Videos won't play until mpv is installed."
    Write-Host "  Install: winget install mpv"
    Write-Host "  Then re-run this installer (or just start using Claude Code — it will warn)."
}

# ── Write settings.json ───────────────────────────────────────────────────────
Write-Host "Writing .claude/settings.json..." -NoNewline

# Escape backslashes for JSON string values
function EscapeJson($path) { $path.Replace("\", "\\").Replace('"', '\"') }

$pyPath    = EscapeJson $brainRotPy
$thinkPath = EscapeJson $thinkBat
$notifyPath = EscapeJson $notifyBat

$settingsJson = @"
{
  "permissions": {
    "allow": [
      "Bash(python brain_rot.py *)"
    ]
  },
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "python \"$pyPath\" start" }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          { "type": "command", "command": "\"$thinkPath\"" }
        ]
      }
    ],
    "Notification": [
      {
        "hooks": [
          { "type": "command", "command": "python \"$pyPath\" notify" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "\"$notifyPath\"" }
        ]
      }
    ]
  }
}
"@

New-Item -ItemType Directory -Force $settingsDir | Out-Null
Set-Content -Path $settingsFile -Value $settingsJson -Encoding UTF8
Write-Host " OK" -ForegroundColor Green

# ── Create state directory ────────────────────────────────────────────────────
Write-Host "Creating ~/.brainrot state directory..." -NoNewline
New-Item -ItemType Directory -Force (Join-Path $env:USERPROFILE ".brainrot") | Out-Null
Write-Host " OK" -ForegroundColor Green

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Done!" -ForegroundColor Green
Write-Host "Open Claude Code in this folder and start a conversation."
Write-Host "Try /brainrot-severity max for the full experience."
Write-Host ""
