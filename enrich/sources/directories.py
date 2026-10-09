"""Free official directories: YC, SEC (EDGAR) and the IRS non-profit list. No account, no cost.

Runs after the rules and before AI, for every company:

  YC directory   matched by website domain     YC backing, batch, YC status (public, acquired), HQ location,
                                                non-profit flag, team size
  SEC EDGAR      matched by legal name          stock ticker (public company), Form D filings (raised private
                                                funding), state of incorporation (Delaware), business address
  IRS Pub 78     matched by legal name + state  listed as a tax-exempt organisation (non-profit)

Quality rules (same spirit as the AI confidence rules):
  - a match is STRONG when it's tied to the company's own website: YC by domain, SEC/IRS by the legal name found on
    the company's site (IRS also needs the same state, or a single match in the country)
  - MEDIUM: the list's company name plus the same city/state as the site's address
  - WEAK: the list's company name only. Never proof. For exclusions it becomes "Needs check" for the AI step
  - an exclusion "yes" or a must-have "no" needs a STRONG match; other answers need STRONG or MEDIUM
  - directories only fill what the rules left open (Needs check / Unknown), with one exception: an IRS listing
    overrules the weak "it has Inc. in its name, so it's for-profit" guess (many non-profits are Inc.)

Downloads are shared by all lists and kept in cache/ (git-ignored). Each company's findings are saved in its list's
database, so re-running the rules never looks them up again.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import time
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import quote

import httpx

from ..core.inputs import normalize_domain
from ..checks.rules import CHECK, NO, SIGNALS, UNKNOWN, US_STATES, YES, sig

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache"
ENABLED = True  # switched off with --no-directories
VERSION = 3  # raise when the matching rules change, so saved lookups are redone

YC_URL = "https://yc-oss.github.io/api/companies/all.json"  # daily copy of ycombinator.com/companies
YC_FILE = CACHE / "yc_companies.json"
YC_MAX_AGE = 7  # days
IRS_URL = "https://apps.irs.gov/pub/epostcard/data-download-pub78.zip"
IRS_FILE = CACHE / "irs_nonprofits.sqlite"
IRS_MAX_AGE = 35  # the IRS updates Pub 78 monthly
SEC_SEARCH = "https://efts.sec.gov/LATEST/search-index?keysTyped={q}"
SEC_COMPANY = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_DOC = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/primary_doc.xml"  # needs SEC_CONTACT_EMAIL
SEC_FILE = CACHE / "sec.sqlite"
SEC_MAX_AGE = 30
SEC_PER_SECOND = 8  # SEC allows 10

EXCHANGES = {"NYSE", "Nasdaq", "NASDAQ", "NYSE American", "NYSE Arca", "CBOE", "BATS"}
NAME_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "llc", "lc", "ltd", "limited", "pbc", "plc", "lp",
                 "llp", "org", "the"}
ORDER = ["YC", "SEC", "IRS"]

_lock = threading.Lock()
_yc: dict | None = None
_sec_last = 0.0
_sec_lock = threading.Lock()
_db_lock = threading.Lock()


def name_key(name: str) -> str:
    """'HubSpot, Inc.' -> 'hubspot'; 'The Wikimedia Foundation Org' -> 'wikimedia foundation'."""
    s = re.sub(r"\(.*?\)", " ", (name or "").lower()).replace("&", " and ")
    words = re.sub(r"[^a-z0-9]+", " ", s).split()
    while words and words[0] == "the":
        words = words[1:]
    while words and words[-1] in NAME_SUFFIXES:
        words = words[:-1]
    return " ".join(words)


def _old(path: Path, days: int) -> bool:
    return not path.exists() or time.time() - path.stat().st_mtime > days * 86400


def _http() -> httpx.Client:
    email = os.environ.get("SEC_CONTACT_EMAIL", "").strip()
    agent = f"PuzzleLeadScoring {email}" if email else "PuzzleLeadScoring research tool"
    return httpx.Client(headers={"User-Agent": agent}, timeout=60, follow_redirects=True)


# YC ---------------------------------------------------------------------
def _yc_index(refresh: bool = False) -> dict:
    global _yc
    with _lock:
        if _yc is not None and not refresh:
            return _yc
        CACHE.mkdir(exist_ok=True)
        if refresh or _old(YC_FILE, YC_MAX_AGE):
            try:
                with _http() as c:
                    data = c.get(YC_URL).raise_for_status().json()
                slim = {}
                for x in data:
                    d = normalize_domain(x.get("website") or "")
                    if d:
                        slim[d] = {k: x.get(k) for k in ("name", "slug", "batch", "status", "stage", "team_size",
                                                         "all_locations", "regions", "nonprofit", "former_names")}
                YC_FILE.write_text(json.dumps(slim))
            except (httpx.HTTPError, ValueError):
                if not YC_FILE.exists():
                    raise
        _yc = json.loads(YC_FILE.read_text())
        return _yc


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name_key(s))


def yc_lookup(domain: str, names: list[str]) -> dict | None:
    """By domain. Strong only if the YC entry's current name matches the company and it isn't marked Acquired:
    an acquired company's YC page sometimes points to a later company's domain (Paribus -> ramp.com)."""
    hit = _yc_index().get(domain)
    if not hit:
        return None
    ours = {_compact(n) for n in names if n} | {_compact(domain.split(".")[0])}
    theirs = _compact(hit.get("name") or "")
    same = bool(theirs) and any(len(o) >= 3 and (o == theirs or o.startswith(theirs) or theirs.startswith(o)) for o in ours)
    if hit.get("status") == "Acquired" or not same:
        return {**hit, "match": "weak", "basis": "website domain, but a different or acquired company"}
    return {**hit, "match": "strong", "basis": "website domain"}


# IRS --------------------------------------------------------------------
def _name_hash(key: str) -> int:
    """64-bit fingerprint of a normalised name. Keeps the IRS index at about 30 MB instead of 180 MB."""
    return int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "big", signed=True)


def _irs_build() -> None:
    CACHE.mkdir(exist_ok=True)
    with _http() as c:
        blob = c.get(IRS_URL).raise_for_status().content
    tmp = IRS_FILE.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.execute("CREATE TABLE orgs (h INTEGER, ein INTEGER, state TEXT, PRIMARY KEY (h, ein)) WITHOUT ROWID")
    with zipfile.ZipFile(io.BytesIO(blob)) as z, z.open(z.namelist()[0]) as f:
        rows = []
        for line in io.TextIOWrapper(f, encoding="utf-8", errors="replace"):
            parts = line.rstrip("\n").split("|")  # EIN|name|city|state|country|deductibility code
            if len(parts) >= 6 and parts[0].strip().isdigit() and parts[1].strip():
                rows.append((_name_hash(name_key(parts[1])), int(parts[0]), parts[3]))
        db.executemany("INSERT OR IGNORE INTO orgs VALUES (?, ?, ?)", rows)
    db.commit()
    db.execute("VACUUM")
    db.close()
    tmp.replace(IRS_FILE)


def _irs_db(refresh: bool = False) -> sqlite3.Connection:
    with _lock:
        if refresh or _old(IRS_FILE, IRS_MAX_AGE):
            try:
                _irs_build()
            except (httpx.HTTPError, zipfile.BadZipFile, OSError):
                if not IRS_FILE.exists():
                    raise
    return sqlite3.connect(f"file:{IRS_FILE}?mode=ro", uri=True, check_same_thread=False)


def irs_lookup(names: list[tuple[str, str]], states: set[str]) -> dict | None:
    """names: [(name, 'legal name' | 'company name')]. Returns the best match with its strength, or None."""
    db = _irs_db()
    try:
        for name, basis in names:
            key = name_key(name)
            if len(key) < 4:
                continue
            hits = db.execute("SELECT ein, state FROM orgs WHERE h = ?", (_name_hash(key),)).fetchall()
            if not hits:
                continue
            same_state = [h for h in hits if h[1] in states]
            ein, state = same_state[0] if same_state else hits[0]
            if basis == "legal name" and (same_state or len(hits) == 1):
                match = "strong"
            elif same_state:
                match = "medium"
            elif len(hits) == 1 and len(key.split()) >= 2:
                match = "weak"
            else:
                continue  # a common name shared by several unrelated organisations: not a match
            ein = f"{ein:09d}"
            return {"ein": f"{ein[:2]}-{ein[2:]}", "name": name, "state": state, "matches": len(hits),
                    "match": match, "basis": basis}
    finally:
        db.close()
    return None


# SEC --------------------------------------------------------------------
def _sec_cache() -> sqlite3.Connection:
    CACHE.mkdir(exist_ok=True)
    db = sqlite3.connect(SEC_FILE, check_same_thread=False, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS calls (url TEXT PRIMARY KEY, data TEXT, fetched REAL)")
    return db


def _sec_get(url: str) -> dict:
    """GET with a shared cache (30 days) and SEC's rate limit."""
    global _sec_last
    with _db_lock:
        db = _sec_cache()
        row = db.execute("SELECT data, fetched FROM calls WHERE url = ?", (url,)).fetchone()
        db.close()
    if row and time.time() - row[1] < SEC_MAX_AGE * 86400:
        return json.loads(row[0])
    for attempt in range(4):
        with _sec_lock:
            wait = _sec_last + 1 / SEC_PER_SECOND - time.time()
            if wait > 0:
                time.sleep(wait)
            _sec_last = time.time()
        with _http() as c:
            r = c.get(url)
        if r.status_code == 404:
            data = {}
            break
        if r.status_code in (429, 500, 502, 503) and attempt < 3:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        data = r.json()
        break
    if "submissions" in url and data:
        data = _sec_slim(data)
    with _db_lock:
        db = _sec_cache()
        db.execute("INSERT OR REPLACE INTO calls VALUES (?, ?, ?)", (url, json.dumps(data), time.time()))
        db.commit()
        db.close()
    return data


