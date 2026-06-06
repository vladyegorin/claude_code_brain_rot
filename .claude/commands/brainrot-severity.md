---
description: Set brain rot severity level (off, low, medium, high, max)
allowed-tools: Bash
argument-hint: "off | low | medium | high | max"
---

Set the brain rot severity to $ARGUMENTS.

Levels:
- **off** — completely disabled, no videos, no beeps
- **low** — video appears after 5 seconds
- **medium** — video appears after 2 seconds
- **high** — video appears after 1 second
- **max** — instant, all 3 corners

!`python brain_rot.py severity $ARGUMENTS`
