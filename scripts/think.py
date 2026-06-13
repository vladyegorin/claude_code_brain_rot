from pathlib import Path
p = Path.home() / ".brainrot"
p.mkdir(exist_ok=True)
(p / "think.flag").touch()
