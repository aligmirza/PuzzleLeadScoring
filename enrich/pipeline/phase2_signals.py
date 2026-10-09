"""Phase 2: signals, for companies that passed phase 1.

Starts from phase 1's saved pages, downloads the extra pages signals need (team, blog, integrations,
customers, locations, menu, engineering blog), then runs the fit, buying and weak signal rules.

Outputs in lists/<list>/output/: signals.csv, signals_evidence.jsonl, signals_ai_queue.jsonl
"""
import asyncio
from pathlib import Path

from rich.live import Live

from .common import (ai_columns, ai_summary, console, files_and_disk, offer_cleanup, run_rules, signal_columns,
                     signal_summary, write_outputs)
from ..sources.crawl import ICP_KINDS, SIGNAL_KINDS, Crawler, slim_html
from . import final
from ..core.dashboard import Dashboard
from ..core.inputs import load_leads
from ..checks.rules import SIGNALS
from ..core.store import Store

OUTPUTS = {"csv": "signals.csv", "evidence": "signals_evidence.jsonl", "ai_queue": "signals_ai_queue.jsonl"}
GROUPS = ("fit", "buying", "weak")
PASSING = ("Yes", "Yes (some must-haves unconfirmed)", "Needs AI check")


def select(store: Store, rows: list[dict], only_yes: bool) -> tuple[list[dict], dict]:
    """Companies phase 1 passed. With only_yes, leave out 'Needs AI check'."""
    allowed = PASSING[:2] if only_yes else PASSING
    picked, skipped = [], {"not checked in phase 1": 0, "not ICP or not reached": 0, "needs AI check (left out)": 0}
    for row in rows:
        r = store.get_result(row["domain"], "icp")
        if not r:
            skipped["not checked in phase 1"] += 1
        elif r.get("icp_verdict") in allowed:
            row["icp"] = r
            picked.append(row)
        elif r.get("icp_verdict") == "Needs AI check":
            skipped["needs AI check (left out)"] += 1
        else:
            skipped["not ICP or not reached"] += 1
    return picked, skipped


async def _crawl_and_check(rows, store: Store, dash: Dashboard, workers: int, max_requests: int, refresh: bool) -> list[dict]:
    crawler = Crawler(max_requests=max_requests)
    queue: asyncio.Queue = asyncio.Queue()
    for row in rows:
        queue.put_nowait(row)
    results: list[dict] = []

    async def worker():
        while True:
            try:
                row = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            d = row["domain"]
            record = store.get_site(d) or {"domain": d, "pages": {}}
            cached = bool(record.get("signals_crawled")) and not refresh
            if not cached:
                try:
                    home = store.load_page(d, "home")
                    if home:
                        pages = await asyncio.wait_for(crawler.crawl_more(record, home, SIGNAL_KINDS), timeout=90)
                        jobs = None
                    else:  # phase 1 pages were deleted: download everything again
                        record, pages, jobs = await asyncio.wait_for(crawler.crawl(d, ICP_KINDS | SIGNAL_KINDS), timeout=120)
                        record["requests_phase2"] = record.get("requests", 0)
                except Exception as e:  # noqa: BLE001  one bad site must not stop the run
                    pages, jobs = {}, None
                    record["phase2_error"] = f"{type(e).__name__}: {e}"[:200]
                for kind, html in pages.items():
                    store.save_page(d, kind, await asyncio.to_thread(slim_html, html))
                if jobs:
                    store.save_jobs(d, jobs)
                record["signals_crawled"] = True
                record.pop("pages_deleted", None)
                record.setdefault("input", {"company": row["company"], "employees": row["employees"]})
                store.save_site(d, record)
            dash.crawled(record, cached)
            dash.c["bytes_saved"] += store.pages_size(d)
            try:
                result = await asyncio.to_thread(run_rules, store, row, record, "signals")
            except Exception as e:  # noqa: BLE001
                result = {"domain": d, "error": f"rules failed: {e!r}"}
                dash.progress.update(dash.t_rules, advance=1)
            else:
                result["icp_verdict"] = row["icp"].get("icp_verdict")  # phase 1 decides ICP
                store.save_result(d, result, "signals")
                dash.ruled(result)
            results.append(result)

    try:
        await asyncio.gather(*(worker() for _ in range(workers)))
    finally:
        await crawler.close()
    return results


