"""Provider steps for a list. Each estimates the credits first and asks before spending.

People and company data come from LeadMagic (Puzzle's provider) when LEADMAGIC_API_KEY is set, else from Prospeo;
CONTACTS_PROVIDER=leadmagic|prospeo in .env picks one. Data is saved under the provider that found it, and
apply.py reads either.

    python -m enrich providers leads --phase icp       company data (LeadMagic or Prospeo) for companies still in the running whose
                                                       must-haves or exclusions are still open after rules, directories, AI
    python -m enrich providers leads --phase signals   the same for open fit and buying signals (after phase 2)
    python -m enrich people leads [--emails]           decision-makers (founders, C-suite, finance lead) at ICP companies;
                                                       --emails also reveals their verified emails
    python -m enrich emails leads [--phones]           LeadMagic: work emails (and mobiles) for the contacts found
                                                       without one

Credits per month are capped by PROVIDER_MONTHLY_CREDITS (Prospeo) and LEADMAGIC_MONTHLY_CREDITS in .env (blank: no cap).
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
from . import leadmagic, prospeo
from .apply import RE_FIN_PERSON

class _Covers(dict):
    """Per phase, the data points whose files switch the provider layer on (config/datapoints/)."""
    def __missing__(self, phase):
        from ..checks import datapoints
        icp = phase == "icp"
        return [s for s, (g, _) in SIGNALS.items() if ((g in ("must", "exclusion")) == icp) and datapoints.provider_on(s)]


COVERS = _Covers()
OUT_OF_RUNNING = ("No", "Unknown: site not reached", "Needs a call: no website")


def _credits_this_month(field: str = "provider_credits") -> float:
    from ..core.usage import report
    return sum(r.get(field, 0) for r in report()["lists"])


def _refuse(credits: float, max_credits: float | None, hint: str) -> dict | None:
    """The answer when a run could use more credits than the caller's limit or the monthly cap; None when it fits."""
    if max_credits is not None and credits > max_credits:
        return {"companies": 0, "credits": 0, "refused": f"Could use up to {credits:g} {LABEL[source()]} credits, above "
                                                           f"max_credits {max_credits:g}. Nothing was sent. {hint}"}
    over = _cap_check(credits, *CAPS[source()])
    if over:
        console.print(f"[red]{over}[/]")
        return {"companies": 0, "credits": 0, "refused": over}
    return None


def _cap_check(credits: float, env: str = "PROVIDER_MONTHLY_CREDITS", field: str = "provider_credits") -> str | None:
    try:
        cap = float(os.environ.get(env, "").strip() or "nan")
    except ValueError:
        return None
    if cap != cap:  # not set
        return None
    used = _credits_this_month(field)
    if used + credits > cap:
        return (f"Monthly credits ({env}): {used:g} of {cap:g} used; this run could use up to {credits:g}. Nothing was sent. "
                f"Raise {env} in .env, or use --limit.")
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


LABEL = {"leadmagic": "LeadMagic", "prospeo": "Prospeo"}
CAPS = {"leadmagic": ("LEADMAGIC_MONTHLY_CREDITS", "leadmagic_credits"), "prospeo": ("PROVIDER_MONTHLY_CREDITS", "provider_credits")}


def source() -> str:
    """Who finds people and company data: CONTACTS_PROVIDER if set, else LeadMagic when its key is set, else Prospeo."""
    want = os.environ.get("CONTACTS_PROVIDER", "").strip().lower()
    if want in LABEL:
        return want
    return "leadmagic" if leadmagic.enabled() else "prospeo"


def provider_enabled() -> bool:
    return (leadmagic if source() == "leadmagic" else prospeo).enabled()


def _credits_field() -> str:
    return CAPS[source()][1]


def _per_company_most() -> int:
    """The most one company's search can cost: Prospeo 1 per search; LeadMagic 1 per person returned (up to the page
    size) plus, when the website finds nobody, 1 for that empty search and 1 for the company lookup (included on
    Pro / Ultimate plans)."""
    return leadmagic.people_limit() + 2 if source() == "leadmagic" else 1


