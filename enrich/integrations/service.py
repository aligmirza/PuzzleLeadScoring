"""Run the pipeline from code instead of the terminal. Used by the API and the MCP server.

check_one()     one company, answered while you wait (for Clay's per-row HTTP API column)
start_list()    a whole list in the background: phase 1, optional AI, phase 2, optional AI, final list,
                optional push to a Clay webhook, optional callback. Progress is in lists/<name>/status.json.
start_ai()      the AI step for an existing list, in the background
also: lists_overview, company_detail, estimate_ai, rerun_rules, evaluate_list, runs, lookup, signals_reference

AI never runs unless asked for, and every AI run is estimated first and refused above max_ai_cost_usd
(default API_AI_MAX_COST_USD in .env, else $5).
"""
import os
import asyncio
import csv
import json
import time
from pathlib import Path

import httpx

from . import clay
from ..pipeline import final
from ..pipeline import phase1_icp
from ..pipeline import phase2_signals
from ..pipeline.common import LISTS, console, list_name
from ..core.dashboard import Dashboard
from ..core.inputs import load_leads, normalize_domain
from ..core.store import Store

SINGLE_LIST = "api_checks"          # where one-off company checks are cached
_inflight: dict[str, asyncio.Task] = {}
_jobs: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
class CostLimit(RuntimeError):
    """The AI estimate is above the caller's limit; nothing was sent."""


def default_max_cost() -> float:
    try:
        return float(os.environ.get("API_AI_MAX_COST_USD") or 5)
    except ValueError:
        return 5.0


def ai_options(ai: dict | None) -> dict:
    """Normalise the AI options of a request: mode, web, search_signals, max_cost_usd."""
    ai = dict(ai or {})
    return {"mode": ai.get("mode") or None, "web": ai.get("web", True), "search_signals": ai.get("search_signals", True),
            "max_cost_usd": float(ai.get("max_cost_usd") or default_max_cost())}


def estimate_ai(name: str, phase: str, mode: str | None = None, search_signals: bool = True, web: bool = True,
                domains: list[str] | None = None) -> dict:
    """What the AI step would cost for a list, in the chosen mode and the other one."""
    from ..ai import runner as ai
    from ..ai import prompts as P
    folder = _folder(name)
    store = Store(folder)
    if domains is None:
        rows, _ = load_leads(folder / "input.csv")
        domains = [r["domain"] for r in rows]
    picked = ai.pick_domains(store, domains, phase)
    out = {"list": folder.name, "phase": phase, "companies": len(picked)}
    for m in ("accurate", "cheap"):
        e = ai.estimate(store, picked, phase, False, search_signals and web, P.mode(m))
        out[m] = {"data_points": e["calls"], "calls_on_pages": e["site_calls"], "step1_usd": round(e["step1_cost"], 4),
                  "web_only_questions": e["search_first"],
                  "web_only_usd": [round(x, 4) for x in e["search_first_cost"]] if web else [0, 0],
                  "worst_case_usd": round(ai.worst_case(e) if web else e["step1_cost"], 4)}
    out["chosen_mode"] = P.mode(mode)["name"]
    return out


async def _ai(store: Store, domains: list[str], phase: str, opts: dict) -> dict:
    """Estimate, refuse above the limit, run, log. Returns the usage."""
    from ..ai import runner as ai
    from ..ai import prompts as P
    from ..core.runlog import log
    picked = ai.pick_domains(store, domains, phase)
    if not picked:
        return {"companies": 0, "cost_usd": 0, "note": "no open AI questions"}
    est = ai.estimate(store, picked, phase, False, opts["search_signals"] and opts["web"], P.mode(opts["mode"]))
    worst = ai.worst_case(est) if opts["web"] else est["step1_cost"]
    if worst > opts["max_cost_usd"]:
        raise CostLimit(f"AI for {phase} could cost up to ${worst:.2f} for {len(picked)} companies, above the limit of "
                        f"${opts['max_cost_usd']:.2f}. Nothing was sent. Raise max_cost_usd, use mode 'cheap', or "
                        f"set search_signals false.")
    usage = await ai.run_async(store, picked, phase, opts["web"], opts["search_signals"], opts["mode"])
    log(store.root, f"AI, {'phase 1' if phase == 'icp' else 'phase 2'} (API)", **usage, estimate_worst_usd=round(worst, 4))
    return {**usage, "estimate_worst_usd": round(worst, 4)}


