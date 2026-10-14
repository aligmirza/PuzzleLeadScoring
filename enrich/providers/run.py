"""Provider steps for a list. Both estimate the credits first and ask before spending.

    python -m enrich providers leads --phase icp       Prospeo company data for companies still in the running whose
                                                       must-haves or exclusions are still open after rules, directories, AI
    python -m enrich providers leads --phase signals   the same for open fit and buying signals (after phase 2)
    python -m enrich people leads [--emails]           decision-makers (founders, C-suite, finance lead) at ICP companies;
                                                       --emails also reveals their verified emails

Credits per month are capped by PROVIDER_MONTHLY_CREDITS in .env (blank: no cap).
"""
from __future__ import annotations

import asyncio
import csv
import os
import re
import sys
from pathlib import Path

from rich.prompt import Confirm
from rich.table import Table

from ..checks.rules import CHECK, SIGNALS, UNKNOWN
from ..core.inputs import load_leads
from ..core.runlog import log
from ..core.store import Store
from ..pipeline import final
from ..pipeline.common import console, rel, run_rules
from . import prospeo
from .apply import RE_FIN_PERSON

class _Covers(dict):
    """Per phase, the data points whose files switch the provider layer on (config/datapoints/)."""
    def __missing__(self, phase):
        from ..checks import datapoints
        icp = phase == "icp"
        return [s for s, (g, _) in SIGNALS.items() if ((g in ("must", "exclusion")) == icp) and datapoints.provider_on(s)]


COVERS = _Covers()
OUT_OF_RUNNING = ("No", "Unknown: site not reached")


def _credits_this_month() -> float:
    from ..core.usage import report
    return sum(r.get("provider_credits", 0) for r in report()["lists"])


def _refuse(credits: float, max_credits: float | None, hint: str) -> dict | None:
    """The answer when a run could use more credits than the caller's limit or the monthly cap; None when it fits."""
    if max_credits is not None and credits > max_credits:
        return {"companies": 0, "credits": 0, "refused": f"Could use up to {credits:g} credits, above max_credits "
                                                           f"{max_credits:g}. Nothing was sent. {hint}"}
    over = _cap_check(credits)
    if over:
        console.print(f"[red]{over}[/]")
        return {"companies": 0, "credits": 0, "refused": over}
    return None


def _cap_check(credits: float) -> str | None:
    try:
        cap = float(os.environ.get("PROVIDER_MONTHLY_CREDITS", "").strip() or "nan")
    except ValueError:
        return None
    if cap != cap:  # not set
        return None
    used = _credits_this_month()
    if used + credits > cap:
        return (f"Monthly provider credits: {used:g} of {cap:g} used; this run could use up to {credits:g}. Nothing was sent. "
                f"Raise PROVIDER_MONTHLY_CREDITS in .env, or use --limit.")
    return None


def _confirm(text: str, assume_yes: bool) -> bool:
    console.print(text)
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        console.print("[yellow]Not running: no terminal to confirm. Add --yes to run without asking.[/]")
        return False
    try:
        return Confirm.ask("Spend the credits?", default=False)
    except (EOFError, KeyboardInterrupt):
        return False


def _balance() -> str:
    try:
        a = prospeo.account()
        return f"Prospeo plan {a.get('current_plan')}, {a.get('remaining_credits')} credits left"
    except prospeo.ProspeoError as e:
        return f"Prospeo balance unknown ({e})"


def _rewrite(folder: Path, store: Store, rows: list[dict], domains: set[str]) -> None:
    from ..pipeline import phase1_icp, phase2_signals
    for row in rows:
        if row["domain"] in domains and store.get_site(row["domain"]):
            run_rules(store, row, store.get_site(row["domain"]), "icp")
            if store.get_result(row["domain"], "signals"):
                r = run_rules(store, row, store.get_site(row["domain"]), "signals")
                r["icp_verdict"] = (store.get_result(row["domain"], "icp") or {}).get("icp_verdict")
                store.save_result(row["domain"], r, "signals")
    icp = [r for r in (store.get_result(x["domain"], "icp") for x in rows) if r]
    phase1_icp.report(icp, folder, store, None)
    sig = [r for r in (store.get_result(x["domain"], "signals") for x in rows) if r]
    if sig:
        phase2_signals.report(sig, folder, store)