def _balance() -> str:
    if source() == "leadmagic":
        try:
            left = leadmagic.credits_left()
            return f"LeadMagic: {left:g} credits left" if left is not None else "LeadMagic balance unknown"
        except leadmagic.LeadMagicError as e:
            return f"LeadMagic balance unknown ({e})"
    try:
        a = prospeo.account()
        return f"Prospeo plan {a.get('current_plan')}, {a.get('remaining_credits')} credits left"
    except prospeo.ProspeoError as e:
        return f"Prospeo balance unknown ({e})"


def status() -> dict:
    """Which provider finds people and company data, then each provider's key, credits left, credits our lists used
    this month and the monthly cap. Free."""
    return {"provider": LABEL[source()], "leadmagic": leadmagic_status(), "prospeo": prospeo_status()}


def prospeo_status() -> dict:
    cap = os.environ.get("PROVIDER_MONTHLY_CREDITS", "").strip()
    out = {"key_set": prospeo.enabled(), "credits_used_this_month": _credits_this_month(),
           "monthly_cap": float(cap) if cap.replace(".", "", 1).isdigit() else None}
    if out["key_set"]:
        try:
            a = prospeo.account()
            out.update(plan=a.get("current_plan"), credits_left=a.get("remaining_credits"))
        except prospeo.ProspeoError as e:
            out["balance_error"] = str(e)
    return out


def leadmagic_status() -> dict:
    """LeadMagic key, credits left, credits used this month by our lists, and the monthly cap. Free."""
    cap = os.environ.get("LEADMAGIC_MONTHLY_CREDITS", "").strip()
    out = {"key_set": leadmagic.enabled(), "credits_used_this_month": _credits_this_month("leadmagic_credits"),
           "monthly_cap": float(cap) if cap.replace(".", "", 1).isdigit() else None}
    if out["key_set"]:
        try:
            out["credits_left"] = leadmagic.credits_left()
        except leadmagic.LeadMagicError as e:
            out["balance_error"] = str(e)
    return out


def read_contacts(folder: Path, domain: str = "", decision_makers_only: bool = False) -> list[dict]:
    """The contacts file (rewritten first, so it is current), optionally one company or decision-makers only."""
    path = write_contacts(folder, Store(folder))
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if (not domain or r["Domain"] == domain) and (not decision_makers_only or r["Decision maker"])]


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
    """Companies whose data points the provider covers are still open, and that have no provider data yet."""
    out = []
    for row in rows:
        d = row["domain"]
        r = store.get_result(d, phase) or {}
        icp = store.get_result(d, "icp") or {}
        if not r or icp.get("icp_verdict") in OUT_OF_RUNNING or has_bought(store, d):
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


def _need_key() -> None:
    if not provider_enabled():
        key = "LEADMAGIC_API_KEY" if source() == "leadmagic" else "PROSPEO_API_KEY"
        raise SystemExit(f"Set {key} first: python -m enrich settings --set {key}=...")


def company_plan(folder: Path, phase: str, limit: int = 0) -> dict:
    """Which companies the company step would send to the provider, and the most it could cost. Spends nothing."""
    store = Store(folder)
    rows, _ = load_leads(folder / "input.csv")
    domains = pick_company(store, rows, phase)[: limit or None]
    return {"provider": LABEL[source()], "domains": domains, "credits_at_most": len(domains) * _per_company_most(),
            "covers": [SIGNALS[s][1] for s in COVERS[phase]]}


