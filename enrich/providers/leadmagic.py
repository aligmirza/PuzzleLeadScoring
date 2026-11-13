"""LeadMagic (leadmagic.io): Puzzle's people and company data provider, first in line (Prospeo only when there is no
LeadMagic key, or CONTACTS_PROVIDER=prospeo). Key: LEADMAGIC_API_KEY in .env (header X-API-Key).

    search_people(domain, finance_only)           POST /v3/people/search         1 credit per person returned, 1 for an empty
                                                                                 search (included on Pro / Ultimate plans)
    company(domain)                               POST /v3/companies/search      1 credit per company found, free if none
    find_email(name, domain, profile_url)         POST /v1/people/email-finder   1 credit per valid email, free if none
                                                                                 (tested live: valid @bobsled.co, 1 credit)
    find_mobile(profile_url)                      POST /v1/people/mobile-finder  5 credits per mobile, free if none
    account()                                     GET  /v1/credits               free: credits left

LeadMagic files people under the company domain its own data has, which is not always the website on the list (a
search for bobsled.co finds nobody; Bobsled's people are under bobsled.com). So when the website finds nobody, the
company is looked up by its website and its people are searched by its LinkedIn company page, which the company
record links to.

People and company records are turned into the same shape as Prospeo's (providers/apply.py reads either), so the
rules don't care which provider found them. Answers are cached (cache/providers.sqlite): nothing is paid for twice
within PROVIDER_CACHE_DAYS (default 90).
"""
from __future__ import annotations

import json
import os
import time

import httpx

from ..core import ratelimit
from .prospeo import _cache_days, _db

BASE = "https://api.leadmagic.io"
PER_SECOND = 4
EMAIL_CREDITS = 1
MOBILE_CREDITS = 5
URL = "https://leadmagic.io"


def people_limit() -> int:
    """People asked for per company search (LEADMAGIC_PEOPLE_LIMIT, default 25: a page under 25 shows the whole team,
    which the finance check needs). Each person returned costs 1 credit unless the plan includes search."""
    try:
        return max(1, min(50, int(os.environ.get("LEADMAGIC_PEOPLE_LIMIT") or 25)))
    except ValueError:
        return 25


class LeadMagicError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(os.environ.get("LEADMAGIC_API_KEY", "").strip())


def _call(method: str, path: str, body: dict | None, cache_key: str | None) -> tuple[dict, float, bool]:
    """(response, credits spent, from cache). Answers that found nothing are cached too: they were free."""
    if cache_key:
        db = _db()
        row = db.execute("SELECT data, created FROM calls WHERE key = ?", (cache_key,)).fetchone()
        db.close()
        if row and time.time() - row[1] < _cache_days() * 86400:
            return json.loads(row[0]), 0.0, True
    key = os.environ.get("LEADMAGIC_API_KEY", "").strip()
    if not key:
        raise LeadMagicError("LEADMAGIC_API_KEY is not set (python -m enrich settings --set LEADMAGIC_API_KEY=...)")
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    for attempt in range(4):
        ratelimit.wait("leadmagic", PER_SECOND)
        with httpx.Client(timeout=60) as c:
            r = c.post(f"{BASE}{path}", json=body, headers=headers) if method == "POST" else c.get(f"{BASE}{path}", headers=headers)
        if r.status_code == 429 and attempt < 3:  # a 429 costs nothing; wait as asked
            try:
                wait = float(r.headers.get("Retry-After") or 0)
            except ValueError:
                wait = 0
            time.sleep(wait or 2 ** attempt * 2)
            continue
        break
    try:
        data = r.json()
    except ValueError:
        raise LeadMagicError(f"LeadMagic answered {r.status_code}: {r.text[:200]}")
    if r.status_code == 402:
        raise LeadMagicError(f"LeadMagic: no credits ({data.get('code') or data.get('message') or 402})")
    if r.status_code >= 400:
        raise LeadMagicError(f"LeadMagic error {r.status_code}: {data.get('message') or data.get('error') or data}")
    try:
        credits = float(data.get("credits_consumed") or 0)
    except (TypeError, ValueError):
        credits = 0.0
    if cache_key:
        db = _db()
        db.execute("INSERT OR REPLACE INTO calls VALUES (?, ?, ?, ?)", (cache_key, json.dumps(data), credits, time.time()))
        db.commit()
        db.close()
    return data, credits, False


def account() -> dict:
    data, _, _ = _call("GET", "/v1/credits", None, None)
    return data


def credits_left() -> float | None:
    a = account()
    for k in ("credits", "credits_remaining", "remaining_credits", "balance"):
        if isinstance(a.get(k), (int, float)):
            return float(a[k])
    return None


