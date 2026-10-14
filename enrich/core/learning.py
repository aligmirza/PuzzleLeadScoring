"""The product learning from its own runs: what went wrong, what it costs, and the fixes that follow.

    record()   one event, appended to DATA_DIR/learning/events.jsonl (never raises: learning must not break a run)
                 answer          an AI answer and what became of it: counted, rejected (with why) or unknown
                 error           a crash, with where it happened
                 cannot_answer   the attached model couldn't answer the way the question asks
                 correction      the user rejected or corrected a data point in test mode
    report()   groups the events into patterns, each with a proposal: what to change, the proof, and who decides
                 safety "auto"     small and safe: crash fixes (made by the GTM engineer in Claude Code, tests must
                                   pass) and switching a phase 2 data point's web search to on-clue when it almost
                                   never finds anything (applied here, tests run, reverted if they fail)
                 safety "approve"  prompt, rule and workflow changes: proposed, applied only after the user says yes
    apply()    applies a setting proposal (tests run; reverted on failure); every change goes to docs/IMPROVEMENTS.md
    mark()     records the outcome of a proposal made by hand (code, prompt or rule edits)

A decided proposal (applied, rejected, done) only comes back when new events after that decision show the same
pattern again. LEARNING_AUTO_APPLY=0 in .env turns the automatic part off.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
from collections import defaultdict
from pathlib import Path

from .paths import DATA_DIR, ROOT

# the unit tests never write to the real log: under `python -m unittest` it goes to a temp folder
_TESTING = "unittest" in (sys.argv[0] if sys.argv else "")
DIR = Path(os.environ.get("LEARNING_DIR") or (Path(tempfile.gettempdir()) / "puzzle_learning_tests" if _TESTING
                                              else DATA_DIR / "learning"))
EVENTS = DIR / "events.jsonl"
STATE = DIR / "proposals.json"
CHANGELOG = ROOT / "docs" / "IMPROVEMENTS.md"
WEB_COST = 0.011  # one OpenAI web search answer (measured); $0 through MCP, but the chat's time and limits too

MIN_SAMPLES = 8          # answers before a web-search pattern counts
UNKNOWN_SHARE = 0.9      # ... and the share of them that found nothing
REJECTED_MIN = 3         # rejections of the same kind for a prompt proposal
CANNOT_MIN = 3


def auto_on() -> bool:
    return os.environ.get("LEARNING_AUTO_APPLY", "1").strip() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------
def record(kind: str, **fields) -> None:
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        with open(EVENTS, "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, **fields}, default=str) + "\n")
    except Exception:  # noqa: BLE001  (learning never breaks a run)
        pass


def error(where: str, exc: BaseException, **fields) -> None:
    """A crash: the deepest line of our own code in the traceback says where to look."""
    place = where
    for fr in reversed(traceback.extract_tb(exc.__traceback__)):
        if "/enrich/" in fr.filename:
            place = f"{fr.filename.split('/enrich/', 1)[1]}:{fr.lineno} {fr.name}"
            break
    record("error", where=where, place=place, type=type(exc).__name__, message=str(exc)[:300], **fields)


def outcomes(result: dict, answered: list[tuple[str, int]], answers: dict, via: str, list_name: str, domain: str) -> None:
    """One 'answer' event per AI answer just given: counted, rejected (an answer the quality checks refused) or
    unknown (the model found nothing). answers = Store.get_ai(domain, phase)."""
    from ..ai.apply import counted
    from ..checks import datapoints
    for pid, step in answered:
        sid = datapoints.sid_for_prompt(pid)
        if not sid:
            continue
        data = ((answers.get(pid) or {}).get(step) or {}).get("answer") or {}
        given = str(data.get("answer", data.get("venture_backed", data.get("value", ""))) or "").lower()
        v = (result.get("signals") or {}).get(sid) or {}
        if counted(v):
            outcome = "counted"
        elif given in ("", "unknown", "not found"):
            outcome = "unknown"
        else:
            outcome = "rejected"
        record("answer", sid=sid, prompt=pid, step="web" if step == 2 else "pages", outcome=outcome, given=given,
               why=(v.get("note") or "")[:200] if outcome == "rejected" else "", via=via, list=list_name, domain=domain)


# ---------------------------------------------------------------------------
def _events() -> list[dict]:
    if not EVENTS.exists():
        return []
    out = []
    for line in EVENTS.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(s: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


def _id(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8]


REJECT_KINDS = [
    ("no quote", re.compile(r"quote", re.I)),
    ("wrong company or domain", re.compile(r"domain|entity|name only|name_only|another company|match", re.I)),
    ("too old", re.compile(r"older|old|date|stale|months", re.I)),
    ("low confidence", re.compile(r"low confidence|confidence", re.I)),
    ("absence taken as proof", re.compile(r"absence|doesn't mention|not mentioned|no mention", re.I)),
]


def _reject_kind(why: str) -> str:
    return next((k for k, rx in REJECT_KINDS if rx.search(why or "")), "other")


def report(auto_apply: bool | None = None) -> dict:
    """Patterns in what happened since each pattern's last decision, with a proposal for each."""
    from ..checks import datapoints
    from ..checks.rules import SIGNALS
    state = _state()
    events = _events()
    proposals = []

    def since(pid: str) -> str:
        return (state.get(pid) or {}).get("decided_at", "")

    def add(p: dict, evs: list[dict]) -> None:
        cut = since(p["id"])
        fresh = [e for e in evs if e["at"] > cut]
        if not fresh or not p["_ok"](fresh):
            return
        p.pop("_ok")
        p["count"] = len(fresh)
        p["examples"] = sorted({e.get("domain") for e in fresh if e.get("domain")})[:5]
        p["status"] = (state.get(p["id"]) or {}).get("status", "open") if not cut else "open (seen again)"
        p["proof"] = p["proof"](fresh)
        proposals.append(p)

    # crashes: fixed straight away by the GTM engineer (tests must pass), the user is told after
    errs = defaultdict(list)
    for e in events:
        if e["kind"] == "error":
            errs[(e.get("place"), e.get("type"))].append(e)
    for (place, typ), evs in errs.items():
        add({"id": _id("error", place, typ), "kind": "code_fix", "safety": "auto", "data_point": "",
             "title": f"Crash: {typ} at {place}",
             "change": f"Fix the code at {place} so this input is handled, add a test with it, run all tests.",
             "proof": lambda f: f"{len(f)}× {f[-1].get('message', '')}", "_ok": lambda f: True}, evs)

    answers = [e for e in events if e["kind"] == "answer"]
    # web searches that almost never find anything: on-clue only (phase 2 data points only)
    by_web = defaultdict(list)
    for e in answers:
        if e.get("step") == "web":
            by_web[e["sid"]].append(e)
    for sid, evs in by_web.items():
        if SIGNALS[sid][0] in ("must", "exclusion") or datapoints.step(sid, "ai_web").get("run") != "if_open":
            continue
        add({"id": _id("web_if_clue", sid), "kind": "setting", "safety": "auto", "data_point": sid,
             "title": f"{sid} {SIGNALS[sid][1]}: web search almost never finds it",
             "change": "ai_web.run: if_open → if_clue (search only when the rules or the page answer saw a hint)",
             "setting": {"ai_web": {"run": "if_clue"}},
             "proof": lambda f: (f"{sum(e['outcome'] == 'unknown' for e in f)} of {len(f)} web answers found nothing "
                                 f"(about ${len(f) * WEB_COST:.2f} with OpenAI)"),
             "_ok": lambda f: len(f) >= MIN_SAMPLES and sum(e["outcome"] == "unknown" for e in f) / len(f) >= UNKNOWN_SHARE},
            evs)

    # answers the quality checks refused, the same way, again and again: the prompt needs a line
    rej = defaultdict(list)
    for e in answers:
        if e["outcome"] == "rejected":
            rej[(e["sid"], _reject_kind(e.get("why", "")))].append(e)
    for (sid, kind), evs in rej.items():
        add({"id": _id("rejected", sid, kind), "kind": "prompt", "safety": "approve", "data_point": sid,
             "title": f"{sid} {SIGNALS[sid][1]}: answers rejected ({kind})",
             "change": f"Add a 'Watch out for' line to {datapoints.step(sid, 'ai_pages').get('prompt')} so answers stop "
                       f"failing on '{kind}'; or loosen the check if the answers were right.",
             "proof": lambda f: "; ".join(sorted({e.get('why', '')[:120] for e in f})[:3]),
             "_ok": lambda f: len(f) >= REJECTED_MIN}, evs)

    # questions the attached model couldn't answer the way they ask
    cant = defaultdict(list)
    for e in events:
        if e["kind"] == "cannot_answer" and e.get("sid"):
            cant[e["sid"]].append(e)
    for sid, evs in cant.items():
        add({"id": _id("cannot", sid), "kind": "workflow", "safety": "approve", "data_point": sid,
             "title": f"{sid} {SIGNALS[sid][1]}: the chat model couldn't answer it",
             "change": "Give this data point a free rule or directory that answers it, or send it to list_ai.",
             "proof": lambda f: "; ".join(sorted({e.get('why', '')[:120] for e in f})[:3]),
             "_ok": lambda f: len(f) >= CANNOT_MIN}, evs)

    # the user's corrections in test mode: always worth a look
    corr = defaultdict(list)
    for e in events:
        if e["kind"] == "correction" and e.get("sid"):
            corr[e["sid"]].append(e)
    for sid, evs in corr.items():
        add({"id": _id("correction", sid), "kind": "prompt_or_rule", "safety": "approve", "data_point": sid,
             "title": f"{sid} {SIGNALS[sid][1]}: corrected by the user",
             "change": "Find why the waterfall gave the wrong answer and change the rule, prompt or setting behind it.",
             "proof": lambda f: "; ".join(f"{e.get('domain')}: got {e.get('got')}, expected {e.get('expected') or '?'} "
                                          f"({e.get('notes', '')[:80]})" for e in f[-3:]),
             "_ok": lambda f: True}, evs)

    applied = []
    if (auto_on() if auto_apply is None else auto_apply):
        for p in proposals:
            if p["safety"] == "auto" and p["kind"] == "setting" and p["status"].startswith("open"):
                applied.append(apply(p["id"], _proposal=p, by="automatic (safe change)"))
    stats = defaultdict(lambda: defaultdict(int))
    for e in answers:
        stats[e["sid"]][e["outcome"]] += 1
    return {"proposals": proposals, "applied_automatically": applied,
            "answers_seen": len(answers), "errors_seen": sum(1 for e in events if e["kind"] == "error"),
            "per_data_point": {k: dict(v) for k, v in sorted(stats.items())},
            "how_to_decide": "auto: crash fixes are made by the GTM engineer and tested, then reported; setting changes "
                             "are applied here and reported. approve: show the proposal and its proof to the user, and "
                             "only after a yes call apply_improvement (settings) or make the edit and call "
                             "mark_improvement. A rejected proposal comes back only if it keeps happening."}


