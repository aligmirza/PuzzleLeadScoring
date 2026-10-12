"""AI answered by the model attached to MCP (Claude Code, Claude Desktop): the only AI on the MCP path.

Through MCP the tool never calls the OpenAI API (or any other AI API). It hands the attached model the questions the
free layers left open (open_questions), with the same prompts the OpenAI step uses in the terminal (config/prompts);
the model answers them itself (reading the page text given, or searching the web where it can), and submit_answers
saves them. A question the model can't answer the way it asks (e.g. web search without a web tool) is sent back as
cannot_answer and simply stays open: nothing is sent elsewhere. Answers go through exactly the same quality checks
as any AI answer (ai/apply.py): an exact quote, enough confidence, a domain match for web answers, dated events for
recent signals. Cost: $0 in API.
"""
from __future__ import annotations

from ..ai import prompts as P
from ..ai.runner import known_facts
from ..checks import datapoints
from ..checks.rules import SIGNALS, VALUE_NAMES
from ..core import shared, versions
from ..core.inputs import load_leads, normalize_domain
from ..core.store import Store
from ..pipeline import final
from ..pipeline.common import LISTS, ai_prompts, run_rules
from . import service

USAGE = {"model": "attached MCP model", "cost": 0, "web_searches": 0, "via": "mcp"}
HOW_TO_ANSWER = (
    "You answer with the SAME prompts the OpenAI step uses, from the project's prompt folder: system_prompt (role, ICP "
    "summary, ground rules, confidence scale; config/prompts/_shared.json) applies to every question, and each "
    "question's instructions come from its own file (prompt_file). Follow them exactly. Use only company_page_text for "
    "answer_from = pages; search the web for answer_from = web. Return each answer as a JSON object with the fields "
    "in answer_fields plus \"id\" and \"answer_from\", then call submit_answers with all of them. "
    "If you can't answer a question the way it asks (for example answer_from = web and you have no web search tool), "
    "don't guess and don't answer from memory: send {\"id\", \"answer_from\", \"cannot_answer\": \"<why>\"} for it.")


def _folder_store(list_name: str):
    folder = LISTS / list_name
    if not ((folder / "input.csv").exists() or (folder / "cache.sqlite").exists()):
        raise ValueError(f"No list called '{list_name}'. Run check_company (list api_checks) or enrich_list first.")
    return folder, Store(folder)


def _row(folder, domain: str, store: Store | None = None) -> dict:
    """The company's row: from the list file, or for one-off checks (api_checks) from what check_company saved."""
    if (folder / "input.csv").exists():
        rows, _ = load_leads(folder / "input.csv")
        hit = next((r for r in rows if r["domain"] == domain), None)
        if hit:
            return hit
    given = ((store.get_site(domain) if store else None) or {}).get("input") or {}
    return {"domain": domain, "company": given.get("company", ""), "employees": given.get("employees", ""), "input": {}}


def _domains(folder, store: Store) -> list[tuple[str, str]]:
    if (folder / "input.csv").exists():
        rows, _ = load_leads(folder / "input.csv")
        return [(r["domain"], r["company"]) for r in rows]
    return [(d, _row(folder, d, store)["company"]) for d in store.all_domains()]


def _phase(store: Store, domain: str, phase: str) -> str:
    if phase in ("icp", "signals"):
        return phase
    icp = store.get_result(domain, "icp") or {}
    if icp.get("icp_verdict") in ("No", "Unknown: site not reached"):
        return "icp"
    open_icp = [p for p in ai_prompts(icp) if p not in store.get_ai(domain, "icp")]
    return "icp" if open_icp or not store.get_result(domain, "signals") else "signals"


