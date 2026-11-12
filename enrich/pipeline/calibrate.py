"""Calibrate the services pipeline on real outcomes: do help-seekers score above DIY buyers?

    python -m enrich calibrate --template labels.csv              a blank labels file to fill in
    python -m enrich calibrate <list> --labels labels.csv         the report and suggested weights (changes nothing)
    python -m enrich calibrate <list> --labels labels.csv --apply also writes the suggested weights and cut-off

The labels come from sales calls and deals (Alexey's coded companies, closed-won services deals), matched by domain:
  Buyer type   help (wants the work done) | review (does the work, wants a check) | diy (does it themselves)
  Outcome      closed_won | open | lost (optional)

The list must have been enriched with the services pipeline first (free steps, then the AI answers). The report:
  - separation: the chance a help-seeker scores above a DIY buyer (0.5 = chance, 1.0 = perfect)
  - per fact: how often it is Yes among help-seekers and among DIY buyers, and the weight that ratio suggests
    (only for facts that are Yes for at least MIN_YES labelled companies; others keep their weight)
  - the Strong-fit cut-off that best separates the two groups, and how many closed-won deals reach it
Writes lists/<list>/output/calibration.json and calibration.csv. --apply changes config/icps/services/scoring.json
only after the tests pass (reverted otherwise).
"""
from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
from pathlib import Path

from rich.table import Table

from .common import console, rel
from ..core.inputs import DOMAIN_COLS, normalize_domain

MIN_YES = 5                    # labelled companies with the fact Yes, before its weight is suggested
LEVELS = [("high", 1.0), ("medium", 0.5), ("low", 0.2)]  # log-ratio of Yes rates (help vs DIY) -> weight
BUYER_TYPES = ("help", "review", "diy")


def write_template(path: Path) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Domain", "Company", "Buyer type", "Outcome", "Notes"])
        w.writerow(["example.com", "Example Co", "help", "closed_won", "books 6 months behind; left Pilot"])
        w.writerow(["another.io", "Another", "diy", "", "runs QuickBooks themselves; wants AI workflows"])
    return path


def read_labels(path: Path) -> dict[str, dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"{path} has no rows")
    cols = {c.strip().lower(): c for c in rows[0]}
    dcol = next((cols[c] for c in DOMAIN_COLS if c in cols), None)
    tcol, ocol = cols.get("buyer type"), cols.get("outcome")
    if not dcol or not tcol:
        raise SystemExit(f"{path} needs a Domain column and a 'Buyer type' column (help, review or diy)")
    out = {}
    for r in rows:
        d = normalize_domain(r.get(dcol, ""))
        t = (r.get(tcol) or "").strip().lower()
        if d and t in BUYER_TYPES:
            out[d] = {"type": t, "outcome": (r.get(ocol) or "").strip().lower() if ocol else ""}
    return out


def _auc(pos: list[float], neg: list[float]) -> float | None:
    """The chance a random help-seeker scores above a random DIY buyer (ties count half)."""
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def _points(level: str, cfg: dict) -> int:
    return cfg["points"].get(level, 0)


def _score(yes: set[str], weights: dict[str, str | dict], weak: set[str], cfg: dict, seg: str) -> int:
    total = 0
    for sid in yes:
        if sid in weak:
            total += cfg["weak_signal_points"]
        elif sid in weights:
            w = weights[sid]
            total += _points(w.get(seg, "none") if isinstance(w, dict) else w, cfg)
    return total


def _best_cutoff(pos: list[int], neg: list[int]) -> tuple[int | None, float]:
    """The score line that best separates help-seekers from DIY buyers (most help-seekers above, fewest DIY)."""
    best, best_j = None, -1.0
    for c in sorted(set(pos + neg)):
        tpr = sum(p >= c for p in pos) / len(pos)
        fpr = sum(n >= c for n in neg) / len(neg)
        if tpr - fpr > best_j:
            best, best_j = c, tpr - fpr
    return best, best_j


