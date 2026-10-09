"""Turn saved AI answers into signal values, then re-decide the ICP verdict.

Used after the AI step and every time the rules are re-run, so AI answers are never lost.

Confidence rules (from config/prompts/_shared.json), plus: a Yes or No needs an exact quote as proof, "the text doesn't
mention it" is never a No, and an estimated funding stage is shown but earns no points.

Confidence rules (from config/prompts/_shared.json):
  - an exclusion "yes" or a must-have "no" needs HIGH confidence (and, from the web, a domain match)
  - any other yes / no needs MEDIUM or HIGH confidence (and, from the web, a domain or name-and-location match)
  - anything weaker is not proof: a clue the AI couldn't confirm becomes Unknown, which never counts against a company
  - AI only fills in what code left open (Needs check or Unknown); proof found by code is never overwritten
"""
import re

from . import prompts as P
from .rules import CHECK, SIGNALS, UNKNOWN, decide


NOT_MENTIONED = re.compile(r"\b(?:does|do|did) not (?:mention|indicate|state|show|say|provide|confirm)|\bno (?:mention|evidence|indication|information)\b"
                           r"|\bnot (?:mentioned|stated|indicated)\b|\bthere is no\b", re.I)


def _final_entry(steps: dict[int, dict]) -> tuple[int, dict] | None:
    """Use step 2 (web search) when it ran, else step 1 (our data)."""
    for step in (2, 1):
        if step in steps and isinstance(steps[step].get("answer"), dict) and "error" not in steps[step]["answer"]:
            return step, steps[step]["answer"]
    return None


def _apply_field(result: dict, pid: str, data: dict) -> None:
    conf = data.get("confidence", "low")
    if conf == "low":
        return
    if pid == "fields/vertical" and data.get("value"):
        result["vertical"] = data["value"]
    elif pid == "fields/segment_headcount" and data.get("value"):
        result["segment_ai"] = {"value": data["value"], "headcount": data.get("headcount_estimate"),
                                "evidence": data.get("evidence_quote", "")}
    elif pid == "fields/legal_entity_type" and data.get("value") not in (None, "Unknown"):
        if result.get("entity_type", "Unknown") == "Unknown":
            result["entity_type"] = data["value"]
    elif pid == "fields/competitors_used":
        result["competitors"] = sorted(set(result.get("competitors", [])) | set(data.get("competitors_used", [])))


def apply(result: dict, answers: dict[str, dict[int, dict]], phase: str) -> dict:
    """answers = Store.get_ai(domain, phase). Changes and returns result."""
    if "signals" not in result:
        return result
    prompts = P.load()[1]
    applied = 0
    for pid, steps in answers.items():
        chosen = _final_entry(steps)
        if not chosen or pid not in prompts:
            continue
        step, data = chosen
        applied += 1
        if pid.startswith("fields/"):
            _apply_field(result, pid, data)
            continue
        sid = prompts[pid].get("signal_id")
        if not sid or sid not in result["signals"]:
            continue
        current = result["signals"][sid]
        if pid == "fit/venture_backed_stage" and data.get("value") not in (None, "Unknown") and data.get("confidence") != "low":
            result["funding_stage"] = f"{data['value']} ({data.get('basis', 'estimated')})"
        if current["value"] not in (CHECK, UNKNOWN):
            continue  # code or a directory already found proof; AI doesn't overrule it

        if pid == "fit/venture_backed_stage":
            ans = data.get("venture_backed", "unknown")
        else:
            ans = data.get("answer", "unknown")
        conf = data.get("confidence", "low")
        entity = data.get("entity_match", "domain") if step == 2 else "domain"
        group = SIGNALS[sid][0]
        strict = (group == "exclusion" and ans == "yes") or (group == "must" and ans == "no")
        conf_ok = conf == "high" if strict else conf in ("high", "medium")
        entity_ok = entity == "domain" if strict else entity in ("domain", "name_and_location")

        sources = data.get("sources") or data.get("_sources_found") or []
        url = data.get("source", "")
        if step == 2 and sources:
            url = sources[0].get("url", url)
        method = "AI, company website" if step == 1 else "AI, web search"
        if ans == "no" and NOT_MENTIONED.search(data.get("reason", "")):
            conf_ok = False  # "it doesn't mention X" is absence of evidence, not a confirmed No
        if ans in ("yes", "no") and not (data.get("evidence_quote") or "").strip():
            conf_ok = False  # a confirmed answer needs an exact quote as proof
        if pid == "fit/venture_backed_stage" and data.get("basis") == "estimated":
            conf_ok = False  # an estimated stage is reported in "Funding stage" but earns no points
        if ans in ("yes", "no") and conf_ok and entity_ok:
            value, note = ans, f"{method} ({conf} confidence): {data.get('reason', '')}"
        else:
            # not enough proof: a clue the AI couldn't confirm no longer holds the company back
            value = UNKNOWN
            note = f"{method} said '{ans}' with {conf} confidence"
            if step == 2:
                note += f" and entity match '{entity}'"
            note += f", not enough to count: {data.get('reason', '')}"
        result["signals"][sid] = {"value": value, "evidence": (data.get("evidence_quote") or current["evidence"])[:300],
                                  "url": url, "note": note[:300], "method": "ai"}
        if value == "yes":
            for tool in data.get("tools_used", []) or []:
                result["partners"] = sorted(set(result.get("partners", [])) | {tool})

    if phase == "icp":
        d = decide(result["signals"], result.get("crawl", {}).get("status"))
        result.update(gate=d["gate"], icp_verdict=d["verdict"], exclusion_reason=d["exclusion_reason"],
                      icp_checks=[SIGNALS[s][1] for s in d["checks"]], must_haves_confirmed=len(d["confirmed"]))
    result.setdefault("ai", {})["answered"] = applied
    return result