def csv_row(r: dict) -> dict:
    if "signals" not in r:
        return {"Domain": r["domain"], "Notes": r.get("error", "")}
    s = r["signals"]
    found = {g: "; ".join(SIGNALS[k][1] for k in s if SIGNALS[k][0] == g and s[k]["value"] == "yes") for g in GROUPS}
    roles = r["crawl"].get("jobs_count")
    return {
        "Domain": r["domain"],
        "Company": r["company"],
        "Employees (from your list)": r["employees_input"],
        "Is it ICP? (phase 1)": r.get("icp_verdict", ""),
        "Fit signals found": found["fit"],
        "Buying signals found": found["buying"],
        "Weak fit signals found": found["weak"],
        "Fit signals (count)": len([1 for k in s if SIGNALS[k][0] == "fit" and s[k]["value"] == "yes"]),
        "Weak fit signals (count)": len([1 for k in s if SIGNALS[k][0] == "weak" and s[k]["value"] == "yes"]),
        "Legal entity type": r["entity_type"],
        "Funding stage": r["funding_stage"],
        "Partners found": ", ".join(r["partners"]),
        "Found in directories": r.get("directories", ""),
        "Competitor tools used": ", ".join(r["competitors"]),
        "Competitor tools mentioned on site": ", ".join(r["competitors_mentioned"]),
        "Job board": (r["crawl"].get("ats") or "").capitalize(),
        "Open roles": roles if roles is not None else "",
        "Prices found": " | ".join(r["facts"].get("prices", [])[:3]),
        **signal_columns(r, GROUPS),
        "Needs checking": "; ".join(SIGNALS[k][1] for k in s if SIGNALS[k][0] in GROUPS and s[k]["value"] == "check"),
        **ai_columns(r),
        "Pages read": ", ".join(r["crawl"]["pages"]),
    }


def report(results: list[dict], folder: Path, store: Store) -> None:
    results.sort(key=lambda r: -sum(1 for v in r.get("signals", {}).values() if v["value"] == "yes"))
    paths = write_outputs(results, [csv_row(r) for r in results], folder / "output", OUTPUTS)
    signal_summary(results, GROUPS, "Phase 2: fit, buying and weak signals")
    ai_summary(results)
    files_and_disk(paths, store)
    out = final.write(folder, store)
    from ..core.runlog import log
    import csv
    with open(out, newline="") as f:
        tiers = [r.get("Lead tier", "") for r in csv.DictReader(f)]
    log(folder, "Phase 2 (signals)", companies=len(results), strong_fit=sum(t.startswith("Strong") for t in tiers),
        weak_fit=sum(t.startswith("Weak") for t in tiers))


def run(folder: Path, only_yes: bool = False, workers: int = 50, max_requests: int = 200, refresh: bool = False,
        keep: str = "useful", yes: bool = False) -> None:
    store = Store(folder)
    rows, _ = load_leads(folder / "input.csv")
    picked, skipped = select(store, rows, only_yes)
    left_out = ", ".join(f"{v} {k}" for k, v in skipped.items() if v) or "none"
    console.print(f"[bold]Phase 2: signals[/]  list [bold]{folder.name}[/]   {len(picked)} companies passed phase 1 "
                  f"(left out: {left_out})")
    if not picked:
        console.print("Nothing to do. Run phase 1 first: python -m enrich icp <csv>")
        return
    dash = Dashboard(f"Phase 2, signals  {folder.name}", len(picked), mode="signals")
    with Live(dash, console=console, refresh_per_second=4, vertical_overflow="visible"):
        results = asyncio.run(_crawl_and_check(picked, store, dash, workers, max_requests, refresh))
    report(results, folder, store)
    offer_cleanup(store, keep, yes)


def rerun_rules(folder: Path) -> None:
    """Re-apply phase 2 rules to saved pages, no downloading."""
    store = Store(folder)
    rows, _ = load_leads(folder / "input.csv")
    picked, _ = select(store, rows, only_yes=False)
    picked = [r for r in picked if (store.get_site(r["domain"]) or {}).get("signals_crawled")]
    dash = Dashboard(f"Phase 2 rules again: {folder.name}", len(picked), mode="signals")
    results = []
    with Live(dash, console=console, refresh_per_second=4):
        for row in picked:
            record = store.get_site(row["domain"])
            dash.crawled(record, cached=True)
            r = run_rules(store, row, record, "signals")
            r["icp_verdict"] = row["icp"].get("icp_verdict")
            store.save_result(row["domain"], r, "signals")
            dash.ruled(r)
            results.append(r)
    report(results, folder, store)
