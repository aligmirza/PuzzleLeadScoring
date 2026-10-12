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
from ..checks.rules import CHECK, SIGNALS, UNKNOWN
from ..engine.merge import redecide


NOT_MENTIONED = re.compile(r"\b(?:does|do|did) not (?:mention|indicate|state|show|say|provide|confirm)|\bno (?:mention|evidence|indication|information)\b"
                           r"|\bnot (?:mentioned|stated|indicated)\b|\bthere is no\b", re.I)


RE_E1_PROOF = re.compile(r"\bseries\s+[c-z]\b|\bIPO\b|initial public offering|publicly traded|\b(?:NYSE|NASDAQ|Nasdaq)\b|"
                         r"stock exchange|\bticker\b|went public", re.I)


def late_stage_proof(quote: str, company: str = "") -> bool:
    """Series C or later needs a quote that shows it. A tender offer or secondary sale is not a funding round."""
    from ..checks.rules import _late_stage_amount
    if re.search(r"tender offer|secondary (?:sale|transaction)", quote, re.I) and not RE_E1_PROOF.search(quote):
        return False
    return bool(RE_E1_PROOF.search(quote) or _late_stage_amount(quote, company or "we"))


RE_HEADCOUNT = re.compile(r"\b\d[\d,]*\+?\s*(?:employees|people|team members|staff|person team|-person)|"
                          r"\b(?:team|company) of\s+\d|\bheadcount\b", re.I)
TOOL_SIGNALS = {"M3", "F2", "F4", "F9", "F19", "F20", "F21"}
RE_CUSTOMER_LIST = re.compile(r"\b(?:including|customers?|clients?|trusted by|used by|loved by|companies like|teams (?:at|like)|"
                              r"such as|tool of choice for|powering)\b", re.I)