def _sec_slim(d: dict) -> dict:
    recent = d.get("filings", {}).get("recent", {})
    forms = list(zip(recent.get("form", []), recent.get("filingDate", [])))
    form_d_docs = [(dt, acc) for f, dt, acc in zip(recent.get("form", []), recent.get("filingDate", []),
                                                    recent.get("accessionNumber", [])) if f in ("D", "D/A")]
    biz = (d.get("addresses") or {}).get("business") or {}
    return {
        "cik": d.get("cik"), "name": d.get("name"), "tickers": d.get("tickers") or [],
        "exchanges": [e for e in d.get("exchanges") or [] if e], "state_of_incorporation": d.get("stateOfIncorporation"),
        "state_of_incorporation_name": d.get("stateOfIncorporationDescription"), "entity_type": d.get("entityType"),
        "industry": d.get("sicDescription"), "website": d.get("website"), "former_names": [
            {"name": f.get("name"), "until": (f.get("to") or "")[:10]} for f in d.get("formerNames") or []],
        "latest_form_d": max(form_d_docs)[1] if form_d_docs else None,
        "business_city": biz.get("city"), "business_state": biz.get("stateOrCountry"),
        "business_place": biz.get("stateOrCountryDescription"),
        "form_d": sorted({dt for f, dt in forms if f in ("D", "D/A")}, reverse=True),
        "annual_reports": sorted({dt for f, dt in forms if f in ("10-K", "20-F", "40-F")}, reverse=True),
    }