def pick_company(store: Store, rows: list[dict], phase: str) -> list[str]:
    """Companies whose data points Prospeo covers are still open, and that don't have Prospeo company data yet."""
    out = []
    for row in rows:
        d = row["domain"]
        r = store.get_result(d, phase) or {}
        icp = store.get_result(d, "icp") or {}
        if not r or icp.get("icp_verdict") in OUT_OF_RUNNING or has_prospeo(store, d):
            continue
        if any(r["signals"].get(sid, {}).get("value") in (UNKNOWN, CHECK) for sid in COVERS[phase] if sid in r["signals"]):
            out.append(d)
    return list(dict.fromkeys(out))


async def _run_many(fn, items, workers: int = 4):
    sem = asyncio.Semaphore(workers)

    async def one(x):
        async with sem:
            return x, await asyncio.to_thread(fn, x)
    return await asyncio.gather(*(one(x) for x in items), return_exceptions=True)


def company_step(folder: Path, phase: str, limit: int = 0, assume_yes: bool = False, max_credits: float | None = None) -> dict:
    if not prospeo.enabled():
        raise SystemExit("Set PROSPEO_API_KEY first: python -m enrich settings --set PROSPEO_API_KEY=...")
    store = Store(folder)
    rows, _ = load_leads(folder / "input.csv")
    domains = pick_company(store, rows, phase)[: limit or None]
    if not domains:
        console.print("No company needs provider data: the earlier layers answered what Prospeo covers.")
        return {"companies": 0, "credits": 0}
    covered = ", ".join(SIGNALS[s][1] for s in COVERS[phase])
    refused = _refuse(len(domains), max_credits, "Use a limit or raise max_credits.")
    if refused:
        return refused
    if not _confirm(f"[bold]Prospeo[/] for {len(domains)} companies still open on: {covered}.\n"
                    f"  At most {len(domains)} credits, 1 per company: one team search gives the company record and its "
                    f"people (no match and repeats within 90 days are free). {_balance()}.", assume_yes):
        return {"companies": 0, "credits": 0}
    spent, matched, errors = 0.0, 0, []
    for item in asyncio.run(_run_many(lambda d: fetch_one(d, size_of(store, d)), domains)):
        if isinstance(item, Exception):
            errors.append(str(item))
            continue
        d, got = item
        spent += got["credits"]
        matched += bool(got["company"])
        save_one(store, d, got)
    _rewrite(folder, store, rows, set(domains))
    final.write(folder, store)
    contacts = write_contacts(folder, store)
    log(folder, "Provider: Prospeo company data and people", companies=len(domains), matched=matched,
        provider_credits=spent, errors=len(errors), contacts_file=rel(contacts))
    console.print(f"Prospeo: {matched} of {len(domains)} companies matched, {spent:g} credits."
                  + (f" [red]{len(errors)} errors[/], first: {errors[0]}" if errors else ""))
    return {"companies": len(domains), "matched": matched, "credits": spent, "errors": errors[:3]}


def _people(domain: str, finance_only: bool = False) -> tuple[list[dict], float, dict | None]:
    """One search, 1 credit if anyone is found: the company's whole team (no seniority filter), or for a large company
    (ENT) only its finance department (finance, accounting, tax, treasury, FP&A), since a 25-person page of a big
    team could miss them. Each result also carries the full company record, so no separate company call is needed."""
    found, credits, _ = prospeo.search_people(domain, departments=["Finance"] if finance_only else None)
    people, company = [], None
    for r in found:
        p = r.get("person") or {}
        c = r.get("company") or {}
        if company is None and (c.get("domain") or "").lower().removeprefix("www.") == domain:
            company = c
        title = p.get("current_job_title") or ""
        people.append({"person_id": p.get("person_id"), "name": p.get("full_name"), "title": title,
                       "linkedin": p.get("linkedin_url"), "location": ", ".join(x for x in (
                           (p.get("location") or {}).get("city"), (p.get("location") or {}).get("country")) if x)})

    def rank(p):
        t = (p["title"] or "").lower()
        return (0 if re.search(r"founder|ceo|chief executive", t) else 1 if RE_FIN_PERSON.search(t) else
                2 if re.search(r"coo|chief operating|operations", t) else 3)
    return sorted(people, key=rank), credits, company


