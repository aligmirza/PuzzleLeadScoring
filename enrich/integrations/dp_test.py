"""Single data point test mode: run ONE data point for ONE company through its own waterfall and show every stage.

Isolated on purpose:
    - works only on lists whose name starts with "dp_test" (a copy made by setup(); production lists are never touched)
    - answers you (or the attached MCP model) give are saved to that test list only, never to the shared answer cache
    - nothing here rewrites a list's final CSV, pushes to Clay, or calls OpenAI or a provider

It reuses the real waterfall: engine/resolve.py (layers 1 and 2 live, saved answers for 3 and 4), the per data point
view in pipeline/waterfall.py, and the same quality checks every AI answer goes through (ai/apply.py).

    setup(source_list, test_list)                 copy a finished list into an isolated test list, without AI answers
    run(domain, data_point, test_list)            run ONE data point's waterfall, return the stage-by-stage report and
                                                  pause for review (nothing else runs until it is reviewed)
    answer(domain, data_point, answer, test_list) save the attached model's answer for the stage the waterfall asks for
    review(action, ...)                           approve / reject / correct / investigate / retry / stop; logged to
                                                  lists/<test_list>/output/dp_test_log.csv
    run(..., confirm_next=True)                   the next data point, only after the user said yes
    summary(test_list)                            progress across the 57 data points and totals from the log

Session progress (current data point, state, retries, reviews) is kept in lists/<test_list>/output/dp_test_session.json.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from ..ai import apply as ai_apply
from ..ai import prompts as P
from ..checks import datapoints
from ..checks.rules import SIGNALS, VALUE_NAMES, phase_of, signal_column
from ..core import versions
from ..core.inputs import normalize_domain
from ..core.paths import ROOT
from ..core.store import Store
from ..engine import layers as L
from ..pipeline import waterfall
from ..pipeline.common import LISTS, run_rules
from .client_ai import _row, with_sources

PREFIX = "dp_test"
EST = {"rules": 0.0, "directories": 0.0, "ai_pages": 0.0008, "ai_web": 0.011, "provider": None}
USAGE = {"model": "attached MCP model", "cost": 0, "web_searches": 0, "via": "mcp dp_test"}
LOG_FIELDS = ["Tested at", "Domain", "Company", "Data point ID", "Data point", "Expected output", "Final value",
              "Decided by", "Evidence", "Source", "Stages executed", "Fallback path", "Actual API cost (USD)",
              "Estimated cost (USD)", "Result type", "Manual: verdict", "Manual: correct answer", "Manual: notes",
              "Manual: reference", "Test status"]


# helpers -----------------------------------------------------------------------------------------------------------
def _guard(test_list: str) -> Path:
    if not test_list.startswith(PREFIX):
        raise ValueError(f"Test mode only works on lists named '{PREFIX}...'; '{test_list}' could be production data.")
    return LISTS / test_list


def _sid(data_point: str) -> str:
    key = data_point.strip()
    if key.upper() in SIGNALS:
        return key.upper()
    low = key.lower()
    for sid, (_, name) in SIGNALS.items():
        if low in (name.lower(), signal_column(sid).lower()):
            return sid
    raise ValueError(f"Unknown data point '{data_point}'. Use an ID (M1..M4, E1..E6, F1..F21, B1..B14, W1..W12) or its name.")


def _description(sid: str) -> str:
    """The data point's definition from docs/SIGNALS.md (the "Yes when / No when / Exclude when" part)."""
    path = ROOT / "docs" / "SIGNALS.md"
    if not path.exists():
        return ""
    text = path.read_text()
    m = re.search(rf"^#{{3,4}} {re.escape(sid)}\. (.+?)$(.*?)(?=^#{{2,4}} |\Z)", text, re.M | re.S)
    if not m:
        return ""
    body, keep = [], False
    for ln in m.group(2).splitlines():
        if re.match(r"- \*\*", ln):  # a top-level bullet: keep the definition ones
            keep = bool(re.match(r"- \*\*(Yes when|No when|Exclude when|Counts when|Fit when|Weak when)", ln))
            if keep:
                body.append(ln.strip("- ").strip())
        elif keep and re.match(r"\s+- ", ln):  # its sub-list ("any one of these: ...")
            body[-1] += (" " if body[-1].endswith(":") else "; ") + ln.strip().lstrip("- ").strip()
    return (m.group(1).strip() + ". " + " ".join(body)).replace("**", "").strip()