def sec_lookup(domain: str, names: list[tuple[str, str]], site_places: str) -> dict | None:
    for name, basis in names:
        key = name_key(name)
        if len(key) < 3:
            continue
        hits = _sec_get(SEC_SEARCH.format(q=quote(key))).get("hits", {}).get("hits", [])
        ciks = list(dict.fromkeys(h["_id"] for h in hits if name_key(h["_source"].get("entity", "")) == key))
        if not ciks:
            continue
        companies = [c for c in (_sec_get(SEC_COMPANY.format(cik=cik.zfill(10))) for cik in ciks[:3]) if c]
        operating = [c for c in companies if c.get("entity_type") != "other"] or companies
        if not operating:
            continue
        c = operating[0]
        if normalize_domain(c.get("website") or "") == domain:
            match, basis = "strong", "website on SEC record"
        elif len(operating) > 1:
            match = "weak"  # several companies with this name
        elif basis == "legal name":
            match = "strong"
        elif c.get("business_city") and c["business_city"].lower() in site_places:
            match, basis = "medium", "company name and city"
        else:
            match = "weak"
        out = {**c, "match": match, "basis": basis, "same_name_count": len(operating)}
        if match != "weak" and c.get("latest_form_d") and os.environ.get("SEC_CONTACT_EMAIL", "").strip():
            out["form_d_details"] = form_d_details(c["cik"], c["latest_form_d"])
        return out
    return None


