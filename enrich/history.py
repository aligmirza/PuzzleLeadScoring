"""Old copies of a company's website from the Internet Archive (Wayback Machine). Free, no account.

Used in phase 2 for two buying signals that need a "before":
  B6  Converted from an LLC to a C-Corp   the footer's legal name 6 to 18 months ago ended in LLC, today in Inc.
  B12 Recently launched paid pricing      6 to 18 months ago the site had no prices (no pricing link, or a pricing
                                          page without prices); today it publishes prices

Only real snapshots count. "The Archive has no copy" proves nothing, so it leaves the signal empty.
The Archive rate-limits, so requests are spaced out (about 2 a second for the whole run).
"""
from __future__ import annotations

import re
import threading
import time
from datetime import date, timedelta

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

CDX = "https://web.archive.org/cdx/search/cdx"
SNAPSHOT = "https://web.archive.org/web/{ts}id_/{url}"
PER_SECOND = 2
WINDOW = (6, 18)  # months back

_lock = threading.Lock()
_last = 0.0


def _get(client: httpx.Client, endpoint: str, **params) -> httpx.Response | None:
    global _last
    for attempt in range(4):
        with _lock:
            wait = _last + 1 / PER_SECOND - time.time()
            if wait > 0:
                time.sleep(wait)
            _last = time.time()
        try:
            r = client.get(endpoint, params=params or None)
        except httpx.HTTPError:
            r = None
        if r is not None and r.status_code == 200:
            return r
        if r is not None and r.status_code not in (429, 502, 503, 504):
            return None
        time.sleep(2 ** attempt * 2)
    raise RuntimeError("Internet Archive not responding (rate limit or outage)")


def _snapshot(client: httpx.Client, url: str) -> tuple[str, str] | None:
    """The snapshot of url closest to 12 months ago, within 6 to 18 months ago: (date, html)."""
    today = date.today()
    start, end = today - timedelta(days=WINDOW[1] * 30), today - timedelta(days=WINDOW[0] * 30)
    r = _get(client, CDX, url=url, output="json", filter="statuscode:200", collapse="timestamp:6",
             **{"from": start.strftime("%Y%m%d"), "to": end.strftime("%Y%m%d"), "limit": "24"})
    rows = (r.json() if r else [])[1:]
    if not rows:
        return None
    target = (today - timedelta(days=365)).strftime("%Y%m%d")
    ts, original = min(((row[1], row[2]) for row in rows), key=lambda x: abs(int(x[0][:8]) - int(target)))
    page = _get(client, SNAPSHOT.format(ts=ts, url=original))
    if not page:
        return None
    return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}", page.text


def _text(html: str) -> tuple[str, str]:
    tree = HTMLParser(html)
    for n in tree.css("script, style, noscript, svg"):
        n.decompose()
    body = " ".join((tree.body.text(separator=" ") if tree.body else "").split())
    footer = tree.css_first("footer")
    return body, " ".join(footer.text(separator=" ").split()) if footer else ""


def old_site(domain: str, want_legal: bool, want_pricing: bool) -> dict:
    """What the site looked like about a year ago. Keys only appear when a snapshot was found."""
    from .rules import RE_ENTITY, RE_PRICE, RE_PRICE_LOOSE, clean_legal_name
    out: dict = {}
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "PuzzleLeadScoring research tool"}) as c:
        home = _snapshot(c, domain)
        if home:
            when, html = home
            body, footer = _text(html)
            out["home_date"] = when
            if want_legal:
                for src in (footer, body[-1500:]):
                    i = re.search(r"©|\(c\)|copyright", src, re.I)
                    m = RE_ENTITY.search(src[i.end(): i.end() + 160]) if i else None
                    if m:
                        out["legal_name"] = clean_legal_name(f"{m.group(1)} {m.group(2)}")
                        break
            if want_pricing:
                out["home_links_pricing"] = bool(re.search(r"href=[\"'][^\"']*/(?:pricing|plans)\b|>\s*(?:Pricing|Plans)\s*<", html, re.I))
                out["home_prices"] = bool(RE_PRICE.search(body) or RE_PRICE_LOOSE.search(body))
        if want_pricing:
            pricing = _snapshot(c, f"{domain}/pricing")
            if pricing:
                when, html = pricing
                body, _ = _text(html)
                out["pricing_date"] = when
                out["pricing_prices"] = bool(RE_PRICE.search(body) or RE_PRICE_LOOSE.search(body))
    return out
