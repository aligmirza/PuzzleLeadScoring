"""How answers from different layers are combined: the one set of quality rules every layer goes through.

    settle()    a directory or provider answer: only fills what earlier layers left open; strong matches only can
                make a company NO; a strong answer that contradicts the website becomes Needs check
    redecide()  the ICP verdict again after a layer changed answers
AI answers have their own extra checks (quote, confidence, dates, customers vs tools) in ai/apply.py, then the same
rule: they only fill what is still open.
"""
from __future__ import annotations

from ..checks.rules import CHECK, NO, SIGNALS, UNKNOWN, YES, decide, sig


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


def redecide(result: dict, phase: str) -> dict:
    """Phase 1's verdict, worked out again from the current answers."""
    if phase == "icp" and "signals" in result:
        d = decide(result["signals"], result.get("crawl", {}).get("status"))
        result.update(gate=d["gate"], icp_verdict=d["verdict"], exclusion_reason=d["exclusion_reason"],
                      icp_checks=[SIGNALS[x][1] for x in d["checks"]], must_haves_confirmed=len(d["confirmed"]))
    return result