def _pending(store: Store, domain: str, phase: str, result: dict) -> list[tuple[str, str]]:
    """(question, 'pages' or 'web') still to answer: a page answer first; the web where the page answer left it open."""
    done = store.get_ai(domain, phase)
    site_ok = (result.get("crawl") or {}).get("status") == "live"
    out = []
    for pid in ai_prompts(result):
        steps = done.get(pid, {})
        sid = datapoints.sid_for_prompt(pid)
        still_open = datapoints.still_open(sid, result)
        if not steps:
            if sid and not still_open:
                continue  # already settled after the questions were chosen (e.g. by provider data bought since)
            out.append((pid, "pages" if site_ok and datapoints.ask_pages(pid, result) else "web"))
        elif 2 not in steps and still_open and datapoints.web_after(pid, (steps.get(1) or {}).get("answer"), True, site_ok):
            out.append((pid, "web"))
    return [(p, k) for p, k in out if k == "pages" or datapoints.step(datapoints.sid_for_prompt(p) or "", "ai_web").get("run", "after_pages") != "off"]


def _fields(schema: dict) -> dict:
    out = {}
    for name, spec in (schema.get("properties") or {}).items():
        if name.startswith("_"):
            continue
        out[name] = spec.get("enum") or spec.get("description") or spec.get("type", "")
    return out


def prompt_for(pid: str, kind: str, company: str, domain: str, facts: dict, result: dict, page_answer: dict | None = None) -> dict:
    """The exact prompt the OpenAI step would send for this question (system, instruction, schema; page text for the
    pages step), built from config/prompts, so the attached model answers with the same prompt."""
    if kind == "pages":
        msg = P.build(pid, company, domain, facts, result.get("ai_context", {}))
    else:
        msg = P.build_search(pid, company, domain, facts, result.get("legal_name"), page_answer)
    return {**msg, "prompt_file": f"config/prompts/{pid}.json"}


def open_questions(domain: str, list_name: str = service.SINGLE_LIST, phase: str = "auto", limit: int = 12) -> dict:
    d = normalize_domain(domain) or domain
    folder, store = _folder_store(list_name)
    phase = _phase(store, d, phase)
    result = store.get_result(d, phase)
    if not result:
        raise ValueError(f"No {phase} result for {d} in '{list_name}'. Run check_company first.")
    pending = _pending(store, d, phase, result)
    row = _row(folder, d, store)
    facts = known_facts(result)
    questions = []
    done = store.get_ai(d, phase)
    system = context = ""
    for pid, kind in pending[:limit]:
        msg = prompt_for(pid, kind, row["company"] or d, d, facts, result, (done.get(pid, {}).get(1) or {}).get("answer"))
        system = system or msg["system"]
        context = context or msg.get("context", "")
        questions.append({"id": pid, "data_point": P.name(pid), "answer_from": kind, "prompt_file": msg["prompt_file"],
                          "prompt_version": versions.prompt(pid), "instructions": msg["instruction"],
                          "answer_fields": _fields(msg["schema"])})
    needs_pages = any(q["answer_from"] == "pages" for q in questions)
    return {
        "list": folder.name, "domain": d, "phase": phase, "icp_verdict": (store.get_result(d, "icp") or {}).get("icp_verdict"),
        "open_questions": len(pending), "shown": len(questions),
        "how_to_answer": HOW_TO_ANSWER,
        "system_prompt": system,
        "company_page_text": (context or P.context_text(row["company"] or d, d, facts, result.get("ai_context", {})))
                             if needs_pages else "",
        "questions": questions,
        "next": "Answer these, then call submit_answers(domain, answers=[{id, answer_from, ...answer_fields}], "
                f"list_name='{folder.name}', phase='{phase}')." if questions else "Nothing left to answer in this phase.",
    }


