"""Lead scoring after phase 2, using the weights in scoring.json (3 for high, 2 for medium, 1 for low)."""
import json
import re
from functools import lru_cache
from pathlib import Path

from .rules import SIGNALS

CONFIG = Path(__file__).resolve().parents[2] / "config" / "scoring.json"


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
    return {"score": total, "tier": tier, "breakdown": breakdown, "parts": parts}


def tier_range(signals: dict, seg: str | None, open_sids: set | None = None) -> dict:
    """Could the remaining open signals still change the lead tier? The lowest and highest score reachable if every
    open fit or buying signal ended Yes (more points) or every open weak signal ended Yes (minus points). When both
    ends give the same tier, no answer left can change it ("locked"). Unknown size: never locked (weights depend on it)."""
    cfg = config()
    pts = cfg["points"]
    now = score(signals, seg or "SMB")
    if not seg:
        return {"locked": False, "score": now["score"], "tier": now["tier"], "why": "size not known yet"}
    weights = {**cfg["fit_signals"], **cfg["buying_signals"]}
    gain = loss = 0
    for sid, v in signals.items():
        # open = still has a question waiting (open_sids); Unknown after its whole waterfall is final (0 points)
        if v.get("value") not in ("unknown", "check") or (open_sids is not None and sid not in open_sids):
            continue
        group, name = SIGNALS[sid]
        if group == "weak":
            loss += -cfg["weak_signal_points"]
        elif name in weights:
            w = weights[name]
            gain += pts[w.get(seg, "none") if isinstance(w, dict) else w]
    low, high = now["score"] - loss, now["score"] + gain
    cut = cfg["tiers"]["strong_fit_min_score"]
    locked = (low >= cut) == (high >= cut)
    return {"locked": locked, "score": now["score"], "tier": now["tier"], "low": low, "high": high, "strong_at": cut,
            "why": (f"score {now['score']}, the open signals can only reach {low} to {high} (Strong fit at {cut}): "
                    f"the tier stays {now['tier']}") if locked else ""}