async def run_rows(folder: Path, rows: list[dict], signals: bool = True, use_ai: bool = False, web: bool = True,
                   refresh: bool = False, workers: int = 20, on_step=None, ai: dict | None = None) -> Store:
    store = Store(folder)
    step = on_step or (lambda s, **kw: None)
    opts = {**ai_options(ai), "web": web if ai is None else ai_options(ai)["web"]}
    step("phase 1: ICP check")
    await phase1_icp._crawl_and_check(rows, store, Dashboard("", len(rows), "icp"), workers, 200, refresh)
    if use_ai:
        step("AI for phase 1")
        step(None, ai_phase1=await _ai(store, [r["domain"] for r in rows], "icp", opts))
    if signals:
        picked, _ = phase2_signals.select(store, rows, only_yes=False)
        if picked:
            step("phase 2: signals")
            await phase2_signals._crawl_and_check(picked, store, Dashboard("", len(picked), "signals"), workers, 200, refresh)
            if use_ai:
                step("AI for phase 2")
                step(None, ai_phase2=await _ai(store, [r["domain"] for r in picked], "signals", opts))
    return store


def findings(store: Store, domain: str, company: str = "", employees: str = "") -> dict:
    out = final.findings(domain, employees, store.get_result(domain, "icp"), store.get_result(domain, "signals"))
    return {"domain": domain, "company": company, **out}


# ---------------------------------------------------------------------------
async def check_one(domain: str, company: str = "", employees: str = "", signals: bool = True, use_ai: bool = False,
                    web: bool = True, refresh: bool = False, wait_seconds: float = 45, ai: dict | None = None) -> dict:
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
        task = asyncio.ensure_future(run_rows(folder, [row], signals, use_ai, web, refresh, workers=1,
                                              ai={**(ai or {}), "web": web}))
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
               push_to_clay: bool = False, clay_webhook_url: str | None = None, source: str = "api",
               ai: dict | None = None, callback_url: str | None = None) -> dict:
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
                     options={"signals": signals, "ai": use_ai, "web": web, "push_to_clay": push_to_clay,
                              "ai_options": {**ai_options(ai), "web": web} if use_ai else None, "callback_url": callback_url},
                     started=time.strftime("%Y-%m-%d %H:%M:%S"), finished=None, error=None, push=None,
                     ai_phase1=None, ai_phase2=None, callback=None)
    _jobs[name] = asyncio.ensure_future(_run_list(folder, signals, use_ai, web, push_to_clay, clay_webhook_url,
                                                  {**(ai or {}), "web": web}, callback_url))
    return status


def _stepper(folder: Path):
    def step(s, **extra):
        _status(folder, **({"step": s} if s is not None else {}), **extra)
    return step


async def _callback(folder: Path, url: str | None) -> None:
    """Tell the caller the list is finished: POST the status (with summary) to their URL, 3 tries."""
    if not url:
        return
    body = json.loads((folder / "status.json").read_text())
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(url, json=body)
            _status(folder, callback={"url": url, "status_code": r.status_code})
            if r.status_code < 500:
                return
        except httpx.HTTPError as e:
            _status(folder, callback={"url": url, "error": str(e)[:200]})
        await asyncio.sleep(2 ** attempt * 5)


