"""Speed benchmark of the free steps (download, job boards, code scan, directories, rules; no AI, no Prospeo): $0.

    python -m enrich bench examples/leads.csv --label before        a fresh list bench_before, timed per stage
    python -m enrich bench --compare before after                    speed and every answer that differs

Runs the same path as the MCP and API (service.run_rows), downloads fresh (nothing reused from other lists), and saves
lists/bench_<label>/output/bench.json: wall time, seconds per stage, and every company's answers, so two runs can be
compared answer by answer.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from rich.table import Table

from ..core import timing
from .common import LISTS, console, prepare_list


def _out(label: str) -> Path:
    return LISTS / f"bench_{label}" / "output" / "bench.json"


def run(csv_path: Path, label: str, workers: int = 50, limit: int = 0) -> dict:
    from ..core.store import Store
    from ..integrations import service
    if _out(label).exists():
        raise SystemExit(f"bench_{label} already exists; use another --label.")
    folder, rows, _ = prepare_list(csv_path, f"bench_{label}")
    rows = rows[:limit] if limit else rows
    console.print(f"Benchmark [bold]{label}[/]: {len(rows)} companies, {workers} at a time, free steps only, fresh download")
    timing.reset()
    t0 = time.perf_counter()
    lag = {"total": 0.0, "worst": 0.0}

    async def watch():  # how long the event loop was blocked: no download can progress meanwhile
        while True:
            t = time.perf_counter()
            await asyncio.sleep(0.05)
            late = time.perf_counter() - t - 0.05
            if late > 0.01:
                lag["total"] += late
                lag["worst"] = max(lag["worst"], late)

    async def go():
        w = asyncio.ensure_future(watch())
        try:
            await service.run_rows(folder, rows, signals=True, use_ai=False, refresh=True, workers=workers)
        finally:
            w.cancel()
    asyncio.run(go())
    wall = time.perf_counter() - t0
    store = Store(folder)
    answers = {}
    for r in rows:
        d = r["domain"]
        icp, sig = store.get_result(d, "icp") or {}, store.get_result(d, "signals") or {}
        answers[d] = {"icp": icp.get("icp_verdict"),
                      "signals": {k: v["value"] for k, v in {**icp.get("signals", {}), **sig.get("signals", {})}.items()}}
    out = {"label": label, "companies": len(rows), "workers": workers, "wall_s": round(wall, 1),
           "loop_blocked_s": round(lag["total"], 1), "loop_blocked_worst_s": round(lag["worst"], 2),
           "stages": timing.summary(), "answers": answers}
    _out(label).parent.mkdir(parents=True, exist_ok=True)
    _out(label).write_text(json.dumps(out, indent=1))
    show(out)
    return out


def show(b: dict) -> None:
    console.print(f"[bold]{b['label']}[/]: {b['companies']} companies in [bold]{b['wall_s']} s[/] ({b['workers']} at a time)"
                  + (f"; event loop blocked {b['loop_blocked_s']} s in total (worst {b['loop_blocked_worst_s']} s)"
                     if "loop_blocked_s" in b else ""))
    t = Table("Stage", "Companies", "Total s", "Median s", "Slowest s", header_style="bold")
    for name, s in b["stages"].items():
        t.add_row(name, str(s["companies"]), str(s["total_s"]), str(s["median_s"]), str(s["slowest_s"]))
    console.print(t)


def compare(a_label: str, b_label: str) -> dict:
    a, b = (json.loads(_out(x).read_text()) for x in (a_label, b_label))
    show(a)
    show(b)
    console.print(f"\nWall time: {a['wall_s']} s → {b['wall_s']} s ([bold]{a['wall_s'] / max(b['wall_s'], 0.1):.1f}× faster[/])")
    diffs = []
    for d in sorted(set(a["answers"]) | set(b["answers"])):
        x, y = a["answers"].get(d, {}), b["answers"].get(d, {})
        if x.get("icp") != y.get("icp"):
            diffs.append((d, "ICP", x.get("icp"), y.get("icp")))
        for sid in sorted(set(x.get("signals", {})) | set(y.get("signals", {}))):
            if x.get("signals", {}).get(sid) != y.get("signals", {}).get(sid):
                diffs.append((d, sid, x.get("signals", {}).get(sid), y.get("signals", {}).get(sid)))
    if diffs:
        t = Table("Company", "Data point", a_label, b_label, header_style="bold", title="Answers that differ")
        for row in diffs:
            t.add_row(*map(str, row))
        console.print(t)
    else:
        console.print("[green]Every answer is the same.[/]")
    return {"before_s": a["wall_s"], "after_s": b["wall_s"], "differences": diffs}