def _expected(pid: str | None) -> dict:
    out = {"final_value": ["Yes", "No", "Needs check", "Unknown (not found; never a No)"]}
    if pid:
        schema = P.load()[1][pid]["output"]["schema"]
        out["ai_answer_fields"] = {k: v.get("enum") or v.get("type", "") for k, v in schema.get("properties", {}).items()
                                   if not k.startswith("_")}
    return out


def _directory_summary(store: Store, domain: str) -> dict:
    found = store.get_directory(domain) or {}
    out = {}
    for src in ("YC", "SEC", "IRS", "Wayback"):
        v = found.get(src)
        if not v:
            out[src] = "no match"
            continue
        if src == "YC":
            out[src] = f"{v.get('name')} ({v.get('batch')}, stage {v.get('stage')}, status {v.get('status')}); match {v.get('match')} by {v.get('basis')}"
        elif src == "SEC":
            out[src] = (f"{v.get('name')} (CIK {v.get('cik')}, tickers {v.get('tickers') or 'none'}, "
                        f"{v.get('business_city')}, {v.get('business_state')}); match {v.get('match')} by {v.get('basis')}")
        elif src == "IRS":
            out[src] = f"{v.get('name')}; match {v.get('match')}"
        else:
            out[src] = {k: v.get(k) for k in ("home_date", "pricing_date", "pricing_prices")}
    if found.get("errors"):
        out["errors"] = found["errors"]
    return out


def _pending_for(store: Store, domain: str, phase: str, result: dict, pid: str | None) -> str | None:
    """'pages' or 'web' if the waterfall wants this data point's AI question answered next, else None."""
    if not pid:
        return None
    from .client_ai import _pending
    # test mode reviews every data point: asked even when a normal run would stop early (the tier can't change)
    return next((kind for p, kind in _pending(store, domain, phase, result, all_questions=True) if p == pid), None)


def _reason(layer: str, status: str, cfg: dict, pending: str | None, note: str) -> str:
    if status == "DECIDED":
        return "Stopped here: this layer confirmed the answer, so later layers are not run."
    if status == "not needed":
        return "Not run: an earlier layer already decided."
    if status == "doesn't cover this":
        return "This layer cannot answer this data point (no code support)."
    if status == "site not read":
        return "The website was not read, so rules had nothing to check."
    if status.startswith("unavailable"):
        return "Directory lookups are switched off for this run (--no-directories), so this layer could not run."
    if status.startswith("off in its file"):
        return f"Switched off in {cfg['file']}."
    if status == "skipped (web first)":
        return "Settings say web search first, so the page step is skipped."
    if status == "clue only, passed on":
        return "Found a hint, not proof (Needs check), so the next layer is asked."
    if status.startswith("nothing found"):
        return "Looked and found no evidence, so the next layer is asked (if its settings allow)."
    if status == "not counted, passed on":
        return f"Answered, but the quality checks rejected it: {note or 'see the answer'}."
    if layer == "ai_pages":
        need = cfg["steps"]["ai_pages"].get("requires_page")
        if need and need not in cfg.get("_pages", []):
            nxt = ("so the web search is next" if cfg["steps"]["ai_web"].get("run", "off") != "off"
                   else "and web search is off for this data point")
            return f"Skipped: this step needs a {need} page and none was found, {nxt}."
        if pending == "pages":
            return "Waiting: the data point is still open and its settings say ask the page question next."
        run = cfg["steps"]["ai_pages"].get("run")
        return f"Not triggered: ai_pages.run is '{run}' and the data point's state doesn't meet it."
    if layer == "ai_web":
        if pending == "web":
            return "Waiting: the page step left it open, and ai_web settings say search the web next."
        run = cfg["steps"]["ai_web"].get("run")
        return (f"Not triggered: ai_web.run is '{run}'. "
                + {"after_pages": "It only runs after a page answer that is empty or low confidence.",
                   "if_open": "It runs only if the data point is still open after the page answer.",
                   "if_clue": "Rarely public: it runs only on a clue (the rules flagged it, or the page answer saw "
                              "an unproven yes/no). A clean 'unknown' from the pages ends here.",
                   "off": "Web search is switched off for this data point" + _web_off_why(cfg) + "."}.get(run, ""))
    if layer == "provider":
        return "Not run: no Prospeo data bought for this company in the test list."
    return status


def _web_off_why(cfg: dict) -> str:
    pid = cfg["steps"]["ai_pages"].get("prompt")
    why = ((P.load()[1].get(pid) or {}).get("web_search") or {}).get("why", "") if pid else ""
    return f": {why.rstrip('.')}" if why else ""


