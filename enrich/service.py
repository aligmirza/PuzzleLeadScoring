"""Run the pipeline from code instead of the terminal. Used by the API and the MCP server.

check_one()   one company, answered while you wait (for Clay's per-row HTTP API column)
start_list()  a whole list in the background: phase 1, optional AI, phase 2, optional AI, final list,
              optional push to a Clay webhook. Progress is in lists/<name>/status.json.
"""
import asyncio
import csv
import json
import time
from pathlib import Path

from . import clay, final, phase1_icp, phase2_signals
from .common import LISTS, console, list_name
from .dashboard import Dashboard
from .inputs import load_leads, normalize_domain
from .store import Store

SINGLE_LIST = "api_checks"          # where one-off company checks are cached
_inflight: dict[str, asyncio.Task] = {}
_jobs: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
async def _ai(store: Store, domains: list[str], phase: str, web: bool) -> None:
    from . import ai
    try:
        runner = ai.Runner(store, phase, web, redo=False, concurrency=8)
    except SystemExit as e:  # e.g. OPENAI_API_KEY missing
        raise RuntimeError(str(e))
    try:
        await asyncio.gather(*(runner.company(d) for d in domains))
    except SystemExit as e:
        raise RuntimeError(str(e))
    finally:
        await runner.client.close()


async def run_rows(folder: Path, rows: list[dict], signals: bool = True, use_ai: bool = False, web: bool = True,
                   refresh: bool = False, workers: int = 20, on_step=None) -> Store:
    store = Store(folder)
    step = on_step or (lambda s: None)
    step("phase 1: ICP check")
    await phase1_icp._crawl_and_check(rows, store, Dashboard("", len(rows), "icp"), workers, 200, refresh)
    if use_ai:
        step("AI for phase 1")
        await _ai(store, [r["domain"] for r in rows], "icp", web)
    if signals:
        picked, _ = phase2_signals.select(store, rows, only_yes=False)
        if picked:
            step("phase 2: signals")
            await phase2_signals._crawl_and_check(picked, store, Dashboard("", len(picked), "signals"), workers, 200, refresh)
            if use_ai:
                step("AI for phase 2")
                await _ai(store, [r["domain"] for r in picked], "signals", web)
    return store


def findings(store: Store, domain: str, company: str = "", employees: str = "") -> dict:
    out = final.findings(domain, employees, store.get_result(domain, "icp"), store.get_result(domain, "signals"))
    return {"domain": domain, "company": company, **out}


# ---------------------------------------------------------------------------
async def check_one(domain: str, company: str = "", employees: str = "", signals: bool = True, use_ai: bool = False,
                    web: bool = True, refresh: bool = False, wait_seconds: float = 45) -> dict:
    """Check one company. Results are cached, so asking again is instant. If the check takes longer than
    wait_seconds, it keeps running and the answer says to ask again."""
    d = normalize_domain(domain)
    if not d:
        raise ValueError(f"'{domain}' is not a valid domain")
    folder = LISTS / SINGLE_LIST
    folder.mkdir(parents=True, exist_ok=True)
    row = {"domain": d, "company": company, "employees": employees, "input": {}}
    task = _inflight.get(d)
    if task is None or task.done():
        task = asyncio.ensure_future(run_rows(folder, [row], signals, use_ai, web, refresh, workers=1))
        _inflight[d] = task
    try:
        store = await asyncio.wait_for(asyncio.shield(task), timeout=wait_seconds)
    except asyncio.TimeoutError:
        return {"domain": d, "company": company, "status": "still running",
                "message": f"Still checking {d}; call again in a minute for the result."}
    return {"status": "done", **findings(store, d, company, employees)}