def form_d_details(cik: str, accession: str) -> dict:
    """Year of incorporation, date of first sale and amount sold from a Form D. SEC serves these documents only to
    requests that carry a contact email, so this runs only when SEC_CONTACT_EMAIL is set."""
    import xml.etree.ElementTree as ET
    url = SEC_DOC.format(cik=int(cik), acc=accession.replace("-", ""))
    with _db_lock:
        db = _sec_cache()
        row = db.execute("SELECT data FROM calls WHERE url = ?", (url,)).fetchone()
        db.close()
    if row:
        return json.loads(row[0])
    with _sec_lock:
        time.sleep(1 / SEC_PER_SECOND)
    with _http() as c:
        r = c.get(url)
    if r.status_code != 200:
        return {"error": f"http {r.status_code}"}
    root = ET.fromstring(r.content)

    def first(path: str) -> str | None:
        node = root.find(path)
        return node.text.strip() if node is not None and node.text else None
    out = {
        "year_of_incorporation": first(".//primaryIssuer/yearOfInc/value"),
        "incorporated_within_5_years": first(".//primaryIssuer/yearOfInc/withinFiveYears"),
        "date_of_first_sale": first(".//offeringData/typeOfFiling/dateOfFirstSale/value"),
        "total_amount_sold": first(".//offeringData/offeringSalesAmounts/totalAmountSold"),
        "total_offering_amount": first(".//offeringData/offeringSalesAmounts/totalOfferingAmount"),
        "url": url,
    }
    with _db_lock:
        db = _sec_cache()
        db.execute("INSERT OR REPLACE INTO calls VALUES (?, ?, ?)", (url, json.dumps(out), time.time()))
        db.commit()
        db.close()
    return out


# lookup + apply -----------------------------------------------------------
def lookup(domain: str, company: str, legal_name: str | None, us_clues: list[str], skip_sec: bool = False,
           history: dict | None = None) -> dict:
    """history: phase 2 only, which old-site checks are worth doing, e.g. {"legal": True, "pricing": False}."""
    names = ([(legal_name, "legal name")] if legal_name else []) + ([(company, "company name")] if company else [])
    names = [(n, b) for i, (n, b) in enumerate(names) if name_key(n) not in {name_key(x) for x, _ in names[:i]}]
    places = " ".join(us_clues).lower()
    states = {code for code, full in US_STATES.items() if re.search(rf"\b{code}\b|\b{full}\b", " ".join(us_clues))}
    found: dict = {"checked": date.today().isoformat(), "version": VERSION, "legal_name": legal_name, "errors": {}}
    for label, fn in (("YC", lambda: yc_lookup(domain, [company, legal_name or ""])),
                      ("IRS", lambda: irs_lookup(names, states)),
                      ("SEC", lambda: None if skip_sec else sec_lookup(domain, names, places))):
        try:
            found[label] = fn()
        except Exception as e:  # noqa: BLE001  a directory being down must never stop the run
            found["errors"][label] = repr(e)[:200]
    if history and (history.get("legal") or history.get("pricing")):
        try:
            from .history import old_site
            found["Wayback"] = old_site(domain, bool(history.get("legal")), bool(history.get("pricing")))
        except Exception as e:  # noqa: BLE001
            found["errors"]["Wayback"] = repr(e)[:200]
    found["history_asked"] = history or {}
    found["complete"] = not found["errors"]
    return found