# "Recent" signals: the answer is decided by the date in the AI's own quote, not by the AI's date arithmetic
# (in testing it found "closed a $45M Series B on 4/17/2026" and still answered "not in the last 6 months").
RECENT = {  # signal: (days, words that show the right kind of event)
    "B1": (183, r"rais|closed|secured|funding|round|seed|series|SAFE|investment"),
    "B2": (365, r"join|led by|invest|backed|accelerator|batch|new investor|participat"),
    "B6": (548, r"convert|reincorporat|became|formerly|now (?:a|an) .{0,20}(?:corporation|Inc)"),
    "B7": (365, r"incorporat|founded|formed|registered"),
    "B12": (183, r"launch|introduc|now available|rolled out|announc|new (?:plan|pricing|tier|product)"),
    "B13": (183, r"board|appoint|joins as|investor update"),
}
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
RE_DATES = [
    (re.compile(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b"), lambda m: (int(m[3]), int(m[1]), int(m[2]))),
    (re.compile(rf"\b({MONTHS})[a-z]*\.?\s+(\d{{1,2}}),?\s+(20\d\d)\b", re.I), lambda m: (int(m[3]), _month(m[1]), int(m[2]))),
    (re.compile(rf"\b(\d{{1,2}})\s+({MONTHS})[a-z]*\.?,?\s+(20\d\d)\b", re.I), lambda m: (int(m[3]), _month(m[2]), int(m[1]))),
    (re.compile(rf"\b({MONTHS})[a-z]*\.?\s+(20\d\d)\b", re.I), lambda m: (int(m[2]), _month(m[1]), 15)),
]


def _month(name: str) -> int:
    return MONTHS.split("|").index(name.lower()[:3] if name.lower()[:4] != "sept" else "sep") + 1


def _dates(text: str) -> list:
    from datetime import date
    out = []
    for rx, parts in RE_DATES:
        for m in rx.finditer(text or ""):
            try:
                out.append(date(*parts(m)))
            except ValueError:
                pass
    return out


def recent_check(sid: str, ans: str, data: dict) -> tuple[str, str]:
    """For 'recent' signals: (answer, note). Yes needs a dated, matching event inside the window; a No whose own
    quote shows such an event becomes Yes."""
    from datetime import date
    days, words = RECENT[sid]
    quote = data.get("evidence_quote") or ""
    text = " ".join(str(data.get(k) or "") for k in ("evidence_quote", "announced", "date", "reason"))
    inside = [d for d in _dates(text) if 0 <= (date.today() - d).days <= days]
    event = re.search(words, quote, re.I)
    if inside and event:
        return "yes", f"dated {max(inside).isoformat()}, within {days} days"
    if ans == "yes":
        return "unknown", "no dated event inside the window in the quote, so not counted"
    return ans, ""


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
        quote = data.get("evidence_quote") or ""
        roles = (result.get("crawl") or {}).get("jobs_count") or 0
        headcount = data.get("headcount_estimate") or 0
        if not RE_HEADCOUNT.search(quote) or (roles and headcount and headcount < roles):
            return  # "31 open roles" is not a headcount, and 15 people can't be hiring 31
        result["segment_ai"] = {"value": data["value"], "headcount": data.get("headcount_estimate"),
                                "evidence": data.get("evidence_quote", "")}
    elif pid == "fields/legal_entity_type" and data.get("value") not in (None, "Unknown"):
        if result.get("entity_type", "Unknown") == "Unknown":
            result["entity_type"] = data["value"]
    elif pid == "fields/competitors_used":
        result["competitors"] = sorted(set(result.get("competitors", [])) | set(data.get("competitors_used", [])))


STAGE_ORDER = ["Pre-Seed", "Seed", "Series A", "Series B", "Series C or later"]


def _stage_from_round(result: dict, data: dict) -> None:
    """A confirmed recent round names the current stage (e.g. Mintlify's site says Seed; it closed a Series B)."""
    text = f"{data.get('round') or ''} {data.get('evidence_quote') or ''}"
    m = re.search(r"\b(pre-?seed|seed|series\s+([a-h]))\b", text, re.I)
    if not m:
        return
    stage = "Pre-Seed" if "pre" in m[1].lower() else "Seed" if not m[2] else (
        f"Series {m[2].upper()}" if m[2].lower() in "ab" else "Series C or later")
    result["recent_round_stage"] = stage


def _apply_recent_stage(result: dict) -> None:
    """Run after all answers: the latest confirmed round wins over an older stage from the site or another answer."""
    stage = result.get("recent_round_stage")
    if not stage:
        return
    current = (result.get("funding_stage") or "").split(" (")[0]
    if current not in STAGE_ORDER or STAGE_ORDER.index(stage) > STAGE_ORDER.index(current):
        result["funding_stage"] = f"{stage} (confirmed)"
        if stage == "Series C or later" and result.get("signals", {}).get("F1", {}).get("value") == "yes":
            result["signals"]["F1"] = {**result["signals"]["F1"], "value": "no",
                                       "note": "a recent round is Series C or later", "method": "ai"}


def _confirm_stage(result: dict, data: dict, step: int) -> None:
    """F1 was Yes from a directory (YC, Form D: venture-backed, stage not shown). A confirmed round with a quote becomes
    its proof; a confirmed Series C or later turns it to No (outside pre-seed to Series B), which needs high + domain."""
    stage, quote = (data.get("value") or "").strip(), (data.get("evidence_quote") or "").strip()
    entity = data.get("entity_match", "domain") if step == 2 else "domain"
    if data.get("basis") != "confirmed" or not quote:
        return
    url = data.get("source", "") if str(data.get("source", "")).startswith("http") else ""
    where = "web search" if step == 2 else "company website"
    if stage in ("Pre-Seed", "Seed", "Series A", "Series B") and entity in ("domain", "name_and_location"):
        result["signals"]["F1"] = {**result["signals"]["F1"], "evidence": quote[:300], "url": url or result["signals"]["F1"].get("url", ""),
                                   "method": "ai", "note": f"{stage} (confirmed), AI, {where} ({data.get('confidence')} confidence); "
                                                           f"also {result['signals']['F1'].get('evidence', '')[:80]}"}
    elif stage == "Series C or later" and data.get("confidence") == "high" and entity == "domain":
        result["signals"]["F1"] = {**result["signals"]["F1"], "value": "no", "evidence": quote[:300], "url": url,
                                   "method": "ai", "note": f"confirmed Series C or later, AI, {where}"}


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
            if current["value"] == "yes" and current.get("method") == "directory":
                _confirm_stage(result, data, step)
                current = result["signals"][sid]
        if current["value"] not in (CHECK, UNKNOWN):
            continue  # code or a directory already found proof; AI doesn't overrule it

        if pid == "fit/venture_backed_stage":
            ans = data.get("venture_backed", "unknown")
            stage = (data.get("value") or "").strip()
            if stage in ("Series C or later", "Bootstrapped"):
                ans = "no"  # funded, but not at pre-seed to Series B (or not venture-backed at all)
            elif ans == "yes" and stage not in ("Pre-Seed", "Seed", "Series A", "Series B"):
                ans = "unknown"
        else:
            ans = data.get("answer", "unknown")
        recent_note = ""
        if sid in RECENT and ans in ("yes", "no"):
            ans, recent_note = recent_check(sid, ans, data)
            if sid == "B1" and ans == "yes":
                _stage_from_round(result, data)
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
        company = result.get("company") or result.get("domain", "").split(".")[0]
        if sid == "E1" and ans == "yes" and not late_stage_proof(data.get("evidence_quote") or "", company):
            conf_ok = False  # the quote must show the round letter, an IPO / listing, or a $100M+ round / $1B+ valuation
        if sid in TOOL_SIGNALS and ans == "yes" and RE_CUSTOMER_LIST.search(data.get("evidence_quote") or ""):
            conf_ok = False  # "40,000 companies (including OpenAI, Coinbase, and Ramp)" names a customer, not their card provider
        if pid == "fit/venture_backed_stage" and data.get("basis") == "estimated":
            conf_ok = False  # an estimated stage is reported in "Funding stage" but earns no points
        if ans in ("yes", "no") and conf_ok and entity_ok:
            value, note = ans, f"{method} ({conf} confidence): {data.get('reason', '')}" + (f" [{recent_note}]" if recent_note else "")
        else:
            # not enough proof: a clue the AI couldn't confirm no longer holds the company back
            value = UNKNOWN
            note = f"{method} said '{ans}' with {conf} confidence"
            if step == 2:
                note += f" and entity match '{entity}'"
            note += f", not enough to count: {data.get('reason', '')}"
        quote = data.get("evidence_quote") or current["evidence"]
        if sid == "E1" and len(quote) > 300:
            m = RE_E1_PROOF.search(quote)  # keep the words that prove it, not just the start of a long quote
            if m:
                quote = "..." + quote[max(0, m.start() - 180): m.end() + 100] + "..."
        result["signals"][sid] = {"value": value, "evidence": quote[:300],
                                  "url": url, "note": note[:300], "method": "ai"}
        if value == "yes":
            for tool in data.get("tools_used", []) or []:
                result["partners"] = sorted(set(result.get("partners", [])) | {tool})

    _apply_recent_stage(result)
    redecide(result, phase)
    result.setdefault("ai", {})["answered"] = applied
    return result