async def _run_list(folder: Path, signals, use_ai, web, push_to_clay, webhook, ai=None, callback_url=None) -> None:
    try:
        _status(folder, state="running")
        rows, _ = load_leads(folder / "input.csv")
        store = await run_rows(folder, rows, signals, use_ai, web, on_step=_stepper(folder), ai=ai)
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
    except CostLimit as e:
        _status(folder, state="stopped: AI cost limit", error=str(e), finished=time.strftime("%Y-%m-%d %H:%M:%S"),
                summary=summary(folder))
    except Exception as e:  # noqa: BLE001  report the failure in the status instead of crashing the server
        console.print_exception()
        _status(folder, state="error", error=f"{type(e).__name__}: {e}"[:500], finished=time.strftime("%Y-%m-%d %H:%M:%S"))
    await _callback(folder, callback_url)


def summary(folder: Path) -> dict:
    try:
        rows = clay.final_rows(folder)
    except RuntimeError:
        return {}
    count = lambda k, v: sum(1 for r in rows if r.get(k) == v)  # noqa: E731
    return {"rows": len(rows), "icp_yes": count("ICP", "YES"), "icp_no": count("ICP", "NO"),
            "icp_pending": count("ICP", "PENDING"), "strong_fit": count("Lead tier", "Strong fit")}


def _folder(name: str) -> Path:
    folder = LISTS / list_name(Path(name))
    if not (folder / "input.csv").exists():
        raise ValueError(f"No list called '{name}'")
    return folder


def list_status(name: str) -> dict:
    folder = _folder(name)
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


# ---------------------------------------------------------------------------
def lists_overview() -> list[dict]:
    out = []
    for folder in sorted((f for f in LISTS.glob("*") if (f / "input.csv").exists()), key=lambda f: f.stat().st_mtime, reverse=True):
        path = folder / "status.json"
        state = json.loads(path.read_text()).get("state") if path.exists() else "done (run from the terminal)"
        out.append({"list": folder.name, "state": state, **summary(folder)})
    return out


def company_detail(name: str, domain: str) -> dict:
    """Every answer for one company with its proof, both phases, plus its row in the final list."""
    from ..checks.rules import SIGNALS, VALUE_NAMES, signal_column
    folder = _folder(name)
    d = normalize_domain(domain) or domain
    store = Store(folder)
    icp, sig = store.get_result(d, "icp"), store.get_result(d, "signals")
    if not icp:
        raise ValueError(f"No result for {d} in list '{folder.name}'")
    answers = {}
    for phase, r in (("icp", icp), ("signals", sig)):
        if not r:
            continue
        for sid, v in r["signals"].items():
            if phase == "signals" and SIGNALS[sid][0] in ("must", "exclusion"):
                continue
            answers[signal_column(sid)] = {"answer": VALUE_NAMES[v["value"]], "proof": v["evidence"], "page": v["url"],
                                           "note": v["note"], "source": v.get("method", "code")}
    row = next((r for r in list_results(folder.name) if normalize_domain(
        next((r[k] for k in r if k.strip().lower() in ("domain", "website", "url")), "")) == d), {})
    return {"list": folder.name, "domain": d, "result": {k: v for k, v in row.items() if v not in ("", None)},
            "answers": answers, "directories": store.get_directory(d)}


_ai_jobs: dict[str, asyncio.Task] = {}