def _normal_run_note(store: Store, domain: str, phase: str, result: dict, pid: str | None, pending: str | None) -> str:
    """What a normal (non-test) run would do with this question: the same, or skip it because the tier can't change."""
    if not pid or not pending:
        return ""
    from .client_ai import tier_lock
    lock = tier_lock(store, domain, phase, result)
    return (f"A normal run would skip this question (stop when the result can't change): {lock['why']}. "
            "It is asked here only because this is the manual review.") if lock else ""


# setup ---------------------------------------------------------------------------------------------------------------
def setup(source_list: str = "mcp_test", test_list: str = PREFIX, reset: bool = False) -> dict:
    """Copy a list (pages, crawl records, directory findings) into an isolated test list, without AI answers."""
    dst = _guard(test_list)
    src = LISTS / source_list
    if not (src / "cache.sqlite").exists():
        raise ValueError(f"No list called '{source_list}'.")
    if dst.exists():
        if not reset:
            store = Store(dst)
            return {"test_list": test_list, "created": False, "note": "Already exists; pass reset=true to copy it fresh.",
                    "companies": _companies(dst, store)}
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    if (src / "raw").exists():
        shutil.copytree(src / "raw", dst / "raw")
    for name in ("input.csv",):
        if (src / name).exists():
            shutil.copy2(src / name, dst / name)
    with sqlite3.connect(f"file:{src / 'cache.sqlite'}?mode=ro", uri=True) as s, sqlite3.connect(dst / "cache.sqlite") as d:
        s.backup(d)  # consistent copy even while the source is open; the source is opened read-only
    store = Store(dst)
    store.db.execute("DELETE FROM ai_answers")
    store.db.execute("DELETE FROM providers")
    store.db.commit()
    for domain in store.all_domains():  # results again from the free layers only
        record = store.get_site(domain)
        if not record:
            continue
        row = _row(dst, domain, store)
        for phase in ("icp", "signals"):
            if store.get_result(domain, phase):
                result = run_rules(store, row, record, phase)
                if phase == "signals":
                    result["icp_verdict"] = (store.get_result(domain, "icp") or {}).get("icp_verdict")
                    store.save_result(domain, result, "signals")
    return {"test_list": test_list, "created": True, "copied_from": source_list,
            "note": "AI and provider answers removed; results recomputed from rules and directories only.",
            "companies": _companies(dst, store)}


def _companies(folder: Path, store: Store) -> list[dict]:
    out = []
    for d in store.all_domains():
        icp = store.get_result(d, "icp") or {}
        out.append({"domain": d, "company": _row(folder, d, store)["company"], "icp_verdict": icp.get("icp_verdict"),
                    "phase_2_available": bool(store.get_result(d, "signals"))})
    return out


# review session: one data point at a time, paused for the user's review and an explicit "next" -------------------------
# idle -> (run) -> awaiting_review -> (approve / reject / correct / ...) -> awaiting_confirmation -> (run, confirm_next) ...
# investigate and retry keep the session on the current data point (awaiting_review); stop ends it.
FINAL = {"approve": "Correct", "reject": "Incorrect", "correct": "Corrected", "partially_correct": "Partially correct",
         "insufficient_evidence": "Insufficient evidence", "conflicting_evidence": "Conflicting evidence"}
STAY = ("investigate", "retry")
ALIASES = {"approved": "approve", "incorrect": "reject", "rejected": "reject", "needs_investigation": "investigate",
           "corrected": "correct", "end": "stop"}
ACTIONS = (*FINAL, *STAY, "stop")
READY = "Ready to move to the next data point?"


def _session_path(folder: Path) -> Path:
    return folder / "output" / "dp_test_session.json"


def _session(folder: Path) -> dict:
    path = _session_path(folder)
    if path.exists():
        return json.loads(path.read_text())
    return {"started": datetime.now().astimezone().isoformat(timespec="seconds"), "state": "idle", "current": None,
            "runs": {}, "retries": {}, "reviews": {}}


def _save_session(folder: Path, s: dict) -> None:
    path = _session_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(s, indent=1))


def _key(domain: str, sid: str) -> str:
    return f"{domain}|{sid}"


