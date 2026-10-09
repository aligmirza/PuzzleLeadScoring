"""Phase 1: is it our ICP?

Downloads only the pages needed for must-haves and exclusions, runs only those rules, and gives each
company a verdict: Yes, Yes (some must-haves unconfirmed), Needs AI check, No, or Unknown (site not reached).

Outputs in lists/<list>/output/: icp_check.csv, icp_evidence.jsonl, icp_ai_queue.jsonl
"""
import asyncio
from pathlib import Path

from rich.live import Live

from .common import (ai_columns, ai_summary, console, files_and_disk, offer_cleanup, plain, prepare_list, rel,
                     run_rules, signal_columns, signal_summary, write_outputs)
from .crawl import ICP_KINDS, Crawler, slim_html
from . import final
from .dashboard import Dashboard
from .inputs import load_leads
from .rules import SIGNALS
from .store import Store

OUTPUTS = {"csv": "icp_check.csv", "evidence": "icp_evidence.jsonl", "ai_queue": "icp_ai_queue.jsonl"}
GROUPS = ("must", "exclusion")


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
            record = None if refresh else store.get_site(d)
            cached = record is not None
            if not cached:
                try:
                    record, pages, jobs = await asyncio.wait_for(crawler.crawl(d, ICP_KINDS), timeout=90)
                except asyncio.TimeoutError:
                    record, pages, jobs = {"domain": d, "status": "unreachable", "error": "took longer than 90s", "pages": {}}, {}, []
                except Exception as e:  # noqa: BLE001  one bad site must not stop the run
                    record, pages, jobs = {"domain": d, "status": "error", "error": f"crawler bug: {e!r}"[:200], "pages": {}}, {}, []
                for kind, html in pages.items():
                    store.save_page(d, kind, await asyncio.to_thread(slim_html, html))
                if jobs:
                    store.save_jobs(d, jobs)
                record["input"] = {"company": row["company"], "employees": row["employees"]}
                store.save_site(d, record)
            dash.crawled(record, cached)
            if cached and record.get("pages_deleted") and store.get_result(d, "icp"):
                result = store.get_result(d, "icp")  # pages were cleaned up earlier; reuse the saved result
                dash.ruled(result)
                results.append(result)
                continue
            dash.c["bytes_saved"] += store.pages_size(d)
            try:
                result = await asyncio.to_thread(run_rules, store, row, record, "icp")
            except Exception as e:  # noqa: BLE001  keep the run going; report at the end
                result = {"domain": d, "error": f"rules failed: {e!r}"}
                dash.progress.update(dash.t_rules, advance=1)
            else:
                dash.ruled(result)
            results.append(result)

    try:
        await asyncio.gather(*(worker() for _ in range(workers)))
    finally:
        await crawler.close()
    return results


def csv_row(r: dict) -> dict:
    if "signals" not in r:
        return {"Domain": r["domain"], "Is it ICP?": "Error", "Why": r.get("error", "")}
    s = r["signals"]
    why = plain(r["exclusion_reason"]) or "; ".join(
        f"{SIGNALS[k][1]}: {s[k]['evidence']}" for k in s if SIGNALS[k][0] == "must" and s[k]["value"] == "no")
    if not why and r.get("icp_checks"):
        why = "To confirm: " + "; ".join(r["icp_checks"])
    if not why and r["crawl"]["status"] in ("blocked", "unreachable", "error"):
        why = f"Site not reached: {r['crawl'].get('error') or r['crawl']['status']}"
    roles = r["crawl"].get("jobs_count")
    return {
        "Domain": r["domain"],
        "Company": r["company"],
        "Employees (from your list)": r["employees_input"],
        "Is it ICP?": r.get("icp_verdict", ""),
        "Why": why,
        "Must-haves confirmed (of 4)": r["must_haves_confirmed"],
        "Website status": r["crawl"]["status"].capitalize(),
        "Legal entity type": r["entity_type"],
        "Legal name": r["legal_name"] or "",
        "Open roles": roles if roles is not None else "",
        "Prices found": " | ".join(r["facts"].get("prices", [])[:3]),
        **signal_columns(r, GROUPS),
        "Found in directories": r.get("directories", ""),
        "Needs checking": "; ".join(r.get("icp_checks", [])),
        **ai_columns(r),
        "Pages read": ", ".join(r["crawl"]["pages"]),
    }


def report(results: list[dict], folder: Path, store: Store, input_report: dict | None) -> None:
    order = {"Yes": 0, "Yes (some must-haves unconfirmed)": 1, "Needs AI check": 2, "Unknown: site not reached": 3, "No": 4}
    results.sort(key=lambda r: order.get(r.get("icp_verdict", ""), 9))
    paths = write_outputs(results, [csv_row(r) for r in results], folder / "output", OUTPUTS)
    signal_summary(results, GROUPS, "Phase 1: must-haves and exclusions")
    if input_report:
        console.print(f"Input: {input_report['total']} rows, {input_report['bad_domain']} bad domains, "
                      f"{input_report['duplicates']} duplicates. Columns used: {input_report['columns']}")
    counts: dict[str, int] = {}
    for r in results:
        counts[r.get("icp_verdict", "Error")] = counts.get(r.get("icp_verdict", "Error"), 0) + 1
    console.print("[bold]Is it ICP?[/] " + ", ".join(f"{k}: {counts[k]}" for k in sorted(counts, key=lambda k: order.get(k, 9))))
    ai_summary(results)
    files_and_disk(paths, store)
    final.write(folder, store)
    nxt = sum(v for k, v in counts.items() if k.startswith("Yes") or k == "Needs AI check")
    console.print(f"\nNext: [bold]python -m enrich signals {folder.name}[/] checks signals for the {nxt} companies that passed.")


def run(csv_path: Path, list_name: str | None = None, workers: int = 50, max_requests: int = 200, limit: int = 0,
        refresh: bool = False, keep: str = "useful", yes: bool = False) -> Path:
    folder, rows, input_report = prepare_list(csv_path, list_name)
    if limit:
        rows = rows[:limit]
    console.print(f"[bold]Phase 1: ICP check[/]  list [bold]{folder.name}[/] -> {rel(folder)}/   {len(rows)} companies "
                  f"({input_report['duplicates']} duplicates and {input_report['bad_domain']} bad domains skipped)")
    store = Store(folder)
    dash = Dashboard(f"Phase 1, is it ICP?  {folder.name}", len(rows), mode="icp")
    with Live(dash, console=console, refresh_per_second=4, vertical_overflow="visible"):
        results = asyncio.run(_crawl_and_check(rows, store, dash, workers, max_requests, refresh))
    report(results, folder, store, input_report)
    offer_cleanup(store, keep, yes)
    return folder


def rerun_rules(folder: Path) -> None:
    """Re-apply phase 1 rules to saved pages, no downloading."""
    store = Store(folder)
    rows, input_report = load_leads(folder / "input.csv")
    dash = Dashboard(f"Phase 1 rules again: {folder.name}", len(rows), mode="icp")
    results, kept_old = [], 0
    with Live(dash, console=console, refresh_per_second=4):
        for row in rows:
            record = store.get_site(row["domain"])
            if not record:
                continue
            dash.crawled(record, cached=True)
            if record.get("pages_deleted"):
                r = store.get_result(row["domain"], "icp")
                if not r:
                    continue
                kept_old += 1
            else:
                r = run_rules(store, row, record, "icp")
            dash.ruled(r)
            results.append(r)
    if kept_old:
        console.print(f"[yellow]{kept_old} companies had their pages deleted, so their earlier results were kept.[/]")
    report(results, folder, store, input_report)
