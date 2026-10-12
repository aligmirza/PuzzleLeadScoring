"""Turn provider data into answers: the last layer of the waterfall, after rules, directories and AI.

Prospeo is matched by the company's own website, so its data counts as a MEDIUM-strength source (directories.py):
it can confirm fit signals and a US location, but never make a company NO on its own. A public company, a non-profit
or a foreign HQ from Prospeo becomes "Needs check". It only fills answers the earlier layers left open.
"""
import re
from datetime import date

from ..checks.rules import CHECK, NO, YES
from ..engine.merge import redecide
from ..engine.merge import settle as _settle

FINTECH = {"stripe": "Stripe", "gusto": "Gusto", "rippling": "Rippling", "deel": "Deel", "mercury": "Mercury",
           "brex": "Brex", "ramp": "Ramp", "pulley": "Pulley", "chase": "Chase", "carta": "Carta"}
TECH_SIGNALS = {"F2": ["stripe"], "F9": ["gusto", "rippling", "deel"], "F19": ["mercury", "brex"], "F20": ["ramp", "brex"],
                "F21": ["carta", "pulley"], "F4": ["quickbooks"], "W10": ["netsuite", "sage intacct"]}
EARLY = {"pre-seed": "Pre-Seed", "pre seed": "Pre-Seed", "seed": "Seed", "series a": "Series A", "series b": "Series B"}
RE_FIN_PERSON = re.compile(r"\b(cfo|chief financial|controller|head of finance|vp,? finance|finance|financial|"
                           r"accountant|accounting|bookkeep|treasur)", re.I)
TEAM_PAGE = 25  # Prospeo returns at most this many people per search: a full page may not be the whole team
URL = "https://prospeo.io"


def _stage(raw: str | None) -> str | None:
    s = (raw or "").lower().replace("_", " ")
    for k, v in EARLY.items():
        if s.startswith(k):
            return v
    if re.match(r"series [c-z]|ipo|post ipo|private equity", s):
        return "Series C or later"
    return None


def _tech(company: dict) -> dict[str, str]:
    names = (company.get("technology") or {}).get("technology_names") or []
    return {n.lower(): n for n in names}


def headcount(providers: dict) -> dict | None:
    """Company size from the Prospeo search we already paid for (used for the segment only, choice (a))."""
    data = (providers or {}).get("prospeo") or {}
    comp = data.get("company") or {}
    n = None if comp.get("no_match") else comp.get("employee_count")
    if n:
        return {"value": int(n), "source": "Prospeo company record"}
    team = ((data.get("people") or {}).get("team")) or []
    if (data.get("people") or {}).get("team_searched") and 0 < len(team) < TEAM_PAGE:
        return {"value": len(team), "source": "people Prospeo lists at the company"}
    return None


