"""Run log: one line per run in lists/<list>/runs.jsonl (date, step, settings, counts, AI cost and time).

Shown with `python -m enrich runs <list>`. Never deleted by the clean-up.
"""
import json
import sys
from datetime import datetime
from pathlib import Path


def log(folder: Path, step: str, **data) -> None:
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "step": step,
             "command": " ".join(Path(a).name if i == 0 else a for i, a in enumerate(sys.argv)), **data}
    with open(folder / "runs.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def read(folder: Path) -> list[dict]:
    path = folder / "runs.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