def progress(folder: Path, s: dict | None = None) -> dict:
    s = s or _session(folder)
    latest = s["reviews"]
    finals = [r["action"] for r in latest.values()]
    reviewed_sids = {k.split("|")[1] for k, r in latest.items() if r["action"] in FINAL}
    cur = s.get("current")
    return {
        "state": s["state"], "total_data_points": len(SIGNALS),
        "data_points_tested": len({k.split("|")[1] for k in s["runs"]}), "company_tests": len(s["runs"]),
        "data_points_reviewed": len(reviewed_sids),
        "approved": finals.count("approve"), "rejected": finals.count("reject"), "corrected": finals.count("correct"),
        "other_verdicts": sum(1 for a in finals if a in ("partially_correct", "insufficient_evidence", "conflicting_evidence")),
        "needs_investigation": sorted(k for k, r in latest.items() if r["action"] == "investigate"),
        "corrections": {k: {"expected": r.get("expected_value", ""), "notes": r.get("notes", "")}
                        for k, r in latest.items() if r["action"] == "correct" or r.get("notes")},
        "retries": sum(s["retries"].values()),
        "current": cur and {**cur, "retries": s["retries"].get(_key(cur["domain"], cur["data_point_id"]), 0)},
        "remaining": [sid for sid in SIGNALS if sid not in reviewed_sids],
    }


def _paused(folder: Path, s: dict, why: str) -> dict:
    return {"executed": False, "paused": True, "why": why, "progress": progress(folder, s)}


# run -----------------------------------------------------------------------------------------------------------------
def run(domain: str, data_point: str, test_list: str = PREFIX, confirm_next: bool = False) -> dict:
    """Run one data point for one company, then pause for the user's review. Nothing runs while a result is awaiting
    review, and after a review the next data point runs only with confirm_next=True (the user said yes)."""
    folder = _guard(test_list)
    d = normalize_domain(domain) or domain
    sid = _sid(data_point)
    s = _session(folder)
    cur = s.get("current") or {}
    same = (cur.get("domain"), cur.get("data_point_id")) == (d, sid)
    if s["state"] == "awaiting_review":
        if not same:
            return _paused(folder, s, f"{cur['data_point_id']} for {cur['domain']} is waiting for the user's review "
                                      f"({', '.join(ACTIONS)}). Review it before running another data point.")
        return review(d, sid, "retry", test_list=test_list)  # the same data point again = a retry
    if s["state"] in ("awaiting_confirmation", "stopped") and not confirm_next:
        return _paused(folder, s, READY + " Ask the user; call again with confirm_next=true only after they say yes.")
    report = _report(d, sid, test_list)
    info = report["test_information"]
    s.update(state="awaiting_review", current={"domain": d, "company": info["company"], "data_point_id": sid,
                                                "data_point": info["data_point"]})
    s["runs"][_key(d, sid)] = s["runs"].get(_key(d, sid), 0) + 1
    _save_session(folder, s)
    report["review"] = {"waiting_for": "the user's review", "actions": list(ACTIONS),
                        "how": "Show the user the whole result above, then call dp_test_record with their choice. "
                               "Do not run another data point until they have reviewed this one."}
    report["progress"] = progress(folder, s)
    return report