def split_name(full: str) -> tuple[str, str]:
    parts = (full or "").split()
    return (parts[0], " ".join(parts[1:])) if len(parts) > 1 else ((parts[0], "") if parts else ("", ""))


def find_email(name: str, domain: str, profile_url: str = "") -> tuple[dict | None, float, bool]:
    """{'email', 'status'} for a person at this company, or None. Only an email on the company's own domain counts
    (LeadMagic's default): an address at another company is not this lead's contact."""
    first, last = split_name(name)
    body: dict = {"domain": domain}
    if first and last:
        body.update(first_name=first, last_name=last)
    elif profile_url:
        body["profile_url"] = profile_url
    else:
        return None, 0.0, False
    data, credits, cached = _call("POST", "/v1/people/email-finder", body,
                                  f"lm-email:{domain}:{(first + ' ' + last).lower().strip() or profile_url}")
    email = data.get("email")
    if not email or data.get("domain_mismatch"):
        return None, credits, cached
    return {"email": email, "status": data.get("status") or "", "employment_verified": data.get("employment_verified")}, credits, cached


def find_mobile(profile_url: str = "", work_email: str = "") -> tuple[str | None, float, bool]:
    if not (profile_url or work_email):
        return None, 0.0, False
    body = {"profile_url": profile_url} if profile_url else {"work_email": work_email}
    data, credits, cached = _call("POST", "/v1/people/mobile-finder", body, f"lm-mobile:{profile_url or work_email}")
    return data.get("mobile_number") or None, credits, cached


def _person(p: dict) -> dict:
    name = p.get("contact_full_name") or " ".join(x for x in (p.get("contact_first_name"), p.get("contact_last_name")) if x)
    linkedin = p.get("contact_linkedin_url") or ""
    return {"person_id": linkedin or p.get("person_key") or (name or "").lower(), "name": name, "title": p.get("contact_job_title") or "",
            "linkedin": linkedin, "department": p.get("contact_job_function") or "",
            "location": ", ".join(x for x in (p.get("contact_city"), p.get("contact_state_code"), p.get("contact_country_code")) if x)}


def _company(c: dict) -> dict:
    """LeadMagic's company record in Prospeo's field names (what apply.py and the contacts file read)."""
    n = c.get("linkedin_employee_count") or c.get("employee_min")
    total = c.get("total_funding")
    return {"name": c.get("company_name"), "website": c.get("company_website") or c.get("company_domain"),
            "domain": c.get("company_domain"), "industry": c.get("company_industry_linkedin"),
            "employee_count": int(n) if isinstance(n, (int, float)) and n else None,
            "employee_range": c.get("employee_range"),
            "location": {"city": c.get("hq_city") or c.get("location_city"), "state": c.get("hq_state") or c.get("location_state_code"),
                         "country": c.get("hq_country"), "country_code": c.get("hq_country_code") or c.get("location_country_code")},
            "founded": c.get("founded_year"),
            "funding": {"total_funding_printed": f"${total:,.0f}" if isinstance(total, (int, float)) and total else "",
                        "latest_funding_stage": c.get("last_funding_type") or "",
                        "latest_funding_date": str(c.get("last_funding_date") or "")},
            "linkedin_url": c.get("linkedin_url"), "source": "LeadMagic"}


def search_people(domain: str, finance_only: bool = False, linkedin_url: str = "") -> tuple[list[dict], float, bool, dict | None]:
    """(people, credits, cached, company record): the company's team, or only its finance department, by its website or
    (linkedin_url) by its LinkedIn company page. The company record comes with a website search (include_company)."""
    body: dict = {"linkedin_url": linkedin_url} if linkedin_url else {"company_domain": domain, "include_company": True}
    body["limit"] = people_limit()
    if finance_only:
        body["people_filters"] = {"contact_job_function": ["Finance"]}
    data, credits, cached = _call("POST", "/v3/people/search", body,
                                  f"lm-people:{linkedin_url or domain}:{'finance' if finance_only else 'team'}:{body['limit']}")
    found = [p for p in data.get("people") or []  # by LinkedIn page: that page is the match, whatever domain LeadMagic files
             if linkedin_url or (p.get("company_domain") or domain).lower().removeprefix("www.") == domain]
    company = next((_company(p["company"]) for p in found if isinstance(p.get("company"), dict) and p["company"]), None)
    return [_person(p) for p in found], credits, cached, company


def company(domain: str) -> tuple[dict | None, float, bool]:
    data, credits, cached = _call("POST", "/v3/companies/search", {"company_domain": domain}, f"lm-company:{domain}")
    c = data.get("company") if data.get("found", bool(data.get("company"))) else None
    return (_company(c) if c else None), credits, cached
