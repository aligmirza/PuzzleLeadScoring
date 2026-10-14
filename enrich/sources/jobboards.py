"""Open jobs, read for free from the company's own job board or careers page (the same jobs it posts on LinkedIn and
Indeed). No login, no scraping of job sites: each board's public listing feed, or the careers page itself.

    detect(html)                 the boards a company's pages link to: [(board, params), ...], most specific first
    request(board, params)       (method, url, body) of the board's public listing feed
    parse(board, params, text)   the jobs, normalised: {title, location, remote, description, url}
    careers_page_jobs(html, url) jobs listed on the careers page itself (schema.org JobPosting, or job links)

Boards: Greenhouse, Lever, Ashby, Workable, Rippling, Workday, BambooHR, Dover, SmartRecruiters, Recruitee,
Teamtailor, Personio, Breezy.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

BAD_SLUGS = {"embed", "v1", "api", "j", "jobs", "js", "static", "assets", "www", "app", "careers", "search", "en", "en-us"}

# board -> pattern; named groups become the request params
DETECT = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?(?P<slug>[A-Za-z0-9_-]+)"),
    ("lever", r"jobs\.lever\.co/(?P<slug>[A-Za-z0-9_.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/(?P<slug>[A-Za-z0-9_.%-]+)"),
    ("workable", r"apply\.workable\.com/(?P<slug>[A-Za-z0-9_-]+)"),
    ("rippling", r"ats\.rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?P<slug>[A-Za-z0-9_-]+)"),
    ("workday", r"(?P<tenant>[a-z0-9-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[A-Za-z0-9_-]+)"),
    ("bamboohr", r"(?P<slug>[a-z0-9-]+)\.bamboohr\.com"),
    ("dover", r"app\.dover\.com/(?:apply/)?[^/\"'\s]+/careers/(?P<slug>[0-9a-f-]{36})"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/(?P<slug>[A-Za-z0-9_-]+)"),
    ("recruitee", r"(?P<slug>[a-z0-9-]+)\.recruitee\.com"),
    ("teamtailor", r"(?P<slug>[a-z0-9-]+)\.teamtailor\.com"),
    ("personio", r"(?P<slug>[a-z0-9-]+)\.jobs\.personio\.(?P<tld>de|com)"),
    ("breezy", r"(?P<slug>[a-z0-9-]+)\.breezy\.hr"),
]
_DETECT = [(b, re.compile(p, re.I)) for b, p in DETECT]
# the board's own domain, which every link to it contains: the pattern only runs in a window around each mention
# (patterns that start with the account name scanned whole pages letter by letter: up to half a second a company)
DOMAIN = {"greenhouse": "greenhouse.io", "lever": "lever.co", "ashby": "ashbyhq.com", "workable": "workable.com",
          "rippling": "rippling.com", "workday": "myworkdayjobs.com", "bamboohr": "bamboohr.com", "dover": "dover.com",
          "smartrecruiters": "smartrecruiters.com", "recruitee": "recruitee.com", "teamtailor": "teamtailor.com",
          "personio": "jobs.personio.", "breezy": "breezy.hr"}


def _near(html: str, low: str, word: str, before: int = 120, after: int = 200):
    """Pieces of the page around each mention of `word` (in page order)."""
    i = low.find(word)
    while i != -1:
        yield html[max(0, i - before): i + len(word) + after]
        i = low.find(word, i + 1)
PUBLIC_URL = {  # a page anyone can open, for the proof link
    "greenhouse": "https://boards.greenhouse.io/{slug}", "lever": "https://jobs.lever.co/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}", "workable": "https://apply.workable.com/{slug}",
    "rippling": "https://ats.rippling.com/{slug}/jobs", "workday": "https://{tenant}.{wd}.myworkdayjobs.com/{site}",
    "bamboohr": "https://{slug}.bamboohr.com/careers", "dover": "https://app.dover.com/jobs/{slug}",
    "smartrecruiters": "https://jobs.smartrecruiters.com/{slug}", "recruitee": "https://{slug}.recruitee.com",
    "teamtailor": "https://{slug}.teamtailor.com/jobs", "personio": "https://{slug}.jobs.personio.{tld}",
    "breezy": "https://{slug}.breezy.hr",
}


def detect(html: str) -> list[tuple[str, dict]]:
    """Boards linked from the company's pages, each once, in the order above (Greenhouse ... Breezy)."""
    out, seen = [], set()
    html = html or ""
    low = html.lower()
    for board, rx in _DETECT:
        if DOMAIN[board] not in low:
            continue
        for m in (m for piece in _near(html, low, DOMAIN[board]) for m in rx.finditer(piece)):
            params = {k: v for k, v in m.groupdict().items() if v}
            if params.get("slug", "").lower() in BAD_SLUGS:  # a path word, not an account ("search" is a real Workday site)
                continue
            key = (board, tuple(sorted(params.items())))
            if key not in seen:
                seen.add(key)
                out.append((board, params))
            break  # one board account per kind is enough
    return out