def apply(result: dict, providers: dict, phase: str) -> dict:
    data = providers.get("prospeo") or {}
    comp = data.get("company") if not (data.get("company") or {}).get("no_match") else None
    people = data.get("people") or {}
    if "signals" not in result or (not comp and not people):
        return result
    s, facts, m, summary = result["signals"], result.setdefault("facts", {}), "medium", []
    from ..checks import datapoints
    from ..engine.layers import PROVIDER_COVERS
    if comp and not any(datapoints.provider_on(sid) for sid in PROVIDER_COVERS - {"F13", "F14"}):
        comp = None  # company data comes free with the team search, but no data point uses Prospeo for it (kept for contacts)
    if comp:
        loc = comp.get("location") or {}
        where = ", ".join(x for x in (loc.get("city"), loc.get("state"), loc.get("country")) if x)
        if loc.get("country_code") == "US":
            _settle(s, "M1", YES, f"Prospeo: HQ {where}", URL, "company record", m, "provider")
        elif loc.get("country_code"):
            _settle(s, "E4", CHECK, f"Prospeo: HQ {where}", URL, "HQ outside the US per Prospeo", m, "provider")
            _settle(s, "M1", CHECK, f"Prospeo: HQ {where}", URL, "HQ outside the US per Prospeo", m, "provider")
        kind = (comp.get("type") or "").lower()
        if kind == "public":
            _settle(s, "E1", YES, "Prospeo lists the company as public", URL, "company type", m, "provider")
        elif kind == "non profit":
            _settle(s, "E5", YES, "Prospeo lists the company as a non-profit", URL, "company type", m, "provider")
        tech = _tech(comp)
        tools = [FINTECH[k] for k in FINTECH if any(k in t for t in tech)]
        if tools:
            _settle(s, "M3", YES, f"Prospeo technology data: {', '.join(tools)}", URL, "detected technologies", m, "provider")
        for sid, keys in TECH_SIGNALS.items():
            hit = [tech[t] for t in tech if any(k in t for k in keys)]
            if hit:
                _settle(s, sid, YES, f"Prospeo technology data: {', '.join(hit)}", URL, "detected technologies", m, "provider")
        jobs = (comp.get("job_postings") or {}).get("active_count") or 0
        if jobs:
            _settle(s, "M4", YES, f"Prospeo: {jobs} active job postings", URL, "open roles count as activity", m, "provider")
        if jobs >= 3:
            _settle(s, "F12", YES, f"Prospeo: {jobs} active job postings", URL, "job postings", m, "provider")
            _settle(s, "B3", YES, f"Prospeo: {jobs} active job postings", URL, "job postings", m, "provider")
        f = comp.get("funding") or {}
        stage = _stage(f.get("latest_funding_stage"))
        if stage == "Series C or later":
            _settle(s, "E1", YES, f"Prospeo: latest round {f.get('latest_funding_stage')}", URL, "funding data", m, "provider")
            _settle(s, "F1", NO, f"Prospeo: latest round {f.get('latest_funding_stage')}", URL, "later than Series B", m, "provider")
        elif stage:
            if _settle(s, "F1", YES, f"Prospeo: latest round {f.get('latest_funding_stage')} ({f.get('latest_funding_date', '')[:10]})",
                       URL, "funding data", m, "provider") and not result.get("funding_stage", "").endswith("(confirmed)"):
                result["funding_stage"] = f"{stage} (confirmed)"
        when = (f.get("latest_funding_date") or "")[:10]
        if when and stage != "Series C or later":
            try:
                if (date.today() - date.fromisoformat(when)).days <= 183:
                    _settle(s, "B1", YES, f"Prospeo: {f.get('latest_funding_stage')} on {when}", URL, "funding data", m, "provider")
            except ValueError:
                pass
        if comp.get("employee_count") and not facts.get("team_size_directory"):
            facts["team_size_directory"] = {"value": comp["employee_count"], "source": "Prospeo"}
        if comp.get("industry") and not result.get("vertical"):
            result["vertical"] = comp["industry"]
        summary.append("Prospeo: " + ", ".join(x for x in (
            f"{comp.get('employee_count')} people" if comp.get("employee_count") else "", where,
            comp.get("type") or "", f"latest round {f.get('latest_funding_stage')}" if f.get("latest_funding_stage") else "",
            ", ".join(tools)) if x))
    if people:
        finance = people.get("finance") or []
        if finance:
            ev = f"Prospeo: {finance[0].get('name')}, {finance[0].get('title')}"
            _settle(s, "F14", YES, ev, finance[0].get("linkedin") or URL, "finance person found", m, "provider")
            _settle(s, "F13", NO, ev, finance[0].get("linkedin") or URL, "finance person found", m, "provider")
        team = people.get("team") or []
        if not finance and people.get("team_searched") and 2 <= len(team) < TEAM_PAGE:
            titles = "; ".join(f"{x.get('name')} ({x.get('title') or 'no title'})" for x in team[:12])
            ev = (f"No finance or accounting title among the {len(team)} people Prospeo lists at "
                  f"{result.get('domain')}: {titles}{'; ...' if len(team) > 12 else ''}")
            _settle(s, "F13", YES, ev, URL, "full team list, no finance title", m, "provider")
            _settle(s, "F14", NO, ev, URL, "full team list, no finance title", m, "provider")
        summary.append(f"{len(people.get('list') or [])} contacts kept of {people.get('found', 0)} found")
    result["providers"] = "; ".join(summary)
    return redecide(result, phase)
