"""Lead scoring after phase 2, using the weights in scoring.json (3 for high, 2 for medium, 1 for low)."""
import json
import re
from functools import lru_cache
from pathlib import Path

from .rules import SIGNALS

CONFIG = Path(__file__).resolve().parent.parent / "config" / "scoring.json"


@lru_cache(maxsize=1)
def config() -> dict:
    cfg = json.loads(CONFIG.read_text())
    names = {label for _, label in SIGNALS.values()}
    unknown = [n for n in {**cfg["fit_signals"], **cfg["buying_signals"]} if n not in names]
    if unknown:
        raise SystemExit(f"scoring.json names signals that don't exist: {unknown}")
    return cfg


def segment(employees: str) -> tuple[str, str]:
    """(segment, basis). Uses the first number in the employee column, so '11-50' counts as 11 (lower band)."""
    m = re.search(r"\d[\d,]*", employees or "")
    if not m:
        return "SMB", "no employee count in the list, so SMB (profile default)"
    n = int(m.group(0).replace(",", ""))
    for name, (low, high) in ((k, v) for k, v in config()["segments"].items() if k != "note"):
        if n >= low and (high is None or n <= high):
            return name, f"{n} employees from the list"
    return "SMB", f"{n} employees from the list"


def score(signals: dict, seg: str) -> dict:
    """Points for every signal answered Yes. Returns score, tier and a readable breakdown."""
    cfg = config()
    pts = cfg["points"]
    yes = {SIGNALS[sid][1] for sid, v in signals.items() if v["value"] == "yes"}
    skipped = set()
    for rule in cfg["skip_rules"]:
        if rule["if_yes"] in yes:
            skipped |= set(rule["skip"])
    parts = []
    for group in ("fit_signals", "buying_signals"):
        for name, weight in cfg[group].items():
            if name not in yes:
                continue
            level = weight.get(seg, "none") if isinstance(weight, dict) else weight
            if pts[level]:
                parts.append((name, pts[level]))
    for sid, (group, name) in SIGNALS.items():
        if group == "weak" and name in yes and name not in skipped:
            parts.append((name, cfg["weak_signal_points"]))
    total = sum(p for _, p in parts)
    tier = "Strong fit" if total >= cfg["tiers"]["strong_fit_min_score"] else "Weak fit"
    breakdown = "; ".join(f"{name} {p:+d}" for name, p in parts)
    return {"score": total, "tier": tier, "breakdown": breakdown}