def public_url(board: str, params: dict) -> str:
    try:
        return PUBLIC_URL[board].format(**params)
    except (KeyError, IndexError):
        return ""


def request(board: str, p: dict) -> tuple[str, str, dict | None]:
    s = p.get("slug", "")
    return {
        "greenhouse": ("GET", f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs?content=true", None),
        "lever": ("GET", f"https://api.lever.co/v0/postings/{s}?mode=json", None),
        "ashby": ("GET", f"https://api.ashbyhq.com/posting-api/job-board/{s}", None),
        "workable": ("GET", f"https://apply.workable.com/api/v1/widget/accounts/{s}", None),
        "rippling": ("GET", f"https://ats.rippling.com/api/v2/board/{s}/jobs", None),
        "workday": ("POST", f"https://{p.get('tenant')}.{p.get('wd')}.myworkdayjobs.com/wday/cxs/{p.get('tenant')}/{p.get('site')}/jobs",
                    {"limit": 20, "offset": 0, "searchText": "", "appliedFacets": {}}),
        "bamboohr": ("GET", f"https://{s}.bamboohr.com/careers/list", None),
        "dover": ("GET", f"https://app.dover.com/api/v1/careers-page/{s}/jobs", None),
        "smartrecruiters": ("GET", f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=100", None),
        "recruitee": ("GET", f"https://{s}.recruitee.com/api/offers/", None),
        "teamtailor": ("GET", f"https://{s}.teamtailor.com/jobs.rss", None),
        "personio": ("GET", f"https://{s}.jobs.personio.{p.get('tld', 'de')}/xml", None),
        "breezy": ("GET", f"https://{s}.breezy.hr/json", None),
    }[board]


def _plain(text: str | None) -> str:
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return " ".join(text.split())


def _job(title, location="", remote=False, description="", url="") -> dict:
    loc = location if isinstance(location, str) else ", ".join(x for x in location if x)
    return {"title": (title or "").strip(), "location": loc or "", "remote": bool(remote) or "remote" in (loc or "").lower(),
            "description": _plain(description)[:4000], "url": url or ""}


def parse(board: str, p: dict, text: str) -> list[dict] | None:
    """The board's jobs, or None if the answer isn't what the board sends (wrong account, error page)."""
    try:
        if board == "teamtailor":
            root = ET.fromstring(text)
            return [_job(i.findtext("title"), [x.text for x in i.iter() if x.tag.endswith("location") and x.text],
                         "remote" in (i.findtext("{https://teamtailor.com/locations}remoteStatus") or "").lower(),
                         i.findtext("description"), i.findtext("link")) for i in root.iter("item")][:200]
        if board == "personio":
            root = ET.fromstring(text)
            return [_job(x.findtext("name"), x.findtext("office"), False,
                         " ".join(_plain(v.findtext("value")) for v in x.iter("jobDescription")))
                    for x in root.iter("position")][:200]
        data = json.loads(text)
    except (ValueError, ET.ParseError):
        return None
    jobs = []
    if board == "greenhouse":
        for j in (data or {}).get("jobs", []):
            jobs.append(_job(j.get("title"), (j.get("location") or {}).get("name", ""), False, j.get("content"), j.get("absolute_url")))
    elif board == "lever":
        for j in data if isinstance(data, list) else []:
            cats = j.get("categories") or {}
            desc = " ".join([j.get("descriptionPlain", "") or ""] + [_plain(x.get("content")) for x in j.get("lists", []) or []]
                            + [j.get("additionalPlain", "") or ""])
            jobs.append(_job(j.get("text"), cats.get("location", ""), j.get("workplaceType") == "remote", desc, j.get("hostedUrl")))
    elif board == "ashby":
        for j in (data or {}).get("jobs", []):
            jobs.append(_job(j.get("title"), j.get("location", ""), j.get("isRemote"),
                             j.get("descriptionPlain") or j.get("descriptionHtml"), j.get("jobUrl")))
    elif board == "workable":
        for j in (data or {}).get("jobs", []):
            jobs.append(_job(j.get("title"), [j.get("city"), j.get("country")], j.get("telecommuting"), j.get("description"), j.get("url")))
    elif board == "rippling":
        for j in (data or {}).get("items", []) if isinstance(data, dict) else []:
            locs = [x.get("name") for x in j.get("locations") or [] if isinstance(x, dict)]
            jobs.append(_job(j.get("name"), locs, any("remote" in (x or "").lower() for x in locs), "", j.get("url")))
    elif board == "workday":
        base = f"https://{p.get('tenant')}.{p.get('wd')}.myworkdayjobs.com/{p.get('site')}"
        for j in (data or {}).get("jobPostings", []):
            jobs.append(_job(j.get("title"), j.get("locationsText", ""), "remote" in (j.get("remoteType") or "").lower(), "",
                             base + (j.get("externalPath") or "")))
    elif board == "bamboohr":
        for j in (data or {}).get("result", []):
            loc = j.get("location") or {}
            jobs.append(_job(j.get("jobOpeningName"), [loc.get("city"), loc.get("state")],
                             str(j.get("isRemote")) in ("1", "True", "true") or j.get("locationType") == "1", "",
                             f"https://{p.get('slug')}.bamboohr.com/careers/{j.get('id')}"))
    elif board == "dover":
        for j in (data or {}).get("results", []):
            locs = [x.get("name") or x.get("location_type") for x in j.get("locations") or [] if isinstance(x, dict)]
            jobs.append(_job(j.get("title"), locs, any("remote" in (x or "").lower() for x in locs), ""))
    elif board == "smartrecruiters":
        for j in (data or {}).get("content", []):
            loc = j.get("location") or {}
            jobs.append(_job(j.get("name"), [loc.get("city"), loc.get("country")], loc.get("remote"), ""))
    elif board == "recruitee":
        for j in (data or {}).get("offers", []):
            jobs.append(_job(j.get("title"), j.get("location", ""), j.get("remote"), j.get("description"), j.get("careers_url")))
    elif board == "breezy":
        for j in data if isinstance(data, list) else []:
            loc = j.get("location") or {}
            jobs.append(_job(j.get("name"), loc.get("name", ""), loc.get("is_remote"), "", j.get("url")))
    else:
        return None
    return [j for j in jobs if j["title"]][:200]


# ---------------------------------------------------------------------------------------------------------------------
# jobs listed on the careers page itself
RE_ROLE = re.compile(r"\b(engineer|developer|designer|manager|director|head|lead|analyst|scientist|specialist|associate|"
                     r"coordinator|representative|executive|officer|consultant|architect|administrator|accountant|"
                     r"controller|bookkeeper|recruiter|intern|researcher|technician|writer|editor|strategist|"
                     r"marketer|operator|advisor|partner|founding|vp|chief|cfo|cto|coo|sales|support|success|"
                     r"marketing|operations|finance|product|account|legal|counsel|nurse|physician|teacher)\b", re.I)
# a heading only counts when it ends like a job title ("Senior Applications Engineer", "Head of Sales")
RE_TITLE_END = re.compile(r"(?:engineer|developer|designer|manager|director|analyst|scientist|specialist|associate|"
                          r"coordinator|representative|executive|officer|consultant|architect|administrator|accountant|"
                          r"controller|bookkeeper|recruiter|intern|researcher|technician|writer|editor|strategist|"
                          r"\bhead of [a-z &]+|\bvp,? [a-z &]+|\blead)(?:\s*\([^)]*\))?(?:\s*[-–,|].{0,40})?$", re.I)
RE_JOB_PATH = re.compile(r"/(?:careers?|jobs?|positions?|openings?|roles?|join-us|work-with-us|vacancies)/[^/?#]{3,}", re.I)
RE_NOT_A_JOB = re.compile(r"\b(?:all jobs|view all|see all|open positions|open roles|careers?|apply now|learn more|"
                          r"read more|join us|our team|benefits|culture|life at|faq|privacy|blog)\b", re.I)
RE_ANCHOR = re.compile(r"<a\b[^>]*\bhref=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)


RE_CARD_NOISE = re.compile(r"\s*\b(?:view role|view job|apply now|apply|learn more|full[- ]time|part[- ]time|contract|"
                           r"internship|in[- ]person|on[- ]?site|remote|hybrid)\b", re.I)


def _card(text: str, url: str) -> dict:
    """A job card's text ("Software Engineer San Francisco In Person Full-time View role") as title + flags."""
    remote = bool(re.search(r"\bremote\b", text, re.I))
    in_person = bool(re.search(r"\bin[- ]person\b|\bon[- ]?site\b", text, re.I))
    title = " ".join(RE_CARD_NOISE.sub(" ", text).split())
    job = _job(title, url=url, remote=remote)
    if in_person:
        job["location"] = "in person"
    return job


def careers_page_jobs(html: str, url: str) -> list[dict]:
    """Jobs listed on the careers page itself: schema.org JobPosting data first, else links to individual job pages
    whose text reads like a job title. Titles and links only (no descriptions)."""
    jobs: list[dict] = []
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html or "", re.I | re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if isinstance(it, dict) and "JobPosting" in str(it.get("@type")):
                loc = ((it.get("jobLocation") or {}) if isinstance(it.get("jobLocation"), dict) else {}).get("address") or {}
                jobs.append(_job(it.get("title"), [loc.get("addressLocality"), loc.get("addressRegion")] if isinstance(loc, dict) else "",
                                 it.get("jobLocationType") == "TELECOMMUTE", it.get("description"), it.get("url")))
    if jobs:
        return jobs[:100]
    host = (urlparse(url).hostname or "").removeprefix("www.")
    seen = set()
    for href, inner in RE_ANCHOR.findall(html or ""):
        full = urljoin(url, href)
        h = (urlparse(full).hostname or "").removeprefix("www.")
        text = " ".join(_plain(inner).split())
        if (h != host and not h.endswith("." + host)) or not RE_JOB_PATH.search(urlparse(full).path):
            continue
        if not (2 <= len(text.split()) <= 10) or RE_NOT_A_JOB.search(text) or not RE_ROLE.search(text):
            continue
        if full not in seen:
            seen.add(full)
            jobs.append(_card(text, full))
    if jobs:
        return jobs[:100]
    # last: job titles written as headings (no link per job), e.g. "<h3>Applications Engineer</h3>"
    for tag, inner in re.findall(r"<(h[2-5])\b[^>]*>(.*?)</\1>", html or "", re.I | re.S):
        text = " ".join(_plain(inner).split())
        if 2 <= len(text.split()) <= 8 and RE_TITLE_END.search(text) and not RE_NOT_A_JOB.search(text) and text not in seen:
            seen.add(text)
            jobs.append(_job(text, url=url))
    return jobs[:100]
