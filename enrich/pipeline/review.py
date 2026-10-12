"""Review mode: you check each data point's waterfall and mark it right or wrong.

    python -m enrich review linear.app                one company, the 4 must-haves
    python -m enrich review accuracy_test             every company in a list
    python -m enrich review leads --group all         must, exclusions, fit, buying, weak or all

For each data point: Enter = right, n = wrong (then the right answer and a note), s = skip, q = stop.
At the end of each company you can also judge the ICP verdict. Stopping is safe: next time, what you already
reviewed is skipped (--redo shows it again).

Saved in the list's output folder:
    review.csv    every verdict with our answer, the layer that decided and the proof; ask Claude to "check the review"
    labels.csv    your right answers in evaluate's format: python -m enrich evaluate <list> --labels .../labels.csv
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

from rich.prompt import Prompt

from ..checks.rules import SIGNALS
from ..core.inputs import load_leads
from ..core.store import Store
from . import waterfall
from .common import LISTS, console, rel

FIELDS = ["Reviewed at", "Domain", "Company", "Data point", "Our answer", "Decided by", "Our proof", "Page",
          "Your verdict", "Right answer", "Note"]
GROUP_TITLES = {"must": "Must-haves", "exclusion": "Exclusions", "fit": "Fit signals", "buying": "Buying signals",
                "weak": "Weak fit signals"}
ANSWERS = {"y": "Yes", "yes": "Yes", "n": "No", "no": "No", "u": "", "": ""}


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _save(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def _labels(review: list[dict], path: Path) -> None:
    """Your answers as evaluate labels: one row per company, 'Expected ICP' and 'Expected: <data point>'."""
    by: dict[str, dict] = {}
    for r in review:
        if r["Your verdict"] not in ("right", "wrong"):
            continue
        row = by.setdefault(r["Domain"], {"Domain": r["Domain"], "Company Name": r["Company"]})
        answer = r["Right answer"] if r["Your verdict"] == "wrong" else r["Our answer"]
        if r["Data point"] == "ICP verdict":
            row["Expected ICP"] = answer
        else:
            name = r["Data point"].split(": ", 1)[-1]
            row[f"Expected: {name}"] = answer if answer in ("Yes", "No") else ""
    cols = list(dict.fromkeys(k for row in by.values() for k in row))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(by.values())


def _ask(text: str, default: str = "") -> str:
    try:
        return Prompt.ask(text, default=default, show_default=False).strip()
    except (EOFError, KeyboardInterrupt):
        return "q"


def run(target: str, group: str = "must", redo: bool = False, refresh: bool = False, only: list[str] | None = None) -> None:
    if not sys.stdin.isatty():
        raise SystemExit("Review needs a terminal to answer in. Run it in your own terminal.")
    folder = LISTS / target
    if (folder / "input.csv").exists():
        rows, _ = load_leads(folder / "input.csv")
        companies = [(r["domain"], r["company"]) for r in rows]
    else:  # a single company: prepare it like the waterfall does (free layers)
        from ..core.inputs import normalize_domain
        domain = normalize_domain(target) or target
        console.print(f"[dim]Preparing {domain} (free layers)...[/]")
        from . import trace
        console.quiet = True
        try:
            trace.run(domain, "", "", False, None, pause=False, refresh=refresh)
        finally:
            console.quiet = False
        folder = LISTS / f"trace_{domain.replace('.', '_')}"
        companies = [(domain, "")]
    store = Store(folder)
    (folder / "output").mkdir(exist_ok=True)
    review_path, labels_path = folder / "output" / "review.csv", folder / "output" / "labels.csv"
    review = _load(review_path)
    done = {(r["Domain"], r["Data point"]) for r in review}
    groups = waterfall.GROUPS["all" if only else group]
    only = [x.upper() for x in only] if only else None
    if only and any(x not in SIGNALS for x in only):
        raise SystemExit(f"Unknown data point id in {only}. Ids: M1-M4, E1-E6, F1-F21, B1-B14, W1-W12.")
    counts = {"right": 0, "wrong": 0, "skipped": 0}
    console.print(f"[bold]Review[/] {folder.name}, group: {group}. For each data point: [bold]Enter[/] right, [bold]n[/] wrong, "
                  f"[bold]s[/] skip, [bold]q[/] stop. Saved as you go to {rel(review_path)}.")
    for domain, company in dict.fromkeys(companies):
        icp = store.get_result(domain, "icp")
        if not icp:
            continue
        ctx = waterfall.context(store, domain, company)
        in_running = icp.get("icp_verdict") not in ("No", "Unknown: site not reached")
        # phase 2 only counts for companies still in the running; for a NO company its answers are never used
        sids = [sid for sid, (g, _) in SIGNALS.items() if (sid in only if only else g in groups)
                and (g in ("must", "exclusion") or in_running)]
        todo = [sid for sid in sids if redo or (domain, waterfall.signal_column(sid)) not in done]
        if not todo and ((domain, "ICP verdict") in done or only):
            continue
        console.rule(f"[bold]{ctx['company'] or domain}[/] ({domain}): ICP {icp.get('icp_verdict')}"
                     + ("" if in_running or set(groups) <= {"must", "exclusion"} else " (phase 2 not reviewed: it never counts for a NO)"))
        section = None
        for n, sid in enumerate(todo, start=1):
            dp = waterfall.data_point(ctx, sid)
            if not dp:
                continue
            if SIGNALS[sid][0] != section:
                section = SIGNALS[sid][0]
                console.print(f"\n[bold reverse] {GROUP_TITLES[section]} [/]")
            for part in waterfall.render(dp):
                console.print(part)
            choice = _ask(f"[bold]Right?[/] ({n}/{len(todo)}) Enter = yes, n = no, s = skip, c = skip rest of company, q = stop").lower()
            if choice == "q":
                _finish(review, review_path, labels_path, counts)
                return
            if choice == "c":
                break
            verdict, right, note = "right", "", ""
            if choice == "s":
                verdict = "skipped"
            elif choice in ("n", "no"):
                verdict = "wrong"
                right = ANSWERS.get(_ask("Right answer: y = Yes, n = No, u = unknown / can't tell").lower(), "")
                note = _ask("What's wrong? (optional, e.g. 'quote is about a customer')")
            counts[verdict if verdict != "skipped" else "skipped"] += 1
            f = dp["final"]
            review = [r for r in review if not (r["Domain"] == domain and r["Data point"] == dp["name"])]
            review.append({"Reviewed at": time.strftime("%Y-%m-%d %H:%M"), "Domain": domain, "Company": ctx["company"],
                           "Data point": dp["name"], "Our answer": dp["final_txt"].split(" (")[0],
                           "Decided by": dp["decided_by"] or "nothing", "Our proof": (f.get("evidence") or f.get("note") or "")[:300],
                           "Page": f.get("url", ""), "Your verdict": verdict, "Right answer": right, "Note": note})
            _save(review_path, review)
        if not only and (redo or (domain, "ICP verdict") not in done):
            reason = icp.get("exclusion_reason") or "; ".join(icp.get("icp_checks", [])) or ""
            console.print(f"[bold]ICP verdict:[/] {icp.get('icp_verdict')}" + (f" ({reason[:160]})" if reason else ""))
            choice = _ask("[bold]Is the ICP verdict right?[/] Enter = yes, n = no, s = skip, q = stop").lower()
            if choice == "q":
                _finish(review, review_path, labels_path, counts)
                return
            if choice != "s":
                ours = "YES" if (icp.get("icp_verdict") or "").startswith("Yes") else "NO" if icp.get("icp_verdict") == "No" else "PENDING"
                right = ours if choice not in ("n", "no") else ("NO" if ours == "YES" else "YES")
                note = _ask("What's wrong? (optional)") if choice in ("n", "no") else ""
                review = [r for r in review if not (r["Domain"] == domain and r["Data point"] == "ICP verdict")]
                review.append({"Reviewed at": time.strftime("%Y-%m-%d %H:%M"), "Domain": domain, "Company": ctx["company"],
                               "Data point": "ICP verdict", "Our answer": ours, "Decided by": icp.get("gate", ""),
                               "Our proof": reason[:300], "Page": "", "Your verdict": "wrong" if choice in ("n", "no") else "right",
                               "Right answer": right if choice in ("n", "no") else "", "Note": note})
                counts["wrong" if choice in ("n", "no") else "right"] += 1
                _save(review_path, review)
    _finish(review, review_path, labels_path, counts)


def _finish(review: list[dict], review_path: Path, labels_path: Path, counts: dict) -> None:
    _save(review_path, review)
    _labels(review, labels_path)
    wrong = [r for r in review if r["Your verdict"] == "wrong"]
    console.print(f"\n[bold]This session:[/] {counts['right']} right, {counts['wrong']} wrong, {counts['skipped']} skipped. "
                  f"All reviews so far: {len(review)}, of which {len(wrong)} marked wrong.")
    console.print(f"Review: {rel(review_path)}\nLabels for evaluate: {rel(labels_path)}")
    if wrong:
        console.print("Tell Claude [bold]\"check the review\"[/] to go through what you marked wrong.")
