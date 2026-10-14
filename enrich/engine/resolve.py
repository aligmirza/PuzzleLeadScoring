"""Run one company through the waterfall, layer by layer, cheapest first.

    1 rules         checks/rules.py          pattern rules on the scraped pages
    2 directories   sources/directories.py   YC, SEC, IRS, Internet Archive
    -- the verdict and the AI questions still open are worked out here (paid work the runners can do next) --
    3 AI on pages   ai/apply.py              answers already bought (ai/runner.py asks new ones)
    4 AI web search ai/apply.py              same
    5 provider      providers/apply.py       Prospeo data already bought (providers/run.py buys new data)

Each layer only fills what the earlier ones left open, and each data point's settings (config/datapoints/) can switch
a layer off for it. The result is saved per list; nothing here spends money.
"""
from __future__ import annotations

from ..checks.rules import Extractor, decide


def free_layers(domain: str, company: str, employees: str, record: dict, pages: dict[str, str], jobs: list[dict],
                phase: str, directory_finder=None) -> dict:
    """Layers 1 and 2 (free), then the verdict and the paid questions still open."""
    x = Extractor(domain, company, employees, record, pages, jobs, phase)
    read = x.collect()
    if read and directory_finder:
        directories_layer(x, directory_finder)
    return x.finish()


def directories_layer(x: Extractor, finder) -> None:
    """Layer 2: look the company up (once per list; saved) and fill what the rules left open."""
    from ..sources import directories
    gate = decide(x.s, x.record.get("status"))["gate"]
    history = None
    if x.phase == "signals":  # old copies of the site, only where they could show a change
        history = {"legal": x.facts.get("entity_suffix") == "corp", "pricing": bool(x.facts.get("prices"))}
    x.directories_found = finder(x.facts.get("legal_name"), x.facts.get("us_clues", []),
                                 gate in ("excluded", "failed must-have"), history)
    directories.apply(x)


def resolve(store, row: dict, record: dict, phase: str) -> dict:
    """The whole waterfall for one company with what is already known and bought; saved and returned."""
    from ..pipeline.common import directory_finder
    pages = {k: store.load_page(row["domain"], k) for k in record.get("pages", {})}
    pages = {k: v for k, v in pages.items() if v}
    bought = store.get_provider(row["domain"])
    if bought:  # the headcount from a Prospeo search already bought sets the size before the questions are chosen
        from ..providers.apply import headcount
        hint = headcount(bought)
        record = {**record, "headcount_hint": hint} if hint else record
    result = free_layers(row["domain"], row["company"], row["employees"], record, pages, store.load_jobs(row["domain"]),
                         phase, directory_finder(store, row))
    answers = store.get_ai(row["domain"], phase)
    if answers:  # layers 3 and 4: AI answers already bought (kept when the rules are re-run)
        from ..ai import apply as ai_apply
        result = ai_apply.apply(result, answers, phase)
    if bought:  # layer 5: provider data already bought
        from ..providers import apply as provider_apply
        result = provider_apply.apply(result, bought, phase)
    close_skipped(store, row["domain"], phase, result)
    store.save_result(row["domain"], result, phase)
    return result


from ..checks.datapoints import OPPOSITES  # noqa: E402  (defined with the conditional runs)


def close_skipped(store, domain: str, phase: str, result: dict) -> None:
    """Settle opposites for free, then the conditional runs (config/datapoints run_if): data points whose parent's
    final answer rules them out are Not applicable. A parent with a question still waiting counts as open."""
    if "signals" not in result:
        return
    derive(result)
    from ..checks import datapoints
    from .merge import gates, redecide
    waiting = {datapoints.sid_for_prompt(p) for p, _ in datapoints.waiting(result, store.get_ai(domain, phase))} - {None}
    gates(result, waiting)
    redecide(result, phase)


def derive(result: dict) -> None:
    """Settle a data point from its opposite's proven Yes (no AI call). Only fills what is still open."""
    s = result.get("signals") or {}
    for sid, other in OPPOSITES.items():
        o = s.get(other) or {}
        if o.get("value") == "yes" and s.get(sid, {}).get("value") in ("unknown", "check"):
            from ..checks.rules import NO, SIGNALS, sig
            s[sid] = sig(NO, f"{SIGNALS[other][1]} is Yes: {o.get('evidence', '')}"[:300], o.get("url", ""),
                         f"the opposite of {other}, which is proven", method="derived")