def submit_answers(domain: str, answers: list[dict], list_name: str = service.SINGLE_LIST, phase: str = "auto") -> dict:
    d = normalize_domain(domain) or domain
    folder, store = _folder_store(list_name)
    phase = _phase(store, d, phase)
    known = P.load()[1]
    saved, rejected, unsupported = [], [], []
    for a in answers:
        a = dict(a)
        pid = a.pop("id", None) or a.pop("question_id", None)
        if pid not in known:
            rejected.append({"id": pid, "why": "unknown question id"})
            continue
        step = 2 if a.pop("answer_from", "pages") == "web" else 1
        if a.get("cannot_answer"):
            unsupported.append({"id": pid, "answer_from": "web" if step == 2 else "pages", "why": str(a["cannot_answer"])[:200]})
            continue
        if step == 2 and a.get("source") and not a.get("_sources_found"):
            a["_sources_found"] = [{"url": a["source"], "title": ""}]
        usage = {**USAGE, "prompt_version": versions.prompt(pid)}
        store.save_ai(d, phase, pid, step, a, usage)
        shared.put_ai(d, phase, pid, step, a, usage)
        saved.append((pid, step))
    out = _finish(folder, store, d, phase, saved)
    out["rejected"] = rejected
    if unsupported:
        out["not_answered"] = {"questions": unsupported,
                               "note": "Left open: through MCP only you (the attached model) answer AI questions."}
    return out


def _finish(folder, store: Store, d: str, phase: str, saved: list[tuple[str, int]]) -> dict:
    """Re-run the waterfall with the new answers, rewrite the list's output, and say which answers counted."""
    record = store.get_site(d)
    row = _row(folder, d, store)
    result = run_rules(store, row, record, phase) if record else store.get_result(d, phase)
    if phase == "signals" and result:
        result["icp_verdict"] = (store.get_result(d, "icp") or {}).get("icp_verdict")
        store.save_result(d, result, "signals")
    if (folder / "input.csv").exists():
        final.write(folder, store)
        row_out = next((r for r in final.read_rows(folder) if r.get("_domain") == d), {})
    else:  # one-off checks have no list file: work out the row directly
        row_out = service.findings(store, d, row["company"], row["employees"])
    outcome = []
    for pid, step in saved:
        sid = datapoints.sid_for_prompt(pid)
        if not sid:
            outcome.append({"id": pid, "data_point": P.name(pid), "result": "saved"})
            continue
        v = result["signals"].get(sid, {})
        counted = v.get("method") == "ai" and v.get("value") in ("yes", "no")
        outcome.append({"id": pid, "data_point": SIGNALS[sid][1], "result": f"counted as {VALUE_NAMES[v['value']]}" if counted
                        else f"not counted ({(v.get('note') or 'already decided by an earlier layer')[:160]})"})
    left = _pending(store, d, phase, result) if result else []
    from ..providers import run as prun
    step5 = (not left and phase == "signals" and prun.prospeo.enabled() and not prun.has_prospeo(store, d)
             and prun.finance_open(store, d))
    nxt = (f"{len(left)} questions still open: call open_questions again." if left else
           "Phase 1 done. If ICP is YES and signals aren't checked yet, call check_company again with include_signals, "
           "then open_questions(phase='signals')." if phase == "icp" else "All questions answered.")
    if step5:
        nxt += (f" Step 5 is next: the Prospeo finance-person check ({', '.join(step5)}), 1 credit. Ask the user; only "
                f"if they agree, call check_company('{d}', use_prospeo=true).")
    return {"domain": d, "phase": phase, "saved": len(saved), "answers": outcome,
            "icp": row_out.get("ICP"), "icp_status": row_out.get("ICP status"), "lead_score": row_out.get("Lead score"),
            "lead_tier": row_out.get("Lead tier"), "next": nxt}


def overview(list_name: str) -> dict:
    """Per company: open questions in each phase, so the attached model can work through a whole list."""
    folder, store = _folder_store(list_name)
    out = []
    for domain, company in dict.fromkeys(_domains(folder, store)):
        icp = store.get_result(domain, "icp")
        if not icp:
            continue
        item = {"domain": domain, "company": company, "icp_verdict": icp.get("icp_verdict")}
        for ph in ("icp", "signals"):
            res = store.get_result(domain, ph)
            item[f"open_{ph}"] = len(_pending(store, domain, ph, res)) if res and (
                ph == "icp" or icp.get("icp_verdict") not in ("No", "Unknown: site not reached")) else 0
        out.append(item)
    return {"list": folder.name, "companies": out,
            "total_open": sum(c["open_icp"] + c["open_signals"] for c in out)}