def fetch_one(domain: str, seg: str | None = None) -> dict:
    """At most 1 credit per company. Size decides the search: ENT -> its finance department; otherwise the whole team.
    Only when a whole-team search finds nobody (free) is the company record looked up on its own (1 credit if matched)."""
    finance_only = seg == "ENT"
    people, credits, company = _people(domain, finance_only)
    if not people and not finance_only:
        company, c2, _ = prospeo.company(domain)
        credits += c2
    return {"people": people, "company": company, "credits": credits, "search": "finance department" if finance_only else "whole team"}


def save_one(store: Store, domain: str, got: dict) -> None:
    store.save_provider(domain, "prospeo", "people", people_record(got["people"][:3], got["people"],
                                                                   got.get("search", "whole team")))
    store.save_provider(domain, "prospeo", "company", got["company"] or {"no_match": True})


def size_of(store: Store, domain: str) -> str | None:
    """The segment the rules worked out from proof (list count or Prospeo), None if not known yet."""
    for phase in ("signals", "icp"):
        seg = ((store.get_result(domain, phase) or {}).get("facts") or {}).get("segment")
        if seg:
            return seg
    return None


def finance_open(store: Store, domain: str) -> list[str]:
    """Data points using Prospeo (the finance check) still open after steps 1-4 that can score for this size."""
    from ..checks.rules import unscored
    res = store.get_result(domain, "signals") or {}
    seg = size_of(store, domain)
    skip = set(unscored(seg)) if seg else set()
    return [sid for sid in COVERS["signals"] if sid not in skip
            and res.get("signals", {}).get(sid, {}).get("value") in (UNKNOWN, CHECK)]


def step5_one(folder: Path, store: Store, domain: str) -> dict:
    """Step 5 for one company (MCP and API): the Prospeo finance check, only when it's needed and allowed.
    At most 1 credit; never a second one for the same company."""
    if not prospeo.enabled():
        return {"used": False, "why": "PROSPEO_API_KEY is not set"}
    icp = (store.get_result(domain, "icp") or {}).get("icp_verdict")
    if not icp or icp in OUT_OF_RUNNING:
        return {"used": False, "why": f"not checked: ICP verdict is {icp or 'missing'}"}
    if has_prospeo(store, domain):
        return {"used": False, "why": "already used its one Prospeo credit (data reused)"}
    open_ = finance_open(store, domain)
    if not open_:
        return {"used": False, "why": "the finance check is already answered (or can't score for this size)"}
    over = _cap_check(1)
    if over:
        return {"used": False, "why": over}
    got = fetch_one(domain, size_of(store, domain))
    save_one(store, domain, got)
    record = store.get_site(domain)
    rows = load_leads(folder / "input.csv")[0] if (folder / "input.csv").exists() else []
    row = next((r for r in rows if r["domain"] == domain), None) or {
        "domain": domain, "company": ((record or {}).get("input") or {}).get("company", ""),
        "employees": ((record or {}).get("input") or {}).get("employees", ""), "input": {}}
    if record:
        run_rules(store, row, record, "icp")
        res = run_rules(store, row, record, "signals")
        res["icp_verdict"] = (store.get_result(domain, "icp") or {}).get("icp_verdict")
        store.save_result(domain, res, "signals")
    contacts = write_contacts(folder, store)
    log(folder, "Provider: Prospeo finance check (MCP)", companies=1, provider_credits=got["credits"],
        matched=bool(got["company"]), people=len(got["people"]))
    return {"used": True, "for": open_, "search": got["search"], "credits": got["credits"],
            "people_found": len(got["people"]), "contacts_file": str(contacts)}


