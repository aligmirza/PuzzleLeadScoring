"""Each data point's own waterfall, from config/datapoints/<group>/<name>.json. Edit a file to change how that data
point is answered; the run schedules everything together for speed (see PIPELINE.md, The waterfall).

    steps.rules        {"on": true}     pattern rules on the scraped pages (free)
    steps.directories  {"on": true}     YC, SEC, IRS, Internet Archive (free; only some data points are covered)
    steps.ai_pages     {"run": ..., "prompt": "<prompt file>", "requires_page": "team"}
                       run: if_open (empty or Needs check) | if_clue (Needs check only) | always | off
    steps.ai_web       {"run": ..., "when_site_unreached": false}
                       run: after_pages (page answer empty or low confidence) | if_open (still open after the page
                       answer, also when it was rejected) | if_clue (still open and there is a hint: the rules
                       flagged it, or the page answer saw an unproven yes/no) | first (web only) | off
    steps.provider     {"run": "if_open" | "off", "provider": "prospeo"}
    run_if             {"M1": ["yes", "unknown"]}   conditional run between data points (see RUN_IF)

A layer only runs while the data point is still open; the first layer that confirms it stops the waterfall.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from ..core.paths import CONFIG
from .rules import SIGNALS

DIR = CONFIG / "datapoints"
ALL_LAYERS = False  # test mode (ai_check.py --all-layers): every data point gets every AI layer, to compare layers
GROUP_DIRS = {"must": "must_haves", "exclusion": "exclusions", "fit": "fit", "buying": "buying", "weak": "weak"}

# what each layer can answer at all (code support); a setting outside these has no effect
from ..engine.layers import DIRECTORY_COVERS, PROVIDER_COVERS  # noqa: E402  (the one place that defines reach)
# Prospeo is used only for the finance-person check for now (one team search, 1 credit per company); switch more on
# in each data point's file. PROVIDER_COVERS is what Prospeo *could* answer.
PROVIDER_ON = {"F13", "F14"}
# answered by the rules and the DNS check only: they read every page's text and code, the job posts and DNS records
# for the tool's name, so an AI reading the same text adds cost, not proof
RULES_ONLY = {"F19", "F20", "F21"}
# rarely public: the web search runs only on a clue (rules found a hint, or the page answer saw something unproven);
# a clean "unknown" from the pages ends there, since a search almost never finds these
WEB_ON_CLUE = {"F17", "B8", "B9", "B11", "B13", "B14", "W11"}
# data points whose page answer needs the team page
_NEEDS_TEAM_PAGE = {"F13", "F14"}
# conditional run between data points: a data point's paid steps (AI pages, AI web, provider) run only when each
# parent's answer is one of the listed values. While a parent still has a question waiting, the child waits for it;
# once the parent is final with another value, the child is skipped and shows "Not applicable". Proof the free steps
# already found is kept. Must-haves go one after another (M1, then M2, M3, M4), then the exclusions: each waits for
# every must-have before it, so one proven No stops the rest.
_GOES_ON = ["yes", "unknown"]  # a must-have that isn't proven No lets the next one run
_MUSTS = ["M1", "M2", "M3", "M4"]
RUN_IF = {**{m: {p: _GOES_ON for p in _MUSTS[:i]} for i, m in enumerate(_MUSTS) if i},
          **{f"E{i}": {p: _GOES_ON for p in _MUSTS} for i in range(1, 7)},
          "F12": {"F1": ["yes"]},  # several open roles: only checked for a venture-backed company
          # buying signals that can't happen once the parent is proven No (still asked when it is Unknown)
          **{b: {"F1": _GOES_ON} for b in ("B1", "B2", "B13")},  # no venture funding: no new round, investor or board
          "B12": {"F8": _GOES_ON},   # no paid pricing published: no pricing launch
          "B11": {"F4": _GOES_ON}}   # not on QuickBooks Online: no QuickBooks complaints


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:50]


def defaults() -> dict[str, dict]:
    from ..ai import prompts as P
    by_signal = P.by_signal()
    out = {}
    for sid, (group, label) in SIGNALS.items():
        pid = by_signal.get(sid)
        # every data point with an AI question: AI reads our pages when rules and directories left it open, then the
        # web search if it is still open after that (the rule for all data points; change one file to differ)
        ai_pages = {"run": "if_open" if pid and sid not in RULES_ONLY else "off"}
        if pid:
            ai_pages["prompt"] = pid
        if sid in _NEEDS_TEAM_PAGE:
            ai_pages["requires_page"] = "team"
        # ...except where the prompt file switches web search off for a proof reason (absence can't be found online)
        web = ("off" if not pid or P.web_mode(pid) == "off" or sid in RULES_ONLY else
               "if_clue" if sid in WEB_ON_CLUE else "if_open")
        out[sid] = {
            "id": sid, "name": label, "group": group,
            "steps": {
                "rules": {"on": True},
                "directories": {"on": sid in DIRECTORY_COVERS},
                "ai_pages": ai_pages,
                "ai_web": {"run": web, "when_site_unreached": False},
                "provider": {"run": "if_open" if sid in PROVIDER_ON else "off", "provider": "prospeo"},
            },
            "covers": {"directories": sid in DIRECTORY_COVERS, "provider": sid in PROVIDER_COVERS},
        }
        if sid in RUN_IF:
            out[sid]["run_if"] = RUN_IF[sid]
    return out


def path(sid: str, cfg: dict | None = None) -> Path:
    cfg = cfg or defaults()[sid]
    name = (cfg["steps"]["ai_pages"].get("prompt") or "").split("/")[-1] or _slug(cfg["name"])
    return DIR / GROUP_DIRS[cfg["group"]] / f"{name}.json"


def write_defaults(overwrite: bool = False) -> int:
    n = 0
    for sid, cfg in defaults().items():
        p = path(sid, cfg)
        if p.exists() and not overwrite:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg, indent=2) + "\n")
        n += 1
    load.cache_clear()
    return n


@lru_cache(maxsize=1)
def load() -> dict[str, dict]:
    """Every data point's waterfall; a missing file falls back to today's default."""
    out = defaults()
    if DIR.exists():
        for p in DIR.rglob("*.json"):
            cfg = json.loads(p.read_text())
            if cfg.get("id") in out:
                base = out[cfg["id"]]
                for layer, settings in (cfg.get("steps") or {}).items():
                    base["steps"].setdefault(layer, {}).update(settings)
                if "run_if" in cfg:
                    base["run_if"] = cfg["run_if"] or {}
    return out


def step(sid: str, layer: str) -> dict:
    return load().get(sid, {}).get("steps", {}).get(layer, {})


def rules_on(sid: str) -> bool:
    return step(sid, "rules").get("on", True)


def directories_on(sid: str) -> bool:
    return step(sid, "directories").get("on", True)


def provider_on(sid: str) -> bool:
    return step(sid, "provider").get("run", "off") != "off"

# data points that are the opposite of another one: once that one is proven Yes, the other is No for free
# (engine/resolve.py derive); the opposite waits while that one still has a question, so it isn't paid for twice
OPPOSITES = {
    "W1": "F5",   # serves one local area <-> startup selling nationally
    "W3": "F5",   # physical or single-location business <-> selling nationally
    "F7": "F5",   # local professional services firm <-> selling nationally
    "W5": "M4",   # not making money yet <-> real transaction activity
    "W4": "F3",   # LLC or sole owner <-> Delaware C-Corp
    "W10": "F13",  # on NetSuite with a finance team <-> no finance person visible
    "W12": "M3",  # no fintech tools <-> uses a fintech tool Puzzle connects to
    "W9": "E6",   # store without stock <-> e-commerce holding physical stock
}


def run_if(sid: str) -> dict[str, list[str]]:
    return load().get(sid, {}).get("run_if") or {}


def gate(sid: str, signals: dict, waiting: set | None = None) -> tuple[str, str]:
    """Conditional run: ('run' | 'wait' | 'skip', why). waiting: data points with a question still to answer; None
    means not known, so an Unknown parent counts as still open (never skips)."""
    opp = OPPOSITES.get(sid)
    if opp and waiting is not None and opp in waiting and signals.get(opp, {}).get("value") in ("unknown", "check"):
        return "wait", f"waits for {opp} ({SIGNALS[opp][1]}), its opposite"
    for parent, allowed in run_if(sid).items():
        if parent not in signals:
            continue
        v = signals[parent]["value"]
        if v in ("unknown", "check"):
            if waiting is None or parent in waiting:
                return "wait", f"waits for {parent} ({SIGNALS[parent][1]})"
            v = "unknown"  # nothing left to ask: Unknown is its final answer
        if v not in allowed:
            from .rules import VALUE_NAMES
            return "skip", f"{parent} ({SIGNALS[parent][1]}) is {VALUE_NAMES.get(v, v)}"
    return "run", ""


def waiting(result: dict, done: dict) -> list[tuple[str, str]]:
    """(question, 'pages' or 'web') the waterfall still wants answered, before conditional runs and stop-when-decided:
    a page answer first; the web where the page answer left it open. done = Store.get_ai(domain, phase)."""
    from ..pipeline.common import ai_prompts
    site_ok = (result.get("crawl") or {}).get("status") == "live"
    out = []
    for pid in ai_prompts(result):
        steps = done.get(pid, {})
        sid = sid_for_prompt(pid)
        is_open = still_open(sid, result)
        if not steps:
            if sid and not is_open:
                continue  # already settled after the questions were chosen (e.g. by provider data bought since)
            out.append((pid, "pages" if site_ok and ask_pages(pid, result) else "web"))
        elif 2 not in steps and is_open and web_after(
                pid, (steps.get(1) or {}).get("answer"), True, site_ok,
                clue=bool(sid) and result["signals"].get(sid, {}).get("value") == "check"):
            out.append((pid, "web"))
    return [(p, k) for p, k in out if k == "pages" or step(sid_for_prompt(p) or "", "ai_web").get("run", "after_pages") != "off"]


def runnable(items: list[tuple[str, str]], result: dict) -> list[tuple[str, str]]:
    """The waiting questions whose data point may run now (conditional run): not waiting for a parent, not skipped."""
    sids = {sid_for_prompt(p) for p, _ in items} - {None}
    signals = result.get("signals") or {}
    return [(p, k) for p, k in items if not sid_for_prompt(p) or gate(sid_for_prompt(p), signals, sids)[0] == "run"]


def sid_for_prompt(pid: str) -> str | None:
    for sid, cfg in load().items():
        if cfg["steps"]["ai_pages"].get("prompt") == pid:
            return sid
    return None


def missing_page(pid: str, result: dict | None) -> str:
    """The page kind this question's page step needs (requires_page) when the company doesn't have it, else ''."""
    sid = sid_for_prompt(pid)
    need = step(sid, "ai_pages").get("requires_page") if sid else None
    have = set(((result or {}).get("crawl") or {}).get("pages") or [])
    return need if need and result is not None and need not in have else ""


def ask_pages(pid: str, result: dict | None = None) -> bool:
    """Does this prompt get a page answer (step 1) for this company? Not when the page it needs is missing."""
    if missing_page(pid, result):
        return False
    sid = sid_for_prompt(pid)
    if ALL_LAYERS and sid:
        return step(sid, "ai_pages").get("run", "off") != "off"  # web-first data points have no page answer
    if not sid:
        from ..ai import prompts as P
        return P.web_mode(pid) != "primary"  # fields (vertical, size...) keep their prompt file's setting
    return step(sid, "ai_pages").get("run", "off") != "off" and step(sid, "ai_web").get("run") != "first"


def still_open(sid: str | None, result: dict) -> bool:
    """Is this data point still open for the AI steps? F1 also stays open until its funding stage is confirmed: a
    directory proves "venture-backed" (YC, Form D) but not the stage, which the funding stage line needs."""
    if not sid:
        return True
    if result.get("signals", {}).get(sid, {}).get("value") in ("unknown", "check"):
        return True
    return sid == "F1" and not (result.get("funding_stage") or "").endswith("(confirmed)")


def web_after(pid: str, page_answer: dict | None, still_open: bool, site_ok: bool = True, clue: bool = False) -> bool:
    """Should the web search run after the page answer (or instead of it)? clue: the rules flagged the data point
    (Needs check)."""
    sid = sid_for_prompt(pid)
    if not sid:
        from ..ai import prompts as P
        return P.needs_web(pid, page_answer)
    web = step(sid, "ai_web")
    run = web.get("run", "off")
    if ALL_LAYERS:
        return run != "off"
    if not site_ok:
        return run != "off" and web.get("when_site_unreached", False)
    if run == "first":
        return True
    if run == "if_open":
        return still_open
    if run == "if_clue":  # rarely public: search only when there is something to follow up
        if not still_open:
            return False
        value = (page_answer or {}).get("answer", (page_answer or {}).get("value"))
        return clue or value in ("yes", "no", "Yes", "No")  # a hint the page answer couldn't prove
    if run == "after_pages":
        if not page_answer:
            return False
        value = page_answer.get("answer", page_answer.get("venture_backed", page_answer.get("value")))
        return value in (None, "", "unknown", "Unknown") or page_answer.get("confidence") == "low"
    return False


def questions(signals: dict, page, open_values=("unknown", "check")) -> list[str]:
    """The AI questions a company gets from the data points' own settings. `page(kind)` says if a page exists."""
    out = []
    for sid, v in signals.items():
        cfg = load().get(sid)
        if not cfg:
            continue
        ap, web = cfg["steps"]["ai_pages"], cfg["steps"]["ai_web"]
        pid = ap.get("prompt")
        if not pid:
            continue
        if ALL_LAYERS:
            if ap.get("run") != "off" or web.get("run") != "off":
                out.append(pid)
            continue
        is_open, clue = v["value"] in open_values, v["value"] == "check"
        if web.get("run") == "first":
            if is_open:
                out.append(pid)
            continue
        run = ap.get("run", "off")
        if ap.get("requires_page") and not page(ap["requires_page"]):
            # the page step can't run without that page: still open after rules and directories -> web search
            if is_open and web.get("run", "off") != "off":
                out.append(pid)
            continue
        if run == "always" or (run == "if_open" and is_open) or (run == "if_clue" and clue):
            out.append(pid)
    return out