def _settle(s: dict, sid: str, value: str, evidence: str, url: str, note: str, match: str) -> bool:
    """Write a directory answer into the signals if the quality rules allow it. Returns True when written."""
    group = SIGNALS[sid][0]
    strict = (group == "exclusion" and value == YES) or (group == "must" and value == NO)
    if value in (YES, NO) and not (match == "strong" or (match == "medium" and not strict)):
        if group != "exclusion" or value != YES:
            return False
        value, note = CHECK, f"possible match only, to confirm: {note}"
    cur = s.get(sid, {"value": UNKNOWN, "evidence": ""})
    overrule_guess = sid == "E5" and value == YES and cur["value"] == NO and cur["evidence"].startswith("for-profit legal entity")
    if cur["value"] in (YES, NO) and value in (YES, NO) and value != cur["value"] and match == "strong" and not overrule_guess:
        # the website and an official directory disagree: neither is confirmed, so the AI step (or a person) decides
        s[sid] = sig(CHECK, f"website: {cur['evidence']} | directory: {evidence}", url,
                     f"website said {cur['value']}, directory said {value} ({note})", method="directory")
        if sid == "M1" and s.get("E4", {}).get("value") == YES:
            s["E4"] = sig(CHECK, s["E4"]["evidence"], s["E4"]["url"], f"directory shows a US location: {evidence}",
                          method="directory")
        return True
    if cur["value"] not in (CHECK, UNKNOWN) and not overrule_guess:
        return False
    if cur["value"] == CHECK and value == CHECK:
        return False
    s[sid] = sig(value, evidence, url, f"directory, {match} match: {note}", method="directory")
    return True