def start_ai(name: str, phase: str, ai: dict | None = None) -> dict:
    """The AI step for an existing list, in the background; its result goes to the list's status."""
    folder = _folder(name)
    key = f"{folder.name}:{phase}"
    if key in _ai_jobs and not _ai_jobs[key].done():
        raise ValueError(f"AI for {phase} is already running on '{folder.name}'")
    opts = ai_options(ai)
    est = estimate_ai(folder.name, phase, opts["mode"], opts["search_signals"], opts["web"])
    chosen = est[est["chosen_mode"]]
    if not chosen["data_points"]:
        return {"list": folder.name, "phase": phase, "state": "nothing to do", "estimate": est,
                "message": "No open AI questions in this phase (all answered, or no company needs AI)."}
    if chosen["worst_case_usd"] > opts["max_cost_usd"]:
        raise CostLimit(f"AI for {phase} could cost up to ${chosen['worst_case_usd']:.2f} ({est['chosen_mode']} mode, "
                        f"{est['companies']} companies), above the limit of ${opts['max_cost_usd']:.2f}. Nothing was sent. "
                        f"Cheap mode worst case: ${est['cheap']['worst_case_usd']:.2f}. Raise max_cost_usd, use mode 'cheap', "
                        f"or set search_signals false.")

    async def job():
        store = Store(folder)
        rows, _ = load_leads(folder / "input.csv")
        try:
            _status(folder, state="running", step=f"AI for {phase}")
            usage = await _ai(store, [r["domain"] for r in rows], phase, opts)
            results = [x for x in (store.get_result(r["domain"], phase) for r in rows) if x]
            (phase1_icp.report(results, folder, store, None) if phase == "icp" else phase2_signals.report(results, folder, store))
            final.write(folder, store)
            _status(folder, state="done", step="", **{f"ai_{'phase1' if phase == 'icp' else 'phase2'}": usage},
                    summary=summary(folder), finished=time.strftime("%Y-%m-%d %H:%M:%S"))
        except CostLimit as e:
            _status(folder, state="stopped: AI cost limit", step="", error=str(e))
        except Exception as e:  # noqa: BLE001
            console.print_exception()
            _status(folder, state="error", step="", error=f"{type(e).__name__}: {e}"[:500])
    _ai_jobs[key] = asyncio.ensure_future(job())
    return {"list": folder.name, "phase": phase, "state": "started", "options": opts, "estimate": est}


async def rerun_rules(name: str, phase: str = "both") -> dict:
    """Re-apply the rules (and saved directory and AI answers) to saved pages. Free; no downloading."""
    folder = _folder(name)
    if phase in ("icp", "both"):
        await asyncio.to_thread(phase1_icp.rerun_rules, folder)
    if phase in ("signals", "both"):
        await asyncio.to_thread(phase2_signals.rerun_rules, folder)
    return {"list": folder.name, "phase": phase, **summary(folder)}


def evaluate_list(name: str, labels: list[dict] | None = None) -> dict:
    """Accuracy against labels (rows with Domain + Expected columns), or the list's own Expected columns."""
    from ..pipeline import evaluate
    from ..core.runlog import log
    folder = _folder(name)
    path = None
    if labels:
        path = folder / "output" / "labels_from_api.csv"
        cols = list(dict.fromkeys(k for r in labels for k in r))
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(labels)
    result = evaluate.run(folder, path)
    log(folder, "Evaluate (API)", **result)
    with open(folder / "output" / "evaluation.csv", newline="") as f:
        result["disagreements"] = list(csv.DictReader(f))
    return result


def runs(name: str) -> dict:
    from ..core.runlog import read
    rows = read(_folder(name))
    return {"list": name, "runs": rows, "total_ai_cost_usd": round(sum(r.get("cost_usd", 0) for r in rows), 4)}


def lookup(domain: str, company: str = "", legal_name: str | None = None, place: str = "") -> dict:
    """The free directories (YC, SEC, IRS) for one company."""
    from ..sources import directories
    d = normalize_domain(domain)
    if not d:
        raise ValueError(f"'{domain}' is not a valid domain")
    found = directories.lookup(d, company, legal_name, [place] if place else [])
    return {"domain": d, **{k: found.get(k) for k in ("YC", "SEC", "IRS")}, "errors": found.get("errors")}


def signals_reference() -> list[dict]:
    """Every data point: its column name in the results, group and points."""
    from ..checks.rules import GROUP_NAMES, SIGNALS, signal_column
    from ..checks.scoring import config
    cfg = config()
    weights = {**cfg["fit_signals"], **cfg["buying_signals"]}
    out = []
    for sid, (group, label) in SIGNALS.items():
        w = weights.get(label)
        points = (cfg["weak_signal_points"] if group == "weak" else
                  {k: cfg["points"][v] for k, v in w.items()} if isinstance(w, dict) else cfg["points"].get(w) if w else None)
        out.append({"name": label, "group": GROUP_NAMES[group], "column": signal_column(sid), "points": points})
    return out