def _report(domain: str, data_point: str, test_list: str = PREFIX, include_page_text: bool = True) -> dict:
    folder = _guard(test_list)
    if not (folder / "cache.sqlite").exists():
        raise ValueError(f"No test list '{test_list}'. Call dp_test_setup first.")
    d = normalize_domain(domain) or domain
    sid = _sid(data_point)
    group, name = SIGNALS[sid]
    phase = phase_of(sid)
    store = Store(folder)
    record = store.get_site(d)
    if not record:
        raise ValueError(f"{d} is not in '{test_list}'.")
    if not store.get_result(d, phase):
        raise ValueError(f"{d} has no phase 2 result: phase 2 data points only run for companies that pass phase 1.")
    row = _row(folder, d, store)
    started = time.time()
    # layers 1 and 2 run live on the saved pages (one rules pass computes the whole phase; only this data point is shown)
    result = run_rules(store, row, record, phase)
    if phase == "signals":
        result["icp_verdict"] = (store.get_result(d, "icp") or {}).get("icp_verdict")
        store.save_result(d, result, "signals")
    ctx = waterfall.context(store, d, row["company"])
    dp = waterfall.data_point(ctx, sid)
    cfg = datapoints.load()[sid]
    cfg = {**cfg, "file": dp["file"], "_pages": sorted((record or {}).get("pages", {}))}
    pid = cfg["steps"]["ai_pages"].get("prompt")
    answers = store.get_ai(d, phase).get(pid, {}) if pid else {}
    pending = _pending_for(store, d, phase, result, pid)
    code_v, dir_v = (ctx["extract"].get(phase, ({}, {}))[0].get(sid) or {}), (ctx["extract"].get(phase, ({}, {}))[1].get(sid) or {})

    stages, executed, actual, estimate, outputs = [], 0, 0.0, 0.0, []
    for i, layer in enumerate(L.ORDER):
        label, found, proof, status = dp["rows"][i]
        if status.startswith("not run"):
            mine = {L.AI_PAGES: "pages", L.AI_WEB: "web"}.get(layer)
            status = "waiting for an answer" if mine and mine == pending else "not run"
        st = {"stage": layer.name, "status": status}
        if layer is L.RULES:
            ran = ctx["live"]
            st.update(executed=ran, source=code_v.get("url") or "saved website pages",
                      raw_evidence=code_v.get("evidence") or code_v.get("note") or "", output=VALUE_NAMES.get(code_v.get("value"), ""),
                      confidence="unavailable (rules give no confidence score)")
        elif layer is L.DIRECTORIES:
            from ..sources import directories
            ran = (sid in L.DIRECTORY_COVERS and cfg["steps"]["directories"].get("on", True) and ctx["live"]
                   and directories.ENABLED)
            if not directories.ENABLED and sid in L.DIRECTORY_COVERS:
                status = "unavailable (directories switched off)"
                st["status"] = status
            is_dir = dir_v.get("method") == "directory"
            st.update(executed=ran, source=_directory_summary(store, d) if ran else "not consulted",
                      raw_evidence=(dir_v.get("evidence") or "") if is_dir else "",
                      output=VALUE_NAMES.get(dir_v.get("value"), "") if is_dir else "",
                      confidence=((dir_v.get("note") or "match strength in note") if is_dir else
                                  "unavailable (directories give match strength, not confidence)"))
        elif layer is L.PROVIDER:
            bought = (store.get_provider(d) or {}).get("prospeo") or {}
            prov_v = (store.get_result(d, phase) or {}).get("signals", {}).get(sid, {})
            ran = bool(bought) and sid in L.PROVIDER_COVERS
            is_prov = prov_v.get("method") == "provider"
            st.update(executed=ran, source=("Prospeo " + ", ".join(sorted(bought))) if ran else "not run",
                      raw_evidence=prov_v.get("evidence", "") if is_prov else "",
                      output=VALUE_NAMES.get(prov_v.get("value"), "") if is_prov else "",
                      confidence=(prov_v.get("note") or "") if is_prov else "unavailable (stage not run)",
                      credits_used=("1 credit per search that found people / per matched company record (cached 90 days)"
                                    if ran else "none"))
        else:
            step = {L.AI_PAGES: 1, L.AI_WEB: 2}.get(layer)
            a = (answers.get(step) or {}) if step else {}
            ans, use = a.get("answer") or {}, a.get("usage") or {}
            ran = bool(a)
            st.update(executed=ran, source=ans.get("source") or ("not run" if not ran else ""),
                      raw_evidence=ans.get("evidence_quote", ""),
                      output=str(ans.get("answer", ans.get("venture_backed", ans.get("value", "")))) if ran else "",
                      confidence=ans.get("confidence", "unavailable") if ran else "unavailable (stage not run)",
                      reason=ans.get("reason", "") if ran else "", answered_by=use.get("model", "") if ran else "")
            if ran and ans.get("entity_match"):
                st["entity_match"] = ans["entity_match"]
        if ran:
            executed += 1
            if st.get("output") in ("Yes", "No", "yes", "no"):
                outputs.append(st["output"].lower())
        est = EST[layer.key]
        usage_cost = None
        if layer in (L.AI_PAGES, L.AI_WEB) and ran:
            usage_cost = float(((answers.get({L.AI_PAGES: 1, L.AI_WEB: 2}[layer]) or {}).get("usage") or {}).get("cost") or 0)
            actual += usage_cost
            estimate += est
        elif ran and est is not None:
            estimate += est
        st["cost"] = {"actual_usd": 0.0 if layer in (L.RULES, L.DIRECTORIES) and ran else usage_cost,
                      "estimated_usd_if_run_by_openai": est,
                      "note": ("free layer" if layer in (L.RULES, L.DIRECTORIES) else
                               "answered by the attached MCP model: $0 API" if ran else "not run")}
        if layer is L.PROVIDER and sid in L.PROVIDER_COVERS and cfg["steps"]["provider"].get("run", "off") == "off":
            status = st["status"] = "off for this data point"
            st["executed"] = False
            st["fallback_reason"] = f"Prospeo is switched off for this data point in {cfg['file']} (used only where switched on)."
        elif layer is L.AI_WEB and cfg["steps"]["ai_web"].get("run", "off") == "off" and not ran:
            status = st["status"] = "off for this data point"
            st["fallback_reason"] = "Web search is switched off for this data point" + _web_off_why(cfg) + "."
        else:
            st["fallback_reason"] = _reason(layer.key, status, cfg, pending, (dp["final"] or {}).get("note", ""))
        stages.append(st)

    final = dp["final"]
    value = final["value"]
    if len(set(outputs)) > 1:
        kind = "conflicting (stages disagree)"
    elif value in ("yes", "no"):
        kind = "directly verified" if final.get("evidence") else "decided without a quote (check it)"
    elif value == "check":
        kind = "inferred: a clue only (Needs check), not proof"
    else:
        kind = "unknown: insufficient evidence (this is NOT a negative)"

    next_step = None
    if pending:
        from .client_ai import HOW_TO_ANSWER, _fields, prompt_for
        from ..ai.runner import known_facts
        page_answer = (answers.get(1) or {}).get("answer")
        msg = prompt_for(pid, pending, row["company"] or d, d, known_facts(result), result, page_answer)
        next_step = {"stage": "AI reading our pages" if pending == "pages" else "AI web search",
                     "answer_from": pending, "question_id": pid, "how_to_answer": HOW_TO_ANSWER,
                     "prompt_file": msg["prompt_file"], "prompt_version": versions.prompt(pid),
                     "system_prompt": msg["system"], "instructions": msg["instruction"],
                     "answer_fields": _fields(msg["schema"]),
                     "estimated_cost_if_openai_usd": EST["ai_pages" if pending == "pages" else "ai_web"]}
        if pending == "pages" and include_page_text:
            next_step["company_page_text"] = msg["context"]

    return {
        "test_information": {
            "company": row["company"], "domain": d, "data_point_id": sid, "data_point": name,
            "group": group, "phase": phase, "description": _description(sid),
            "expected_output": _expected(pid), "waterfall_settings": dp["settings"], "settings_file": dp["file"],
            "tested_at": datetime.now().astimezone().isoformat(timespec="seconds"), "test_list": test_list,
            "run_seconds": round(time.time() - started, 2),
        },
        "waterfall": stages,
        "final_result": {
            "value": VALUE_NAMES[value], "data_type": "Yes / No / Needs check / Unknown",
            "evidence": final.get("evidence", ""), "source": final.get("url", ""), "note": final.get("note", ""),
            "result_type": kind, "decided_by": dp["decided_by"] or "no layer (still open)",
            "stages_executed": executed, "actual_api_cost_usd": round(actual, 4),
            "estimated_cost_if_openai_usd": round(estimate, 4),
            "unresolved": ("Waiting for the " + next_step["stage"] + " stage") if next_step else
                          ("Still open after every layer allowed by its settings" if value in ("unknown", "check") else ""),
        },
        "next_step": next_step,
        "normal_run": _normal_run_note(store, d, phase, result, pid, pending),
        "scope_note": "Rules re-ran for this phase in one pass (free, no network); only this data point is shown. "
                      "Nothing was written outside the test list.",
    }


