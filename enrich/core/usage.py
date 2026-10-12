"""Usage across every list: rows checked and AI cost per month, and the monthly AI budget.

AI_MONTHLY_BUDGET_USD in .env caps AI spending across the terminal, the API, MCP and trace together. Every AI run
is estimated first; a run whose worst case would go over what's left of the month's budget is refused.
"""
import json
import os
import time

from .paths import LISTS


def _entries(month: str):
    for path in LISTS.glob("*/runs.jsonl"):
        for line in path.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("time", "").startswith(month):
                yield path.parent.name, e


def this_month() -> str:
    return time.strftime("%Y-%m")


def budget() -> float | None:
    try:
        v = os.environ.get("AI_MONTHLY_BUDGET_USD", "").strip()
        return float(v) if v else None
    except ValueError:
        return None


def spent(month: str | None = None) -> float:
    return round(sum(e.get("cost_usd", 0) for _, e in _entries(month or this_month()) if e.get("step", "").startswith("AI")), 4)


def check(worst_usd: float) -> str | None:
    """None if the run fits the month's budget, else the reason it doesn't."""
    cap = budget()
    if cap is None:
        return None
    used = spent()
    if used + worst_usd > cap:
        return (f"Monthly AI budget: ${used:.2f} of ${cap:.2f} used in {this_month()}; this run could cost up to "
                f"${worst_usd:.2f}. Nothing was sent. Raise AI_MONTHLY_BUDGET_USD in .env, or run fewer companies.")
    return None


def report(month: str | None = None) -> dict:
    month = month or this_month()
    lists: dict[str, dict] = {}
    for name, e in _entries(month):
        row = lists.setdefault(name, {"list": name, "companies_checked": 0, "ai_runs": 0, "ai_calls": 0,
                                      "web_searches": 0, "ai_cost_usd": 0.0, "provider_credits": 0.0})
        if e.get("step", "").startswith("Provider"):
            row["provider_credits"] += e.get("provider_credits", 0) or 0
        if e.get("step", "").startswith("Phase 1"):
            row["companies_checked"] = max(row["companies_checked"], e.get("companies", 0))
        if e.get("step", "").startswith("AI"):
            row["ai_runs"] += 1
            row["ai_calls"] += e.get("ai_calls", 0) or 0
            row["web_searches"] += e.get("web_searches", 0) or 0
            row["ai_cost_usd"] = round(row["ai_cost_usd"] + (e.get("cost_usd", 0) or 0), 4)
    total = round(sum(r["ai_cost_usd"] for r in lists.values()), 4)
    cap = budget()
    return {"month": month, "lists": sorted(lists.values(), key=lambda r: -r["ai_cost_usd"]),
            "provider_credits": sum(r["provider_credits"] for r in lists.values()),
            "companies_checked": sum(r["companies_checked"] for r in lists.values()), "ai_cost_usd": total,
            "budget_usd": cap, "budget_left_usd": round(cap - total, 4) if cap is not None else None}