def company_step(folder: Path, phase: str, limit: int = 0, assume_yes: bool = False, max_credits: float | None = None) -> dict:
    _need_key()
    store = Store(folder)
    rows, _ = load_leads(folder / "input.csv")
    plan = company_plan(folder, phase, limit)
    domains = plan["domains"]
    if not domains:
        console.print("No company needs provider data: the earlier layers answered what the provider covers.")
        return {"companies": 0, "credits": 0}
    covered, name = ", ".join(plan["covers"]), LABEL[source()]
    refused = _refuse(plan["credits_at_most"], max_credits, "Use a limit or raise max_credits.")
    if refused:
        return refused
    if not _confirm(f"[bold]{name}[/] for {len(domains)} companies still open on: {covered}.\n"
                    f"  At most {plan['credits_at_most']:g} credits: one team search per company gives the company record "
                    f"and its people (no match and repeats within 90 days are free). {_balance()}.", assume_yes):
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
    log(folder, f"Provider: {name} company data and people", companies=len(domains), matched=matched,
        errors=len(errors), contacts_file=rel(contacts), **{_credits_field(): spent})
    console.print(f"{name}: {matched} of {len(domains)} companies matched, {spent:g} credits."
                  + (f" [red]{len(errors)} errors[/], first: {errors[0]}" if errors else ""))
    return {"companies": len(domains), "matched": matched, "credits": spent, "errors": errors[:3]}


def _people(domain: str, finance_only: bool = False) -> tuple[list[dict], float, dict | None]:
    """One search: the company's whole team (no seniority filter), or for a large company (ENT) only its finance
    department (finance, accounting, tax, treasury, FP&A), since a 25-person page of a big team could miss them.
    The company record comes with the results, so no separate company call is needed. LeadMagic first (Prospeo: 1
    credit if anyone is found; LeadMagic: 1 per person returned unless the plan includes search)."""
    if source() == "leadmagic":
        people, credits, _, company = leadmagic.search_people(domain, finance_only)
        return sorted(people, key=_rank), credits, company
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
    return sorted(people, key=_rank), credits, company


def _rank(p: dict) -> int:  # the buyers first: founder, owner or CEO; then the finance person; then COO, chief of staff, operations
    t = (p.get("title") or "").lower()
    return (0 if re.search(r"founder|ceo|chief executive|\bowner\b|\bpresident\b", t) else 1 if RE_FIN_PERSON.search(t) else
            2 if re.search(r"\bcoo\b|chief operating|chief of staff|operations", t) else 3)


def fetch_one(domain: str, seg: str | None = None) -> dict:
    """One search per company. Size decides it: ENT -> its finance department; otherwise the whole team. Only when a
    whole-team search finds nobody (free) is the company record looked up on its own (1 credit if matched)."""
    finance_only, src = seg == "ENT", source()
    people, credits, company = _people(domain, finance_only)
    if src == "leadmagic" and not people:
        # LeadMagic files people under the domain its data has; the company record links its LinkedIn page
        company, c2, _ = leadmagic.company(domain)
        credits += c2
        if (company or {}).get("linkedin_url"):
            found, c3, _, _ = leadmagic.search_people(domain, finance_only, company["linkedin_url"])
            people, credits = sorted(found, key=_rank), credits + c3
    elif not people and not finance_only:
        company, c2, _ = prospeo.company(domain)
        credits += c2
    return {"people": people, "company": company, "credits": credits, "provider": src,
            "search": "finance department" if finance_only else "whole team"}


def save_one(store: Store, domain: str, got: dict, chosen: int = 3) -> None:
    src = got.get("provider", "prospeo")
    page = leadmagic.people_limit() if src == "leadmagic" else 25
    store.save_provider(domain, src, "people", people_record(got["people"][:chosen], got["people"],
                                                             got.get("search", "whole team"), page))
    store.save_provider(domain, src, "company", got["company"] or {"no_match": True})


def size_of(store: Store, domain: str) -> str | None:
    """The segment the rules worked out from proof (list count or provider), None if not known yet."""
    for phase in ("signals", "icp"):
        seg = ((store.get_result(domain, phase) or {}).get("facts") or {}).get("segment")
        if seg:
            return seg
    return None


def finance_open(store: Store, domain: str) -> list[str]:
    """Data points using the provider (the finance check) still open after steps 1-4 that can score for this size."""
    from ..checks.rules import unscored
    res = store.get_result(domain, "signals") or {}
    seg = size_of(store, domain)
    skip = set(unscored(seg)) if seg else set()
    return [sid for sid in COVERS["signals"] if sid not in skip
            and res.get("signals", {}).get(sid, {}).get("value") in (UNKNOWN, CHECK)]