def apply(x) -> None:
    """x is the rules Extractor: writes into x.s (signals), x.facts and x.partners."""
    found = x.directories_found
    if not found:
        return
    s, facts = x.s, x.facts
    summary = []

    yc = found.get("YC")
    if yc and yc.get("match") == "strong":
        page = f"https://www.ycombinator.com/companies/{yc.get('slug') or ''}"
        where = yc.get("all_locations") or ""
        summary.append(f"YC: {yc.get('batch')}, {yc.get('status')}"
                       + (f", {yc['team_size']} people" if yc.get("team_size") else "") + (f", {where}" if where else ""))
        if _settle(s, "F11", YES, f"Y Combinator {yc.get('batch')} (YC company directory)", page, "listed in the YC directory", "strong"):
            x.partners["Y Combinator"] = s["F11"]["evidence"]
        if yc.get("status") == "Public":
            _settle(s, "E1", YES, "YC directory lists the company as public", page, "YC status: Public", "strong")
        elif yc.get("status") != "Acquired":
            _settle(s, "F1", YES, f"Y Combinator {yc.get('batch')}: YC invests in every company it backs", page,
                    "stage not shown in the directory", "strong")
        if yc.get("nonprofit"):
            _settle(s, "E5", YES, "YC directory lists the company as a non-profit", page, "YC non-profit flag", "strong")
        if "USA" in where or "United States of America" in (yc.get("regions") or []):
            _settle(s, "M1", YES, f"YC directory location: {where}", page, "HQ location in the YC directory", "strong")
        elif where and where != "Remote":
            # one directory field is not enough to exclude a company: the AI step confirms it
            _settle(s, "E4", CHECK, f"YC directory location: {where}", page, "HQ outside the US per YC", "strong")
            _settle(s, "M1", CHECK, f"YC directory location: {where}", page, "HQ outside the US per YC", "strong")
        if yc.get("team_size"):
            facts["team_size_directory"] = {"value": yc["team_size"], "source": "YC directory"}
        batch_date = _yc_batch_date(yc.get("batch") or "")
        if batch_date and 0 <= (date.today() - batch_date).days <= 365:
            _settle(s, "B2", YES, f"joined Y Combinator {yc.get('batch')} (YC directory)", page, "accelerator in the last 12 months", "strong")

    sec = found.get("SEC")
    if sec:
        m = sec["match"]
        page = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={sec['cik']}"
        bits = [sec.get("name") or ""]
        if sec.get("tickers"):
            bits.append(f"ticker {', '.join(sec['tickers'])}")
        if sec.get("state_of_incorporation"):
            bits.append(f"incorporated in {US_STATES.get(sec['state_of_incorporation'], sec['state_of_incorporation'])}")
        if sec.get("form_d"):
            bits.append(f"Form D {', '.join(d[:4] for d in sec['form_d'][:3])}")
        public = bool(sec.get("tickers")) and bool(set(sec.get("exchanges") or []) & EXCHANGES)
        if m != "weak" or public:  # a weak name-only match is shown only when it raised a question to check
            summary.append(f"SEC ({'possible' if m == 'weak' else m} match on {sec['basis']}): " + ", ".join(b for b in bits if b))
        if public:
            _settle(s, "E1", YES, f"publicly traded: {sec['exchanges'][0]}: {sec['tickers'][0]} (SEC EDGAR)", page, sec["name"], m)
        if sec.get("form_d") and not public:
            _settle(s, "F1", YES, f"raised private funding: SEC Form D filed {', '.join(sec['form_d'][:3])}", page,
                    "stage not shown in Form D", m)
            _settle(s, "E2", NO, f"disclosed funding: SEC Form D {sec['form_d'][0]}", page, "Form D on file", m)
        recent_d = [d for d in sec.get("form_d", []) if _days_ago(d) <= 183]
        if recent_d and not public:
            _settle(s, "B1", YES, f"SEC Form D filed {recent_d[0]} (a filing is due within 15 days of a sale)", page,
                    "raised money in the last 6 months", m)
        for former in sec.get("former_names") or []:
            fname = former.get("name") or ""
            if (re.search(r"\bL\.?L\.?C\b", fname, re.I) and name_key(fname) == name_key(sec.get("name") or "")
                    and re.search(r"\b(inc|corp|corporation|incorporated)\b", (sec.get("name") or "").lower())
                    and former.get("until") and _days_ago(former["until"]) <= 548):
                _settle(s, "B6", YES, f"SEC record: renamed from {fname} to {sec['name']} on {former['until']}", page,
                        "LLC to corporation", m)
        details = sec.get("form_d_details") or {}
        if details and "error" not in details:
            sold = _usd(details.get("total_amount_sold"))
            if sold and sold >= _late_stage_round():
                _settle(s, "E1", YES, f"SEC Form D: ${sold / 1e6:,.0f}M sold in one offering", details["url"],
                        "a round of $100M+ is beyond Series B", m)
            year = details.get("year_of_incorporation")
            if year and year.isdigit() and int(year) == date.today().year:
                funded = YES in (s.get("F1", {}).get("value"), s.get("B1", {}).get("value"))
                if funded and s.get("M2", {}).get("value") == YES:
                    _settle(s, "B7", YES, f"SEC Form D: incorporated in {year}, already raising, live product", details["url"],
                            "year of incorporation on Form D", m)
        inc = sec.get("state_of_incorporation")
        corp = re.search(r"\b(inc|incorporated|corp|corporation)\b\.?$", (sec.get("name") or "").lower())
        if inc == "DE" and corp:
            if _settle(s, "F3", YES, f"{sec['name']}: incorporated in Delaware (SEC EDGAR)", page, "state of incorporation", m):
                if facts.get("entity_type", "Unknown") == "Unknown":
                    facts.update(entity_type="Delaware C-Corp", entity_basis="confirmed", entity_evidence="SEC EDGAR")
        if sec.get("business_state") in US_STATES:
            _settle(s, "M1", YES, f"SEC business address: {sec.get('business_city')}, {sec['business_state']}", page,
                    "business address on SEC filings", m)

    old = found.get("Wayback") or {}
    if old.get("legal_name") and facts.get("entity_suffix") == "corp" and facts.get("legal_name"):
        if (re.search(r"\bL\.?L\.?C\b", old["legal_name"], re.I)
                and name_key(old["legal_name"]) == name_key(facts["legal_name"])):
            _settle(s, "B6", YES, f"site footer on {old['home_date']}: {old['legal_name']}; today: {facts['legal_name']}",
                    f"https://web.archive.org/web/{old['home_date'].replace('-', '')}/{x.domain}", "Internet Archive snapshot", "strong")
    if facts.get("prices"):
        before = None
        if old.get("pricing_date") and old.get("pricing_prices") is False:
            before = f"the pricing page on {old['pricing_date']} showed no prices"
        elif old.get("home_date") and old.get("home_links_pricing") is False and old.get("home_prices") is False:
            before = f"the homepage on {old['home_date']} had no pricing link or prices"
        if before:
            _settle(s, "B12", YES, f"{before}; today: {facts['prices'][0]}",
                    f"https://web.archive.org/web/{(old.get('pricing_date') or old['home_date']).replace('-', '')}/{x.domain}",
                    "Internet Archive snapshot", "strong")
    if old:
        summary.append("Internet Archive: " + ", ".join(
            f"{k.replace('_', ' ')} {v}" for k, v in old.items() if k in ("home_date", "pricing_date", "legal_name")))

    irs = found.get("IRS")
    if irs:
        m = irs["match"]
        page = f"https://apps.irs.gov/app/eos/details/?ein={irs['ein'].replace('-', '')}"
        summary.append(f"IRS ({'possible' if m == 'weak' else m} match on {irs['basis']}): {irs['name']}, {irs['state']}, EIN {irs['ein']}")
        if _settle(s, "E5", YES, f"IRS lists '{irs['name']}' ({irs['state']}, EIN {irs['ein']}) as tax-exempt",
                   page, "IRS Publication 78", m) and s["E5"]["value"] == YES:
            facts["entity_type"] = "Non-profit"

    # funding found: an LLC with outside investors is not "LLC with no venture funding"
    if s.get("W4", {}).get("value") == YES and s.get("F1", {}).get("method") == "directory" and s["F1"]["value"] == YES:
        s["W4"] = sig(NO, s["F1"]["evidence"], s["F1"]["url"], "directory: funding found", method="directory")
    facts["directories"] = "; ".join(summary)
    if found.get("errors"):
        facts["directory_errors"] = found["errors"]