def has_prospeo(store: Store, domain: str) -> bool:
    """This company already used its one Prospeo credit (company record or team search)."""
    have = store.get_provider(domain).get("prospeo", {})
    return "people" in have or "company" in have


def people_record(chosen: list[dict], people: list[dict], search: str = "whole team") -> dict:
    """What is saved per company: the contacts kept, finance people, and everyone found with names and titles.
    team_searched is only true for a whole-team search: only that can show nobody works in finance (F13 / F14 No)."""
    finance = people if search == "finance department" else [p for p in people if RE_FIN_PERSON.search(p["title"] or "")]
    return {"list": chosen, "finance": finance[:3], "search": search,
            "found": len(people), "team_searched": search == "whole team",
            "team": [{k: p.get(k) for k in ("name", "title", "linkedin", "location", "person_id")} for p in people]}


def people_step(folder: Path, emails: bool = False, per_company: int = 3, strong_only: bool = False,
                limit: int = 0, assume_yes: bool = False, max_credits: float | None = None) -> dict:
    if not prospeo.enabled():
        raise SystemExit("Set PROSPEO_API_KEY first: python -m enrich settings --set PROSPEO_API_KEY=...")
    store = Store(folder)
    rows, _ = load_leads(folder / "input.csv")
    out_rows = final.read_rows(folder)
    keep = {r["_domain"] for r in out_rows if r.get("ICP") == "YES"
            and (not strong_only or (r.get("Lead tier") or "").startswith("Strong"))}
    candidates = [d for d in dict.fromkeys(r["domain"] for r in rows) if d in keep and finance_open(store, d)]
    used = [d for d in candidates if has_prospeo(store, d)]  # already spent their 1 credit: never a second one
    domains = [d for d in candidates if d not in used][: limit or None]
    if used:
        console.print(f"[dim]{len(used)} companies already have Prospeo data (their one credit is used); "
                      f"their contacts are in the contacts file.[/]")
    if not domains:
        console.print("No ICP company is waiting for contacts.")
        write_contacts(folder, store)
        return {"companies": 0, "credits": 0}
    most = len(domains) + (len(domains) * per_company if emails else 0)
    refused = _refuse(most, max_credits, "Use a limit, fewer contacts, or raise max_credits.")
    if refused:
        return refused
    if not _confirm(f"[bold]Prospeo people[/] for {len(domains)} ICP companies{' (Strong fit only)' if strong_only else ''}: "
                    f"founders, C-suite, finance and other leads, top {per_company} kept per company.\n"
                    f"  About {len(domains)} credits for the searches"
                    + (f", plus up to {len(domains) * per_company} for verified emails" if emails else " (emails not revealed; add --emails)")
                    + f". {_balance()}.", assume_yes):
        return {"companies": 0, "credits": 0}
    spent, found_total, errors = 0.0, 0, []
    for item in asyncio.run(_run_many(lambda d: fetch_one(d, size_of(store, d)), domains)):
        if isinstance(item, Exception):
            errors.append(str(item))
            continue
        d, got = item
        people = got["people"]
        spent += got["credits"]
        chosen = people[:per_company]
        if emails:
            for p in chosen:
                if not p.get("person_id"):
                    continue
                try:
                    person, c, _ = prospeo.reveal_email(p["person_id"])
                except prospeo.ProspeoError as e:
                    errors.append(str(e))
                    continue
                spent += c
                em = (person or {}).get("email") or {}
                p["email"], p["email_status"] = em.get("email"), em.get("status")
        found_total += len(people)
        store.save_provider(d, "prospeo", "people", people_record(chosen, people, got["search"]))
        store.save_provider(d, "prospeo", "company", got["company"] or {"no_match": True})
    _rewrite(folder, store, rows, set(domains))
    path = final.write(folder, store)
    contacts = write_contacts(folder, store)
    log(folder, "Provider: Prospeo people", companies=len(domains), contacts=found_total, provider_credits=spent,
        emails=emails, errors=len(errors))
    console.print(f"Prospeo people: {found_total} contacts at {len(domains)} companies, {spent:g} credits. "
                  f"Contacts: {rel(contacts)}; final list: {rel(path)}"
                  + (f" [red]{len(errors)} errors[/], first: {errors[0]}" if errors else ""))
    return {"companies": len(domains), "contacts": found_total, "credits": spent, "errors": errors[:3], "file": str(contacts)}