def step5_one(folder: Path, store: Store, domain: str) -> dict:
    """Step 5 for one company (MCP and API): the provider's finance check (LeadMagic, else Prospeo), only when it's
    needed and allowed. One search; never a second one for the same company."""
    if not provider_enabled():
        return {"used": False, "why": f"{'LEADMAGIC' if source() == 'leadmagic' else 'PROSPEO'}_API_KEY is not set"}
    icp = (store.get_result(domain, "icp") or {}).get("icp_verdict")
    if not icp or icp in OUT_OF_RUNNING:
        return {"used": False, "why": f"not checked: ICP verdict is {icp or 'missing'}"}
    if has_bought(store, domain):
        return {"used": False, "why": "already used its one provider search (data reused)"}
    open_ = finance_open(store, domain)
    if not open_:
        return {"used": False, "why": "the finance check is already answered (or can't score for this size)"}
    over = _cap_check(_per_company_most(), *CAPS[source()])
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
    src = got.get("provider", "prospeo")
    log(folder, f"Provider: {LABEL[src]} finance check (MCP)", companies=1,
        matched=bool(got["company"]), people=len(got["people"]), **{CAPS[src][1]: got["credits"]})
    return {"used": True, "provider": LABEL[src], "for": open_, "search": got["search"], "credits": got["credits"],
            "people_found": len(got["people"]), "contacts_file": str(contacts)}


def has_bought(store: Store, domain: str) -> bool:
    """This company already had its one provider search (company record or team search, LeadMagic or Prospeo)."""
    return bool(bought(store.get_provider(domain))[0])


has_prospeo = has_bought  # the old name


def bought(providers: dict) -> tuple[str, dict]:
    """(provider, its people and company data) for a company: LeadMagic's if it has any, else Prospeo's."""
    from .apply import pick
    return pick(providers)


def people_record(chosen: list[dict], people: list[dict], search: str = "whole team", page: int = 25) -> dict:
    """What is saved per company: the contacts kept, finance people, and everyone found with names and titles.
    team_searched is only true for a whole-team search: only that can show nobody works in finance (F13 / F14 No)."""
    finance = people if search == "finance department" else [p for p in people if RE_FIN_PERSON.search(p["title"] or "")]
    return {"list": chosen, "finance": finance[:3], "search": search,
            "found": len(people), "team_searched": search == "whole team", "page": page,
            "team": [{k: p.get(k) for k in ("name", "title", "linkedin", "location", "person_id")} for p in people]}


def people_plan(folder: Path, emails: bool = False, per_company: int = 3, strong_only: bool = False,
                limit: int = 0) -> dict:
    """Which ICP companies the people step would search, and the most it could cost. Spends nothing."""
    store = Store(folder)
    rows, _ = load_leads(folder / "input.csv")
    keep = {r["_domain"] for r in final.read_rows(folder) if r.get("ICP") == "YES"
            and (not strong_only or (r.get("Lead tier") or "").startswith("Strong"))}
    candidates = [d for d in dict.fromkeys(r["domain"] for r in rows) if d in keep and finance_open(store, d)]
    used = [d for d in candidates if has_bought(store, d)]  # already searched once: never a second search
    domains = [d for d in candidates if d not in used][: limit or None]
    return {"provider": LABEL[source()], "domains": domains, "already_have_prospeo": used, "already_searched": used,
            "credits_at_most": len(domains) * _per_company_most() + (len(domains) * per_company if emails else 0)}