# answer --------------------------------------------------------------------------------------------------------------
def answer(domain: str, data_point: str, answer: dict, test_list: str = PREFIX) -> dict:
    """Save the attached model's answer for the stage the waterfall is waiting for. Test list only; no shared cache."""
    folder = _guard(test_list)
    d = normalize_domain(domain) or domain
    sid = _sid(data_point)
    s = _session(folder)
    cur = s.get("current") or {}
    if s["state"] != "awaiting_review" or (cur.get("domain"), cur.get("data_point_id")) != (d, sid):
        raise ValueError(f"{sid} for {d} is not the data point under test; run it with dp_test_run first.")
    phase = phase_of(sid)
    pid = datapoints.step(sid, "ai_pages").get("prompt")
    if not pid:
        raise ValueError(f"{sid} has no AI question; only rules and directories answer it.")
    store = Store(folder)
    result = store.get_result(d, phase)
    if not result:
        raise ValueError(f"No {phase} result for {d} in '{test_list}'.")
    pending = _pending_for(store, d, phase, result, pid)
    a = dict(answer)
    kind = a.pop("answer_from", None) or pending
    if not pending:
        raise ValueError("The waterfall is not waiting for an AI answer on this data point (decided, or settings don't allow it).")
    if kind != pending:
        raise ValueError(f"The waterfall is waiting for the '{pending}' stage, not '{kind}'.")
    a.pop("id", None)
    step = 2 if kind == "web" else 1
    with_sources(a, step)
    store.save_ai(d, phase, pid, step, a, {**USAGE, "prompt_version": versions.prompt(pid)})  # test list only
    report = _report(d, sid, test_list, include_page_text=False)
    v = (store.get_result(d, phase) or {}).get("signals", {}).get(sid, {})
    report["answer_saved"] = {"stage": "AI reading our pages" if step == 1 else "AI web search",
                              "counted": ai_apply.counted(v),
                              "quality_check_note": v.get("note", "")}
    report["progress"] = progress(folder)
    return report