def unreached_questions(signals: dict) -> list[str]:
    """For a site that couldn't be read: web-only questions for data points set to search anyway."""
    return [cfg["steps"]["ai_pages"]["prompt"] for sid, cfg in load().items()
            if sid in signals and cfg["steps"]["ai_pages"].get("prompt")
            and (cfg["steps"]["ai_web"].get("when_site_unreached") or ALL_LAYERS) and cfg["steps"]["ai_web"].get("run") != "off"
            and (signals[sid]["value"] in ("unknown", "check") or ALL_LAYERS)]


def summary_rows() -> list[list[str]]:
    rows = []
    for sid, cfg in load().items():
        s = cfg["steps"]
        rows.append([sid, cfg["name"],
                     "on" if s["rules"].get("on", True) else "off",
                     ("on" if s["directories"].get("on") else "off") if cfg["covers"]["directories"] else "n/a",
                     s["ai_pages"].get("run", "off") + (f" (needs {s['ai_pages']['requires_page']} page)" if s["ai_pages"].get("requires_page") else ""),
                     s["ai_web"].get("run", "off") + (" + unreached sites" if s["ai_web"].get("when_site_unreached") else ""),
                     s["provider"].get("run", "off") if cfg["covers"]["provider"] else "n/a",
                     str(path(sid, cfg).relative_to(CONFIG.parent))])
    return rows