def people_step(folder: Path, emails: bool = False, per_company: int = 3, strong_only: bool = False,
                limit: int = 0, assume_yes: bool = False, max_credits: float | None = None) -> dict:
    _need_key()
    store = Store(folder)
    rows, _ = load_leads(folder / "input.csv")
    plan = people_plan(folder, emails, per_company, strong_only, limit)
    domains, used = plan["domains"], plan["already_have_prospeo"]
    if used:
        console.print(f"[dim]{len(used)} companies already have provider data (searched once already); "
                      f"their contacts are in the contacts file.[/]")
    if not domains:
        console.print("No ICP company is waiting for contacts.")
        write_contacts(folder, store)
        return {"companies": 0, "credits": 0}
    refused = _refuse(plan["credits_at_most"], max_credits, "Use a limit, fewer contacts, or raise max_credits.")
    if refused:
        return refused
    src = source()
    if not _confirm(f"[bold]{LABEL[src]} people[/] for {len(domains)} ICP companies{' (Strong fit only)' if strong_only else ''}: "
                    f"founders, C-suite, finance and other leads, top {per_company} kept per company.\n"
                    f"  At most {len(domains) * _per_company_most()} credits for the searches"
                    + (f", plus up to {len(domains) * per_company} for verified emails" if emails else " (emails not looked up; add --emails)")
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
                try:
                    if got.get("provider") == "leadmagic":
                        em, c, _ = leadmagic.find_email(p.get("name") or "", d, p.get("linkedin") or "")
                        p["email"], p["email_status"] = (em or {}).get("email"), (em or {}).get("status")
                    elif p.get("person_id"):
                        person, c, _ = prospeo.reveal_email(p["person_id"])
                        em = (person or {}).get("email") or {}
                        p["email"], p["email_status"] = em.get("email"), em.get("status")
                    else:
                        continue
                except (prospeo.ProspeoError, leadmagic.LeadMagicError) as e:
                    errors.append(str(e))
                    continue
                spent += c
        found_total += len(people)
        save_one(store, d, got, per_company)
    _rewrite(folder, store, rows, set(domains))
    path = final.write(folder, store)
    contacts = write_contacts(folder, store)
    log(folder, f"Provider: {LABEL[src]} people", companies=len(domains), contacts=found_total,
        emails=emails, errors=len(errors), **{CAPS[src][1]: spent})
    console.print(f"{LABEL[src]} people: {found_total} contacts at {len(domains)} companies, {spent:g} credits. "
                  f"Contacts: {rel(contacts)}; final list: {rel(path)}"
                  + (f" [red]{len(errors)} errors[/], first: {errors[0]}" if errors else ""))
    return {"companies": len(domains), "contacts": found_total, "credits": spent, "errors": errors[:3], "file": str(contacts)}


def _needs_email(store: Store, domain: str, per_company: int, decision_makers_only: bool, phones: bool) -> list[dict]:
    """The company's top contacts (ranked: founder or CEO, finance, operations) still missing what was asked:
    an email (from Prospeo or LeadMagic), or with phones=True a mobile number. People LeadMagic already looked up
    are skipped, found or not, so nobody is paid for twice."""
    prov = store.get_provider(domain)
    ppl = bought(prov)[1].get("people") or {}
    have = {p.get("person_id"): p for p in ppl.get("list") or [] if p.get("email")}
    done = (prov.get("leadmagic") or {}).get("contacts") or {}
    team = [p for p in (ppl.get("team") or ppl.get("list") or []) if p.get("name") or p.get("linkedin")]
    if decision_makers_only:
        team = [p for p in team if RE_DECISION.search(p.get("title") or "")]
    out = []
    for p in team[:per_company]:
        k, seen = person_key(p), done.get(person_key(p)) or {}
        email = not have.get(p.get("person_id")) and not seen.get("email_checked")
        mobile = phones and bool(p.get("linkedin")) and not seen.get("mobile_checked")
        if email or mobile:
            out.append({**p, "_key": k, "_email": email, "_mobile": mobile})
    return out


def emails_plan(folder: Path, per_company: int = 3, decision_makers_only: bool = False, phones: bool = False,
                limit: int = 0) -> dict:
    """Which contacts LeadMagic would look up and the most it could cost. Spends nothing. Only contacts the people step already
    found at ICP YES companies (run the people step first)."""
    store = Store(folder)
    keep = [r["_domain"] for r in final.read_rows(folder) if r.get("ICP") == "YES"] if (folder / "output").exists() else []
    todo = {d: ppl for d in dict.fromkeys(keep) if (ppl := _needs_email(store, d, per_company, decision_makers_only, phones))}
    domains = list(todo)[: limit or None]
    people = [p for d in domains for p in todo[d]]
    n_email, n_mobile = sum(p["_email"] for p in people), sum(p["_mobile"] for p in people)
    no_people = [d for d in dict.fromkeys(keep) if not (bought(store.get_provider(d))[1].get("people") or {}).get("team")]
    return {"domains": domains, "people": len(people), "emails_to_find": n_email, "mobiles_to_find": n_mobile,
            "credits_at_most": n_email * leadmagic.EMAIL_CREDITS + n_mobile * leadmagic.MOBILE_CREDITS,
            "companies_without_contacts": len(no_people)}


def emails_step(folder: Path, per_company: int = 3, decision_makers_only: bool = False, phones: bool = False,
                limit: int = 0, assume_yes: bool = False, max_credits: float | None = None) -> dict:
    """LeadMagic for the contacts found without an email: 1 credit per valid email found (none found is free),
    and with phones=True 5 credits per mobile found. Asks first; refused above max_credits or the monthly cap."""
    if not leadmagic.enabled():
        raise SystemExit("Set LEADMAGIC_API_KEY first: python -m enrich settings --set LEADMAGIC_API_KEY=...")
    store = Store(folder)
    plan = emails_plan(folder, per_company, decision_makers_only, phones, limit)
    if not plan["people"]:
        console.print("No contact is waiting for an email" + (" or mobile" if phones else "")
                      + (f" ({plan['companies_without_contacts']} ICP companies have no contacts yet: run the people step first)."
                         if plan["companies_without_contacts"] else "."))
        write_contacts(folder, store)
        return {"people": 0, "credits": 0}
    most = plan["credits_at_most"]
    if max_credits is not None and most > max_credits:
        return {"people": 0, "credits": 0, "refused": f"Could use up to {most:g} LeadMagic credits, above max_credits "
                                                      f"{max_credits:g}. Nothing was sent. Use a limit or raise max_credits."}
    over = _cap_check(most, "LEADMAGIC_MONTHLY_CREDITS", "leadmagic_credits")
    if over:
        console.print(f"[red]{over}[/]")
        return {"people": 0, "credits": 0, "refused": over}
    try:
        left = leadmagic.credits_left()
        balance = f"LeadMagic: {left:g} credits left" if left is not None else "LeadMagic balance unknown"
    except leadmagic.LeadMagicError as e:
        balance = f"LeadMagic balance unknown ({e})"
    if not _confirm(f"[bold]LeadMagic[/] for {plan['people']} contacts at {len(plan['domains'])} ICP companies: "
                    f"{plan['emails_to_find']} emails (1 credit each found, free if none)"
                    + (f", {plan['mobiles_to_find']} mobiles (5 credits each found)" if phones else "")
                    + f". At most {most:g} credits. {balance}.", assume_yes):
        return {"people": 0, "credits": 0}

    def one(d: str) -> tuple[dict, float, list[str]]:
        saved = dict((store.get_provider(d).get("leadmagic") or {}).get("contacts") or {})
        spent, errs = 0.0, []
        for p in _needs_email(store, d, per_company, decision_makers_only, phones):
            rec = dict(saved.get(p["_key"]) or {}, name=p.get("name"))
            try:
                if p["_email"]:
                    got, c, _ = leadmagic.find_email(p.get("name") or "", d, p.get("linkedin") or "")
                    spent += c
                    rec.update(email_checked=True, **({"email": got["email"], "status": got["status"]} if got else {}))
                if p["_mobile"]:
                    mob, c, _ = leadmagic.find_mobile(p.get("linkedin") or "")
                    spent += c
                    rec.update(mobile_checked=True, **({"mobile": mob} if mob else {}))
            except leadmagic.LeadMagicError as e:
                errs.append(str(e))
                if "no credits" in str(e):
                    break
            saved[p["_key"]] = rec
        return saved, spent, errs

    spent, emails_found, mobiles_found, errors = 0.0, 0, 0, []
    for item in asyncio.run(_run_many(one, plan["domains"], workers=2)):
        if isinstance(item, Exception):
            errors.append(str(item))
            continue
        d, (saved, c, errs) = item
        spent += c
        errors += errs
        store.save_provider(d, "leadmagic", "contacts", saved)
    for d in plan["domains"]:
        for rec in ((store.get_provider(d).get("leadmagic") or {}).get("contacts") or {}).values():
            emails_found += bool(rec.get("email"))
            mobiles_found += bool(rec.get("mobile"))
    contacts = write_contacts(folder, store)
    log(folder, "Provider: LeadMagic emails", companies=len(plan["domains"]), people=plan["people"],
        emails_found=emails_found, mobiles_found=mobiles_found, leadmagic_credits=spent, errors=len(errors))
    console.print(f"LeadMagic: {emails_found} emails" + (f", {mobiles_found} mobiles" if phones else "")
                  + f" for {plan['people']} contacts, {spent:g} credits. Contacts: {rel(contacts)}"
                  + (f" [red]{len(errors)} errors[/], first: {errors[0]}" if errors else ""))
    return {"companies": len(plan["domains"]), "people": plan["people"], "emails_found": emails_found,
            "mobiles_found": mobiles_found, "credits": spent, "errors": errors[:3], "file": str(contacts)}


CONTACT_FIELDS = ["Company", "Domain", "Website", "ICP", "Lead score", "Lead tier", "Industry", "Employees",
                  "HQ", "Founded", "Total funding", "Latest round", "Latest round date", "Company LinkedIn",
                  "Name", "Title", "Decision maker", "Person LinkedIn", "Person location", "Email", "Email status",
                  "Email source", "Mobile"]
RE_DECISION = re.compile(r"founder|ceo|chief|president|owner|coo|cfo|head of finance|vp,?( of)? finance|vice president,?( of)? finance|controller", re.I)


def person_key(p: dict) -> str:
    return p.get("person_id") or p.get("linkedin") or (p.get("name") or "").lower()


def write_contacts(folder: Path, store: Store) -> Path:
    """Every person the provider found (LeadMagic or Prospeo), one row each, with the company's details and its ICP, score and tier.
    output/<list>_contacts.csv, apart from the results file."""
    out = folder / "output" / f"{folder.name}_contacts.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    scored = {r["_domain"]: r for r in final.read_rows(folder)} if (folder / "output").exists() else {}
    names = {}
    if (folder / "input.csv").exists():
        names = {r["domain"]: r["company"] for r in load_leads(folder / "input.csv")[0]}
    rows = []
    paid = [r[0] for r in store.db.execute("SELECT DISTINCT domain FROM providers WHERE provider IN ('prospeo', 'leadmagic')")]
    for d in dict.fromkeys([*names, *store.all_domains(), *paid]):
        src, data = bought(store.get_provider(d))
        ppl = data.get("people") or {}
        team = ppl.get("team") or ppl.get("list") or []
        if not team:
            continue
        c = data.get("company") if not (data.get("company") or {}).get("no_match") else {}
        c = c or {}
        loc, fund = c.get("location") or {}, c.get("funding") or {}
        emails = {p.get("person_id"): p for p in ppl.get("list") or []}
        lm = store.get_provider(d).get("leadmagic", {}).get("contacts") or {}
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
            got = lm.get(person_key(p)) or {}
            email, status, came = em.get("email"), em.get("email_status"), LABEL.get(src, "") if em.get("email") else ""
            if not email and got.get("email"):
                email, status, came = got["email"], got.get("status"), "LeadMagic"
            rows.append({**company, "Name": p.get("name"), "Title": title,
                         "Decision maker": "yes" if RE_DECISION.search(title) else "",
                         "Person LinkedIn": p.get("linkedin") or "", "Person location": p.get("location") or "",
                         "Email": email or "", "Email status": status or "", "Email source": came,
                         "Mobile": got.get("mobile") or ""})
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CONTACT_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return out


def show_people(store: Store, domain: str) -> Table | None:
    people = (bought(store.get_provider(domain))[1].get("people") or {}).get("list") or []
    if not people:
        return None
    t = Table(header_style="bold")
    for c in ("Name", "Title", "LinkedIn", "Email"):
        t.add_column(c, overflow="fold")
    for p in people:
        t.add_row(p.get("name") or "", p.get("title") or "", p.get("linkedin") or "", p.get("email") or "")
    return t