CONTACT_FIELDS = ["Company", "Domain", "Website", "ICP", "Lead score", "Lead tier", "Industry", "Employees",
                  "HQ", "Founded", "Total funding", "Latest round", "Latest round date", "Company LinkedIn",
                  "Name", "Title", "Decision maker", "Person LinkedIn", "Person location", "Email", "Email status"]


def write_contacts(folder: Path, store: Store) -> Path:
    """Every person Prospeo found, one row each, with the company's details and its ICP, score and tier.
    output/<list>_contacts.csv, apart from the results file."""
    out = folder / "output" / f"{folder.name}_contacts.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    scored = {r["_domain"]: r for r in final.read_rows(folder)} if (folder / "output").exists() else {}
    names = {}
    if (folder / "input.csv").exists():
        names = {r["domain"]: r["company"] for r in load_leads(folder / "input.csv")[0]}
    rows = []
    bought = [r[0] for r in store.db.execute("SELECT DISTINCT domain FROM providers WHERE provider = 'prospeo'")]
    for d in dict.fromkeys([*names, *store.all_domains(), *bought]):
        data = store.get_provider(d).get("prospeo", {})
        ppl = data.get("people") or {}
        team = ppl.get("team") or ppl.get("list") or []
        if not team:
            continue
        c = data.get("company") if not (data.get("company") or {}).get("no_match") else {}
        c = c or {}
        loc, fund = c.get("location") or {}, c.get("funding") or {}
        emails = {p.get("person_id"): p for p in ppl.get("list") or []}
        r = scored.get(d, {})
        company = {"Company": c.get("name") or names.get(d) or d, "Domain": d, "Website": c.get("website") or "",
                   "ICP": r.get("ICP", ""), "Lead score": r.get("Lead score", ""), "Lead tier": r.get("Lead tier", ""),
                   "Industry": c.get("industry") or "", "Employees": c.get("employee_count") or "",
                   "HQ": ", ".join(x for x in (loc.get("city"), loc.get("state"), loc.get("country")) if x),
                   "Founded": c.get("founded") or "", "Total funding": fund.get("total_funding_printed") or "",
                   "Latest round": fund.get("latest_funding_stage") or "",
                   "Latest round date": (fund.get("latest_funding_date") or "")[:10],
                   "Company LinkedIn": c.get("linkedin_url") or ""}
        for p in team:
            title = p.get("title") or ""
            em = emails.get(p.get("person_id")) or {}
            rows.append({**company, "Name": p.get("name"), "Title": title,
                         "Decision maker": "yes" if re.search(r"founder|ceo|chief|president|owner|coo|cfo|head of finance|"
                                                              r"vp,? finance|controller", title, re.I) else "",
                         "Person LinkedIn": p.get("linkedin") or "", "Person location": p.get("location") or "",
                         "Email": em.get("email") or "", "Email status": em.get("email_status") or ""})
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CONTACT_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return out


def show_people(store: Store, domain: str) -> Table | None:
    people = (store.get_provider(domain).get("prospeo", {}).get("people") or {}).get("list") or []
    if not people:
        return None
    t = Table(header_style="bold")
    for c in ("Name", "Title", "LinkedIn", "Email"):
        t.add_column(c, overflow="fold")
    for p in people:
        t.add_row(p.get("name") or "", p.get("title") or "", p.get("linkedin") or "", p.get("email") or "")
    return t