# ---------------------------------------------------------------------------
def _tests() -> tuple[bool, str]:
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT, capture_output=True,
                       text=True, timeout=900)
    lines = (r.stderr or r.stdout or "").strip().splitlines()
    return r.returncode == 0, lines[-1] if lines else ""


def apply(proposal_id: str, _proposal: dict | None = None, by: str = "the user's approval") -> dict:
    """Apply a setting proposal to its data point's file, run the tests, revert if they fail, and log it."""
    from ..checks import datapoints
    p = _proposal or next((x for x in report(auto_apply=False)["proposals"] if x["id"] == proposal_id), None)
    if not p:
        return {"id": proposal_id, "applied": False, "why": "no open proposal with that id"}
    if p["kind"] != "setting":
        return {"id": proposal_id, "applied": False,
                "why": f"a {p['kind']} change is made by editing the files: {p['change']} Then call mark_improvement."}
    sid = p["data_point"]
    path = datapoints.path(sid, datapoints.load()[sid])
    before = path.read_text()
    data = json.loads(before)
    for layer, settings in p["setting"].items():
        data.setdefault("steps", {}).setdefault(layer, {}).update(settings)
    path.write_text(json.dumps(data, indent=2) + "\n")
    datapoints.load.cache_clear()
    ok, last = _tests()
    if not ok:
        path.write_text(before)
        datapoints.load.cache_clear()
        mark(proposal_id, "failed", f"tests failed, reverted: {last}", _proposal=p)
        return {"id": proposal_id, "applied": False, "why": f"tests failed, change reverted ({last})"}
    mark(proposal_id, "applied", f"{p['change']} ({by}; tests passed)", _proposal=p)
    return {"id": proposal_id, "applied": True, "data_point": sid, "change": p["change"], "proof": p["proof"],
            "file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path), "by": by}


def mark(proposal_id: str, status: str, note: str = "", _proposal: dict | None = None) -> dict:
    """Record a decision: applied, done (fixed by hand), rejected or failed. Logged in docs/IMPROVEMENTS.md."""
    if status not in ("applied", "done", "rejected", "failed"):
        raise ValueError("status must be applied, done, rejected or failed")
    p = _proposal or next((x for x in report(auto_apply=False)["proposals"] if x["id"] == proposal_id), {})
    s = _state()
    s[proposal_id] = {"status": status, "decided_at": time.strftime("%Y-%m-%d %H:%M:%S"), "note": note,
                      "title": p.get("title", (s.get(proposal_id) or {}).get("title", ""))}
    _save_state(s)
    if not CHANGELOG.exists():
        CHANGELOG.write_text("# Improvements\n\nWhat the pipeline changed about itself, from what it learned while scoring "
                             "leads (`enrich/core/learning.py`). Newest last.\n\n| Date | Proposal | Status | What and why |\n"
                             "|---|---|---|---|\n")
    proof = p.get("proof", "")
    cell = f"{note}{' Proof: ' + proof if proof else ''}".replace("|", "/").replace("\n", " ")
    with open(CHANGELOG, "a") as f:
        f.write(f"| {s[proposal_id]['decided_at'][:10]} | {s[proposal_id]['title']} (`{proposal_id}`) | {status} | {cell} |\n")
    return {"id": proposal_id, "status": status, "note": note}