def run(folder: Path, labels_path: Path, apply: bool = False) -> dict:
    from ..checks.rules import SIGNALS
    from ..core import icp
    from ..core.store import Store
    if icp.name() != "services":
        raise SystemExit("Calibration is for the services pipeline (the default); don't pass --icp product.")
    labels = read_labels(labels_path)
    store = Store(folder)
    cfg = icp.scoring()
    by_name = {name: sid for sid, (_, name) in SIGNALS.items()}
    weights = {by_name[n]: w for n, w in {**cfg["fit_signals"], **cfg["buying_signals"]}.items()}
    weak = {sid for sid, (g, _) in SIGNALS.items() if g == "weak"}
    scored = [sid for sid, (g, _) in SIGNALS.items() if g in ("fit", "buying", "weak")]

    companies, missing = [], []
    for d, lab in labels.items():
        icp_r, sig_r = store.get_result(d, "icp"), store.get_result(d, "signals")
        if not icp_r:
            missing.append(d)
            continue
        s = {**(sig_r or {}).get("signals", {}), **icp_r.get("signals", {})}
        seg = ((sig_r or icp_r).get("facts") or {}).get("segment") or "SMB"
        companies.append({"domain": d, **lab, "seg": seg, "icp": icp_r.get("icp_verdict", ""),
                          "yes": {sid for sid in scored if s.get(sid, {}).get("value") == "yes"}})
    if not companies:
        raise SystemExit(f"None of the {len(labels)} labelled companies is in {folder.name}. Enrich them first "
                         f"(services pipeline), e.g. python -m enrich run labels.csv --list {folder.name}")
    help_c = [c for c in companies if c["type"] == "help"]
    diy_c = [c for c in companies if c["type"] == "diy"]

    # per fact: Yes rates and the weight their ratio suggests
    facts, suggested = [], dict(weights)
    for sid in scored:
        h = sum(sid in c["yes"] for c in help_c)
        n = sum(sid in c["yes"] for c in diy_c)
        rate_h = (h + 0.5) / (len(help_c) + 1)
        rate_n = (n + 0.5) / (len(diy_c) + 1)
        ratio = math.log(rate_h / rate_n)
        enough = (h + n) >= MIN_YES and help_c and diy_c
        suggestion = ""
        if enough and sid not in weak:
            suggestion = next((lvl for lvl, cut in LEVELS if ratio >= cut), "none")
            suggested[sid] = suggestion
        elif enough and sid in weak and ratio > 0.2:
            suggestion = "not a weak signal here: help-seekers have it more often (review its role)"
        facts.append({"id": sid, "name": SIGNALS[sid][1], "group": SIGNALS[sid][0], "yes_help": h, "help": len(help_c),
                      "yes_diy": n, "diy": len(diy_c), "log_ratio": round(ratio, 2),
                      "current": json.dumps(weights.get(sid, "weak (-2)" if sid in weak else "")),
                      "suggested": suggestion or ("keep (too few labelled companies with it)" if not enough else "keep")})

    def scores(w):
        return ([_score(c["yes"], w, weak, cfg, c["seg"]) for c in help_c], [_score(c["yes"], w, weak, cfg, c["seg"]) for c in diy_c])
    now_h, now_d = scores(weights)
    new_h, new_d = scores(suggested)
    cut_now = cfg["tiers"]["strong_fit_min_score"]
    best_cut, _ = _best_cutoff(new_h, new_d) if new_h and new_d else (None, 0)
    won = [c for c in companies if c["outcome"] == "closed_won"]
    won_scores = [_score(c["yes"], suggested, weak, cfg, c["seg"]) for c in won]
    report = {
        "list": folder.name, "labels": str(labels_path), "labelled": len(labels), "found_in_list": len(companies),
        "missing_from_list": missing[:20], "help": len(help_c), "review": sum(c["type"] == "review" for c in companies),
        "diy": len(diy_c), "closed_won": len(won),
        "separation_now": _auc(now_h, now_d), "separation_suggested": _auc(new_h, new_d),
        "strong_fit_cut_now": cut_now, "strong_fit_cut_suggested": best_cut,
        "help_at_or_above_cut": sum(x >= (best_cut or cut_now) for x in new_h),
        "diy_at_or_above_cut": sum(x >= (best_cut or cut_now) for x in new_d),
        "closed_won_at_or_above_cut": sum(x >= (best_cut or cut_now) for x in won_scores),
        "excluded_but_labelled_help": [c["domain"] for c in help_c if c["icp"] == "No"],
        "facts": facts,
    }
    out = folder / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "calibration.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    with open(out / "calibration.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(facts[0]))
        w.writeheader()
        w.writerows(sorted(facts, key=lambda r: -abs(r["log_ratio"])))
    _show(report)
    if apply:
        report["applied"] = _apply(suggested, best_cut, cfg, by_name)
    console.print(f"Saved: {rel(out / 'calibration.json')}, {rel(out / 'calibration.csv')}")
    return report