def _days_ago(iso: str) -> int:
    try:
        return (date.today() - date.fromisoformat(iso[:10])).days
    except ValueError:
        return 10 ** 6


def _usd(text: str | None) -> float | None:
    try:
        return float(text) if text else None
    except ValueError:
        return None  # "Indefinite"


def _late_stage_round() -> float:
    from ..checks.rules import LATE_STAGE_ROUND_USD
    return LATE_STAGE_ROUND_USD


def _yc_batch_date(batch: str) -> date | None:
    """'Winter 2026' -> 2026-01-01, 'Summer 2025' -> 2025-06-01."""
    m = re.match(r"(Winter|Spring|Summer|Fall)\s+(\d{4})", batch)
    if not m:
        return None
    return date(int(m.group(2)), {"Winter": 1, "Spring": 4, "Summer": 6, "Fall": 9}[m.group(1)], 1)


# status / refresh ---------------------------------------------------------
def status() -> list[dict]:
    out = []
    for name, path, what in (("YC directory", YC_FILE, "companies with a website"),
                             ("IRS non-profit list (Pub 78)", IRS_FILE, "organisations"),
                             ("SEC EDGAR lookups", SEC_FILE, "saved lookups")):
        row = {"name": name, "path": path, "exists": path.exists()}
        if path.exists():
            row["updated"] = date.fromtimestamp(path.stat().st_mtime).isoformat()
            row["size"] = path.stat().st_size
            if path == YC_FILE:
                row["count"] = f"{len(json.loads(path.read_text())):,} {what}"
            else:
                db = sqlite3.connect(path)
                table = "orgs" if path == IRS_FILE else "calls"
                row["count"] = f"{db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:,} {what}"
                db.close()
        out.append(row)
    return out


def refresh() -> None:
    global _yc
    _yc = None
    _yc_index(refresh=True)
    _irs_db(refresh=True).close()
