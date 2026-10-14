"""How answers from different layers are combined: the one set of quality rules every layer goes through.

    settle()    a directory or provider answer: only fills what earlier layers left open; strong matches only can
                make a company NO; a strong answer that contradicts the website becomes Needs check
    redecide()  the ICP verdict again after a layer changed answers
    gates()     conditional runs: data points that don't apply any more show Not applicable
AI answers have their own extra checks (quote, confidence, dates, customers vs tools) in ai/apply.py, then the same
rule: they only fill what is still open.
"""
from __future__ import annotations

from ..checks.rules import CHECK, NA, NO, SIGNALS, UNKNOWN, YES, decide, sig


def settle(s: dict, sid: str, value: str, evidence: str, url: str, note: str, match: str, method: str = "directory") -> bool:
    """Write a directory or provider answer if the quality rules allow it. Returns True when written."""
    from ..checks import datapoints
    if (method == "directory" and not datapoints.directories_on(sid)) or (method == "provider" and not datapoints.provider_on(sid)):
        return False  # this layer is switched off in the data point's own file
    group = SIGNALS[sid][0]
    strict = (group == "exclusion" and value == YES) or (group == "must" and value == NO)
    if value in (YES, NO) and not (match == "strong" or (match == "medium" and not strict)):
        if group != "exclusion" or value != YES:
            return False
        value, note = CHECK, f"possible match only, to confirm: {note}"
    cur = s.get(sid, {"value": UNKNOWN, "evidence": ""})
    overrule_guess = sid == "E5" and value == YES and cur["value"] == NO and cur["evidence"].startswith("for-profit legal entity")
    if cur["value"] in (YES, NO) and value in (YES, NO) and value != cur["value"] and match == "strong" and not overrule_guess:
        # the website and an official source disagree: neither is confirmed, so the AI step (or a person) decides
        s[sid] = sig(CHECK, f"website: {cur['evidence']} | {method}: {evidence}", url,
                     f"website said {cur['value']}, {method} said {value} ({note})", method=method)
        if sid == "M1" and s.get("E4", {}).get("value") == YES:
            s["E4"] = sig(CHECK, s["E4"]["evidence"], s["E4"]["url"], f"directory shows a US location: {evidence}",
                          method="directory")
        return True
    if cur["value"] not in (CHECK, UNKNOWN) and not overrule_guess:
        return False
    if cur["value"] == CHECK and value == CHECK:
        return False
    s[sid] = sig(value, evidence, url, f"{method}, {match} match: {note}", method=method)
    return True


def gates(result: dict, waiting: set | None = None) -> None:
    """Close data points that no longer apply (Not applicable, no paid step runs for them). Only open ones (Unknown,
    Needs check) are closed; proof already found stays. waiting: data points with a question still to answer (None:
    not known, so an Unknown parent never closes anything).
      - ICP already NO (a must-have No or an exclusion Yes): the rest of phase 1 can't change it
      - a data point's run_if (config/datapoints): a parent's final answer isn't one it runs on"""
    from ..checks import datapoints
    s = result.get("signals") or {}

    def close(sid, why):
        old = s[sid]
        s[sid] = sig(NA, old.get("evidence", ""), old.get("url", ""), f"Not applicable: {why}", method="skipped")

    d = decide(s, (result.get("crawl") or {}).get("status"))
    if d["gate"] in ("excluded", "failed must-have"):
        why = "ICP is already NO: " + "; ".join(f"{x} ({SIGNALS[x][1]}) is {'Yes' if x in d['excluded'] else 'No'}"
                                                for x in d["excluded"] + d["failed"])
        for sid, (group, _) in SIGNALS.items():
            if group in ("must", "exclusion") and s.get(sid, {}).get("value") in (UNKNOWN, CHECK):
                close(sid, why)
    changed = True
    while changed:  # a closed parent closes its children too
        changed = False
        for sid in list(s):
            if s[sid]["value"] in (UNKNOWN, CHECK):
                run, why = datapoints.gate(sid, s, waiting)
                if run == "skip":
                    close(sid, why)
                    changed = True


def redecide(result: dict, phase: str) -> dict:
    """Phase 1's verdict, worked out again from the current answers."""
    if "signals" in result:
        gates(result)
    if phase == "icp" and "signals" in result:
        d = decide(result["signals"], result.get("crawl", {}).get("status"))
        result.update(gate=d["gate"], icp_verdict=d["verdict"], exclusion_reason=d["exclusion_reason"],
                      icp_checks=[SIGNALS[x][1] for x in d["checks"]], must_haves_confirmed=len(d["confirmed"]))
    return result