# ---------------------------------------------------------------------------
def _write_input(folder: Path, rows: list[dict]) -> None:
    cols = list(dict.fromkeys(k for r in rows for k in r))
    if not any(c.strip().lower() in ("domain", "website", "url", "company domain", "company website") for c in cols):
        raise ValueError("Rows need a domain column: one of Domain, Website, URL, Company Domain")
    with open(folder / "input.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def _status(folder: Path, **update) -> dict:
    path = folder / "status.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.update(update)
    path.write_text(json.dumps(data, indent=2))
    return data


def start_list(name: str | None, rows: list[dict], signals: bool = True, use_ai: bool = False, web: bool = True,
               push_to_clay: bool = False, clay_webhook_url: str | None = None, source: str = "api") -> dict:
    """Save the rows as a new list and process it in the background. Must be called inside a running event loop."""
    if not rows:
        raise ValueError("No rows given")
    name = list_name(Path(name or f"{source}_{time.strftime('%Y%m%d_%H%M%S')}"))
    if name in _jobs and not _jobs[name].done():
        raise ValueError(f"List '{name}' is already running")
    folder = LISTS / name
    folder.mkdir(parents=True, exist_ok=True)
    _write_input(folder, rows)
    status = _status(folder, list=name, state="queued", step="", rows=len(rows), source=source,
                     options={"signals": signals, "ai": use_ai, "web": web, "push_to_clay": push_to_clay},
                     started=time.strftime("%Y-%m-%d %H:%M:%S"), finished=None, error=None, push=None)
    _jobs[name] = asyncio.ensure_future(_run_list(folder, signals, use_ai, web, push_to_clay, clay_webhook_url))
    return status


async def _run_list(folder: Path, signals, use_ai, web, push_to_clay, webhook) -> None:
    try:
        _status(folder, state="running")
        rows, _ = load_leads(folder / "input.csv")
        store = await run_rows(folder, rows, signals, use_ai, web, on_step=lambda s: _status(folder, step=s))
        _status(folder, step="writing results")
        icp = [r for r in (store.get_result(row["domain"], "icp") for row in rows) if r]
        phase1_icp.report(icp, folder, store, None)
        if signals:
            sig = [r for r in (store.get_result(row["domain"], "signals") for row in rows) if r]
            if sig:
                phase2_signals.report(sig, folder, store)
        final.write(folder, store)
        if push_to_clay:
            _status(folder, step="pushing to Clay")
            _status(folder, push=await clay.push_rows(clay.final_rows(folder), webhook))
        _status(folder, state="done", step="", finished=time.strftime("%Y-%m-%d %H:%M:%S"), summary=summary(folder))
    except Exception as e:  # noqa: BLE001  report the failure in the status instead of crashing the server
        console.print_exception()
        _status(folder, state="error", error=f"{type(e).__name__}: {e}"[:500], finished=time.strftime("%Y-%m-%d %H:%M:%S"))


def summary(folder: Path) -> dict:
    try:
        rows = clay.final_rows(folder)
    except RuntimeError:
        return {}
    count = lambda k, v: sum(1 for r in rows if r.get(k) == v)  # noqa: E731
    return {"rows": len(rows), "icp_yes": count("ICP", "YES"), "icp_no": count("ICP", "NO"),
            "icp_pending": count("ICP", "PENDING"), "strong_fit": count("Lead tier", "Strong fit")}


def list_status(name: str) -> dict:
    folder = LISTS / list_name(Path(name))
    if not (folder / "input.csv").exists():
        raise ValueError(f"No list called '{name}'")
    path = folder / "status.json"
    data = json.loads(path.read_text()) if path.exists() else {"list": folder.name, "state": "done (run from the terminal)"}
    data["summary"] = summary(folder)
    return data


def list_results(name: str, only_icp: bool = False, limit: int = 0) -> list[dict]:
    rows = clay.final_rows(LISTS / list_name(Path(name)), only_icp)
    return rows[:limit] if limit else rows


async def push_list(name: str, webhook_url: str | None = None, only_icp: bool = False) -> dict:
    return await clay.push_rows(list_results(name, only_icp), webhook_url)


def start_from_clay(table_id: str, name: str | None = None, limit: int = 0, **options) -> dict:
    rows, info = clay.pull_table(table_id, limit)
    if not rows:
        raise ValueError(f"Clay table {table_id} has no rows")
    status = start_list(name or f"clay_{table_id}", rows, source="clay", **options)
    status["clay"] = {"table_id": table_id, "columns": info["columns"], "truncated": info["truncated"]}
    return status
