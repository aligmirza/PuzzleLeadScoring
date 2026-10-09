"""Compare a list's results with the right answers, to measure accuracy and find what to fix.

The right answers ("labels") come from columns in the list itself, or from a separate CSV matched by domain:
  Expected ICP        YES or NO
  Expected tier       Strong fit or Weak fit (only for companies expected YES)
  Expected: <signal>  Yes or No for any data point, by its plain name, e.g. "Expected: Delaware C-Corp"
  Expected reason     free text, shown next to disagreements

Writes lists/<list>/output/evaluation.csv (one row per disagreement, with the proof we used) and prints a summary.
The number that matters most is "good companies wrongly marked NO": those leads are lost for good.
"""
import csv
from pathlib import Path

from rich.table import Table

from .common import console, rel
from ..core.inputs import DOMAIN_COLS, normalize_domain
from ..checks.rules import SIGNALS, signal_column

TEMPLATE_SIGNALS = ["M1", "E1", "E3", "E4", "E5", "E6", "F1", "F2", "F3", "F5", "F6"]


def _read(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames or []), list(r)


def _domain_col(cols: list[str]) -> str | None:
    lowered = {c.strip().lower(): c for c in cols}
    return next((lowered[c] for c in DOMAIN_COLS if c in lowered), None)


def _norm(v: str) -> str:
    return (v or "").strip().lower()


def run(folder: Path, labels: Path | None = None) -> dict:
    result_path = folder / "output" / f"{folder.name}_enriched.csv"
    if not result_path.exists():
        raise SystemExit(f"No results yet for '{folder.name}'. Run phase 1 first.")
    cols, results = _read(result_path)
    by_domain = {normalize_domain(r.get(_domain_col(cols) or "", "")): r for r in results}
    if labels:
        lcols, lrows = _read(labels)
    else:
        lcols, lrows = cols, results
    dcol = _domain_col(lcols)
    if not dcol or not any(c.startswith("Expected") for c in lcols):
        raise SystemExit("No labels found. Add 'Expected ICP' (and optionally 'Expected tier', 'Expected: <signal>') "
                         "columns to the list, or pass --labels <file>. Template: python -m enrich evaluate --template")
    signal_names = {SIGNALS[sid][1].lower(): sid for sid in SIGNALS}
    sig_cols = {c: signal_names[c.split(":", 1)[1].strip().lower()] for c in lcols
                if c.startswith("Expected:") and c.split(":", 1)[1].strip().lower() in signal_names}

    icp = {"right": 0, "wrong": 0, "pending": 0, "wrong_no": 0, "wrong_yes": 0}
    tier = {"right": 0, "wrong": 0, "not_scored": 0}
    per_signal = {sid: {"right": 0, "wrong": 0, "empty": 0} for sid in sig_cols.values()}
    issues = []
    for lab in lrows:
        d = normalize_domain(lab.get(dcol, ""))
        got = by_domain.get(d)
        if not d or not got:
            continue
        name = lab.get("Company Name") or lab.get("Company") or d
        exp = _norm(lab.get("Expected ICP"))
        if exp in ("yes", "no"):
            g = _norm(got.get("ICP"))
            if g == "pending":
                icp["pending"] += 1
                issues.append((name, d, "ICP", exp.upper(), "PENDING", got.get("Why", ""), lab.get("Expected reason", "")))
            elif g == exp:
                icp["right"] += 1
            else:
                icp["wrong"] += 1
                icp["wrong_no" if exp == "yes" else "wrong_yes"] += 1
                issues.append((name, d, "ICP", exp.upper(), g.upper(), got.get("Why", "") or got.get("Score breakdown", ""),
                               lab.get("Expected reason", "")))
        exp_tier = _norm(lab.get("Expected tier"))
        if exp_tier in ("strong fit", "weak fit") and exp != "no":
            g = _norm(got.get("Lead tier")).replace(" (pending ai check)", "")
            if g not in ("strong fit", "weak fit"):
                tier["not_scored"] += 1
            elif g == exp_tier:
                tier["right"] += 1
            else:
                tier["wrong"] += 1
                issues.append((name, d, "Tier", exp_tier.title(), g.title(), got.get("Score breakdown", ""),
                               lab.get("Expected reason", "")))
        for col, sid in sig_cols.items():
            e = _norm(lab.get(col))
            if e not in ("yes", "no"):
                continue
            g = _norm(got.get(signal_column(sid)))
            if g in ("yes", "no"):
                ok = g == e
                per_signal[sid]["right" if ok else "wrong"] += 1
                if not ok:
                    issues.append((name, d, SIGNALS[sid][1], e.title(), g.title(), "", lab.get("Expected reason", "")))
            else:
                per_signal[sid]["empty"] += 1

    out = folder / "output" / "evaluation.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Company", "Domain", "What", "Expected", "Got", "Our proof / breakdown", "Expected reason"])
        w.writerows(issues)

    decided = icp["right"] + icp["wrong"]
    t = Table(title=f"Evaluation: {folder.name}", title_justify="left", header_style="bold")
    for c in ("Check", "Right", "Wrong", "Not decided", "Accuracy (decided)"):
        t.add_column(c, justify="left" if c == "Check" else "right")
    t.add_row("ICP YES / NO", str(icp["right"]), str(icp["wrong"]), str(icp["pending"]),
              f"{icp['right'] / decided:.0%}" if decided else "-")
    td = tier["right"] + tier["wrong"]
    t.add_row("Strong / Weak fit", str(tier["right"]), str(tier["wrong"]), str(tier["not_scored"]),
              f"{tier['right'] / td:.0%}" if td else "-")
    for sid, c in per_signal.items():
        n = c["right"] + c["wrong"]
        t.add_row(SIGNALS[sid][1], str(c["right"]), str(c["wrong"]), str(c["empty"]), f"{c['right'] / n:.0%}" if n else "-")
    console.print(t)
    style = "red" if icp["wrong_no"] else "green"
    console.print(f"[{style}]Good companies wrongly marked NO: {icp['wrong_no']}[/]   "
                  f"Companies wrongly marked YES: {icp['wrong_yes']}")
    if issues:
        it = Table(title="Disagreements", title_justify="left", header_style="bold dim", show_lines=False)
        for c in ("Company", "What", "Expected", "Got", "Our proof"):
            it.add_column(c, overflow="fold")
        for name, _, what, e, g, proof, _ in issues[:25]:
            it.add_row(name, what, e, g, proof[:140])
        console.print(it)
    console.print(f"Every disagreement with its proof: {rel(out)}")
    return {"icp": icp, "tier": tier, "signals": {SIGNALS[k][1]: v for k, v in per_signal.items()}, "issues": len(issues)}


def write_template(path: Path) -> Path:
    cols = (["Company Name", "Domain", "Expected ICP", "Expected tier", "Expected reason"]
            + [f"Expected: {SIGNALS[sid][1]}" for sid in TEMPLATE_SIGNALS])
    example = {"Company Name": "Acme", "Domain": "acme.com", "Expected ICP": "YES", "Expected tier": "Strong fit",
               "Expected reason": "Puzzle customer since 2025",
               f"Expected: {SIGNALS['M1'][1]}": "Yes", f"Expected: {SIGNALS['F3'][1]}": "Yes"}
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerow(example)
    return path
