---
description: Re-enable brain rot — restores severity to max so videos play again
allowed-tools: Bash
---
!`python brain_rot.py enable && python -c "import json; c=json.load(open('config.json')); print(c['severity'])"`

Reply with only this: "Brainrot: enabled. Severity: <severity>." where <severity> is the value printed above.