# review log ----------------------------------------------------------------------------------------------------------
def review(domain: str = "", data_point: str = "", action: str = "", expected_value: str = "", notes: str = "",
           reference: str = "", test_list: str = PREFIX) -> dict:
    """The user's review of the data point under test (only ever the current one).

    approve / reject / correct (needs expected_value) / partially_correct / insufficient_evidence / conflicting_evidence:
        logged to lists/<test_list>/output/dp_test_log.csv (automated and manual columns kept apart); the session then
        waits for the user to confirm the next data point.
    investigate: more evidence and how the result was produced; stays on this data point.
    retry: clears this data point's test answers and re-runs only it; stays on this data point.
    stop: ends the session (progress is kept)."""
    folder = _guard(test_list)
    key = action.strip().lower().replace(" ", "_")
    key = ALIASES.get(key, key)
    if key not in ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(ACTIONS)}")
    s = _session(folder)
    cur = s.get("current") or {}
    if key == "stop":
        s["state"] = "stopped"
        _save_session(folder, s)
        return {"stopped": True, "progress": progress(folder, s),
                "note": "Session stopped. Run dp_test_run with confirm_next=true to start again; progress is kept."}
    if not cur:
        raise ValueError("Nothing under test. Run a data point with dp_test_run first.")
    d = normalize_domain(domain) or domain if domain else cur["domain"]
    sid = _sid(data_point) if data_point else cur["data_point_id"]
    if (d, sid) != (cur["domain"], cur["data_point_id"]):
        raise ValueError(f"Only the current data point can be reviewed: {cur['data_point_id']} for {cur['domain']}.")
    if s["state"] != "awaiting_review" and key in STAY:
        raise ValueError(f"{sid} for {d} was already reviewed. {READY}")
    if key == "correct" and not expected_value.strip():
        raise ValueError("A correction needs expected_value (the answer you expected), plus notes if useful.")
    k = _key(d, sid)

    if key == "retry":
        _clear_answers(folder, d, sid)
        s["retries"][k] = s["retries"].get(k, 0) + 1
        s["runs"][k] = s["runs"].get(k, 0) + 1
        s["state"] = "awaiting_review"
        _save_session(folder, s)
        report = _report(d, sid, test_list)
        report["review"] = {"retry": s["retries"][k], "waiting_for": "the user's review", "actions": list(ACTIONS),
                            "note": "Only this data point was re-run; its earlier test answers were cleared, so any AI "
                                    "stage asks again."}
        report["progress"] = progress(folder, s)
        return report

    rep = _report(d, sid, test_list, include_page_text=key == "investigate")
    out = {}
    if key == "investigate":
        out["investigation"] = _investigate(folder, d, sid, rep)
    row = _log(folder, rep, key, expected_value, notes, reference)
    if key in ("reject", "correct", "partially_correct", "conflicting_evidence"):
        from ..core import learning
        learning.record("correction", sid=sid, domain=d, action=key, got=rep["final_result"]["value"],
                        expected=expected_value, notes=notes, list=folder.name)
    s["reviews"][k] = {"action": key, "expected_value": expected_value, "notes": notes, "reference": reference,
                       "final_value": rep["final_result"]["value"], "at": row["Tested at"]}
    if key in FINAL:
        s["state"] = "awaiting_confirmation"
    _save_session(folder, s)
    out.update(recorded=True, action=key, log=str((folder / "output" / "dp_test_log.csv").relative_to(LISTS.parent)),
               row=row, progress=progress(folder, s))
    out["next"] = (READY + " Ask the user and wait; run the next data point only after they say yes (confirm_next=true)."
                   if key in FINAL else "Still on this data point: show the investigation, then ask the user to approve, "
                                       "reject, correct, retry or stop.")
    return out


def record(domain: str, data_point: str, verdict: str, correct_answer: str = "", notes: str = "", reference: str = "",
           test_list: str = PREFIX) -> dict:
    """Older name for review() (verdict = action, correct_answer = expected_value)."""
    return review(domain, data_point, verdict, correct_answer, notes, reference, test_list)


def _clear_answers(folder: Path, domain: str, sid: str) -> None:
    """Remove this data point's AI answers from the TEST list (never a production list or the shared cache)."""
    pid = datapoints.step(sid, "ai_pages").get("prompt")
    if not pid:
        return
    phase = phase_of(sid)
    store = Store(folder)
    store.db.execute("DELETE FROM ai_answers WHERE domain = ? AND phase = ? AND prompt_id = ?", (domain, phase, pid))
    store.db.commit()


