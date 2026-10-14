"""Prospeo (prospeo.io): company data and people. Key: PROSPEO_API_KEY in .env (header X-KEY).

    company(domain)          POST /enrich-company   1 credit per match (free if no match or re-enriched within 90 days)
                             headcount, HQ location, type (private / public / non profit), funding (latest stage,
                             date, rounds), technologies, active job postings, industry
    search_people(domain)    POST /search-person    1 credit per search that finds someone (25 per page)
                             names, titles, seniority, departments, LinkedIn; emails masked
    reveal_email(person_id)  POST /enrich-person    1 credit per verified email found
    account()                GET  /account-information   free: plan and remaining credits
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

import httpx

from ..core import ratelimit
from ..core.paths import CACHE

BASE = "https://api.prospeo.io"
CACHE_DB = CACHE / "providers.sqlite"
PER_SECOND = 2


class ProspeoError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(os.environ.get("PROSPEO_API_KEY", "").strip())


def _cache_days() -> float:
    try:
        return float(os.environ.get("PROVIDER_CACHE_DAYS") or 90)
    except ValueError:
        return 90.0


def _db() -> sqlite3.Connection:
    CACHE.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(CACHE_DB, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS calls (key TEXT PRIMARY KEY, data TEXT, credits REAL, created REAL)")
    return db


def _call(path: str, body: dict | None, cache_key: str | None) -> tuple[dict, float, bool]:
    """(response, credits spent, from cache). 'No match' answers are cached too (they're free, and stay free)."""
    if cache_key:
        db = _db()
        row = db.execute("SELECT data, created FROM calls WHERE key = ?", (cache_key,)).fetchone()
        db.close()
        if row and time.time() - row[1] < _cache_days() * 86400:
            return json.loads(row[0]), 0.0, True
    key = os.environ.get("PROSPEO_API_KEY", "").strip()
    if not key:
        raise ProspeoError("PROSPEO_API_KEY is not set (python -m enrich settings --set PROSPEO_API_KEY=...)")
    for attempt in range(4):
        ratelimit.wait("prospeo", PER_SECOND)
        with httpx.Client(timeout=60) as c:
            r = (c.post(f"{BASE}{path}", json=body, headers={"X-KEY": key, "Content-Type": "application/json"})
                 if body is not None else c.get(f"{BASE}{path}", headers={"X-KEY": key}))
        if r.status_code == 429 and attempt < 3:
            time.sleep(2 ** attempt * 2)
            continue
        break
    try:
        data = r.json()
    except ValueError:
        raise ProspeoError(f"Prospeo answered {r.status_code}: {r.text[:200]}")
    code = data.get("error_code") or (data.get("message") if data.get("error") else None)
    if data.get("error") and code not in ("NO_MATCH", "NO_RESULTS"):
        raise ProspeoError(f"Prospeo error {r.status_code}: {code or data}")
    no_match = bool(data.get("error"))
    free = no_match or data.get("free_enrichment") or data.get("free")
    credits = 0.0 if free else 1.0
    out = {"no_match": no_match, **({} if no_match else data)}
    if cache_key:
        db = _db()
        db.execute("INSERT OR REPLACE INTO calls VALUES (?, ?, ?, ?)", (cache_key, json.dumps(out), credits, time.time()))
        db.commit()
        db.close()
    return out, credits, False


def account() -> dict:
    data, _, _ = _call("/account-information", None, None)
    return data.get("response", data)


def company(domain: str) -> tuple[dict | None, float, bool]:
    """Prospeo's company record for a website, or None when they have no match."""
    data, credits, cached = _call("/enrich-company", {"data": {"company_website": domain}}, f"company:{domain}")
    return (None if data.get("no_match") else data.get("company")), credits, cached


def search_people(domain: str, seniorities: list[str] | None = None, departments: list[str] | None = None,
                  page: int = 1) -> tuple[list[dict], float, bool]:
    filters: dict = {"company": {"websites": {"include": [domain]}}}  # nested, per the API docs
    if seniorities:
        filters["person_seniority"] = {"include": seniorities}
    if departments:
        filters["person_department"] = {"include": departments}
    key = f"people:{domain}:{','.join(seniorities or [])}:{','.join(departments or [])}:{page}"
    data, credits, cached = _call("/search-person", {"page": page, "filters": filters}, key)
    return ([] if data.get("no_match") else data.get("results", [])), credits, cached


def reveal_email(person_id: str) -> tuple[dict | None, float, bool]:
    data, credits, cached = _call("/enrich-person", {"only_verified_email": True, "data": {"person_id": person_id}},
                                  f"email:{person_id}")
    return (None if data.get("no_match") else data.get("person")), credits, cached