def _show(r: dict) -> None:
    def pct(x):
        return "n/a" if x is None else f"{x:.2f}"
    console.print(f"[bold]Calibration[/] {r['list']}: {r['found_in_list']} of {r['labelled']} labelled companies found "
                  f"({r['help']} help, {r['review']} review, {r['diy']} DIY; {r['closed_won']} closed-won)")
    console.print(f"Separation (chance a help-seeker scores above a DIY buyer): now {pct(r['separation_now'])}, "
                  f"with the suggested weights {pct(r['separation_suggested'])}")
    console.print(f"Strong-fit cut-off: now {r['strong_fit_cut_now']}, suggested {r['strong_fit_cut_suggested']} -> "
                  f"{r['help_at_or_above_cut']} help-seekers, {r['diy_at_or_above_cut']} DIY buyers and "
                  f"{r['closed_won_at_or_above_cut']} closed-won deals at or above it")
    if r["excluded_but_labelled_help"]:
        console.print(f"[yellow]Help-seekers the hard stops exclude: {', '.join(r['excluded_but_labelled_help'][:10])}[/] "
                      "(check those exclusions)")
    t = Table("ID", "Data point", "Help Yes", "DIY Yes", "Log ratio", "Now", "Suggested", header_style="bold")
    for f in sorted(r["facts"], key=lambda x: -abs(x["log_ratio"]))[:20]:
        t.add_row(f["id"], f["name"], f"{f['yes_help']}/{f['help']}", f"{f['yes_diy']}/{f['diy']}", str(f["log_ratio"]),
                  f["current"], f["suggested"])
    console.print(t)


def _apply(suggested: dict, cut: int | None, cfg: dict, by_name: dict) -> str:
    """Write the suggested weights and cut-off to the services scoring.json; revert if the tests fail."""
    from ..core import icp
    path = icp.folder("services") / "scoring.json"
    before = path.read_text(encoding="utf-8")
    data = json.loads(before)
    for group in ("fit_signals", "buying_signals"):
        for name in data[group]:
            sid = by_name.get(name)
            if sid in suggested and isinstance(suggested[sid], str) and not isinstance(data[group][name], dict):
                data[group][name] = suggested[sid]
    if cut is not None:
        data["tiers"]["strong_fit_min_score"] = cut
        data["tiers"]["note"] = "Set by calibrate on labelled companies; re-run calibrate when more labels arrive."
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    icp._scoring.cache_clear()
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True,
                       cwd=Path(__file__).resolve().parents[2], timeout=900)
    if r.returncode != 0:
        path.write_text(before, encoding="utf-8")
        icp._scoring.cache_clear()
        console.print("[red]Tests failed: the weights were not changed.[/]")
        return "reverted (tests failed)"
    console.print(f"[green]Applied[/] to {rel(path)} (tests pass). Size-specific weights were left as they were.")
    return "applied"