def _investigate(folder: Path, domain: str, sid: str, rep: dict) -> dict:
    """Everything behind the result: settings, the rules' full finding, directory records, saved answers, page text."""
    store = Store(folder)
    phase = rep["test_information"]["phase"]
    pid = datapoints.step(sid, "ai_pages").get("prompt")
    result = store.get_result(domain, phase) or {}
    page_text = (rep.get("next_step") or {}).get("company_page_text")
    if page_text is None and pid:
        from ..ai.runner import known_facts
        row = _row(folder, domain, store)
        page_text = P.context_text(row["company"] or domain, domain, known_facts(result), result.get("ai_context", {}))
    return {
        "how_it_was_produced": [f"{st['stage']}: {st['status']} - {st['fallback_reason']}" for st in rep["waterfall"]],
        "data_point_settings": datapoints.load()[sid],
        "final_answer_record": result.get("signals", {}).get(sid, {}),
        "pages_read": (store.get_site(domain) or {}).get("pages", {}),
        "directory_records": store.get_directory(domain) or {},
        "saved_test_answers": {str(k): v for k, v in (store.get_ai(domain, phase).get(pid, {}) if pid else {}).items()},
        "ai_question": P.instruction_text(pid) if pid else "none (rules and directories only)",
        "company_page_text": (page_text or "")[:8000],
    }


def _log(folder: Path, rep: dict, key: str, expected: str, notes: str, reference: str) -> dict:
    info, fin = rep["test_information"], rep["final_result"]
    path = [f"{s['stage']}: {s['status']}" for s in rep["waterfall"]]
    row = {"Tested at": info["tested_at"], "Domain": info["domain"], "Company": info["company"],
           "Data point ID": info["data_point_id"], "Data point": info["data_point"],
           "Expected output": "Yes / No / Needs check / Unknown", "Final value": fin["value"],
           "Decided by": fin["decided_by"], "Evidence": fin["evidence"][:500], "Source": fin["source"],
           "Stages executed": fin["stages_executed"], "Fallback path": " | ".join(path),
           "Actual API cost (USD)": fin["actual_api_cost_usd"], "Estimated cost (USD)": fin["estimated_cost_if_openai_usd"],
           "Result type": fin["result_type"], "Manual: verdict": FINAL.get(key, "Needs investigation"),
           "Manual: correct answer": expected, "Manual: notes": notes, "Manual: reference": reference,
           "Test status": "closed" if key in FINAL else "open"}
    out = folder / "output" / "dp_test_log.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    with open(out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    return row


def summary(test_list: str = PREFIX) -> dict:
    folder = _guard(test_list)
    path = folder / "output" / "dp_test_log.csv"
    if not path.exists():
        return {"tested": 0, "of": len(SIGNALS), "note": "No reviews recorded yet.", "progress": progress(folder)}
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    latest = {}
    for r in rows:  # the latest review per company + data point counts
        latest[(r["Domain"], r["Data point ID"])] = r
    rows = list(latest.values())
    by_verdict, per_dp = {}, {}
    for r in rows:
        by_verdict[r["Manual: verdict"]] = by_verdict.get(r["Manual: verdict"], 0) + 1
        p = per_dp.setdefault(r["Data point ID"], {"name": r["Data point"], "tests": 0, "verdicts": {}, "web_search": 0,
                                                   "weak_or_missing_evidence": 0, "conflicting": 0, "est_cost": 0.0})
        p["tests"] += 1
        p["verdicts"][r["Manual: verdict"]] = p["verdicts"].get(r["Manual: verdict"], 0) + 1
        p["web_search"] += "AI web search: DECIDED" in r["Fallback path"] or "AI web search: not counted" in r["Fallback path"]
        p["weak_or_missing_evidence"] += r["Final value"] in ("Unknown", "Needs check") or not r["Evidence"]
        p["conflicting"] += r["Result type"].startswith("conflicting")
        p["est_cost"] += float(r["Estimated cost (USD)"] or 0)
    tested = len(per_dp)
    avg = (sum(p["est_cost"] for p in per_dp.values()) / max(1, sum(p["tests"] for p in per_dp.values())))
    return {"data_points_tested": tested, "of": len(SIGNALS), "reviews": len(rows), "by_verdict": by_verdict,
            "per_data_point": per_dp,
            "estimated_cost_per_test_usd": round(avg, 4),
            "estimated_cost_all_57_one_company_usd": round(avg * len(SIGNALS), 4),
            "progress": progress(folder),
            "note": "Manual verdicts are yours; automated columns come from the run. Costs are OpenAI estimates; "
                    "answers given through MCP cost $0 in API."}
