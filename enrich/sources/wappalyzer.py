"""Website technologies, detected locally with the open-source Wappalyzer fingerprints (the community-kept copy at
github.com/enthec/webappanalyzer, GPL-3.0). Free: the fingerprint files are downloaded once into cache/wappalyzer
(refreshed monthly, never committed) and matched in our own code, like BuiltWith or Wappalyzer would do on the
public website: page HTML, script sources, meta tags, response headers, and DNS records (TXT, MX).

What it can see: anything in a website's code or DNS (CMS, analytics, payments, chat, email, hosting...).
What it can't: tools used only inside the company (payroll, banking, accounting), unless their DNS shows them.
"""
from __future__ import annotations

import json
import re
import threading
import time

import httpx

from ..core.paths import CACHE

SRC = "https://raw.githubusercontent.com/enthec/webappanalyzer/main/src"
DIR = CACHE / "wappalyzer"
MAX_AGE_DAYS = 30
FILES = ["categories.json"] + [f"technologies/{c}.json" for c in "_abcdefghijklmnopqrstuvwxyz"]
ENABLED = True

_lock = threading.Lock()
_db: dict | None = None
RE_SCRIPT = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.I)
RE_META = re.compile(r"<meta\b[^>]*>", re.I)
RE_ATTR = re.compile(r"""(name|property|content)\s*=\s*["']([^"']*)["']""", re.I)


def _stale() -> bool:
    f = DIR / "categories.json"
    return not f.exists() or time.time() - f.stat().st_mtime > MAX_AGE_DAYS * 86400


def update(force: bool = False) -> int:
    """Download the fingerprint files (about 3 MB). Returns the number of technologies."""
    if force or _stale():
        texts = _run(_download())
        (DIR / "technologies").mkdir(parents=True, exist_ok=True)
        for name, text in texts.items():
            (DIR / name).write_text(text)
    global _db
    _db = None
    return len(_load()["techs"])


async def _download() -> dict[str, str]:
    import asyncio
    from .crawl import PublicDNSFallback  # public DNS when the local resolver fails
    dns = PublicDNSFallback()
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            c._transport._pool._network_backend = dns
            rs = await asyncio.gather(*(c.get(f"{SRC}/{n}") for n in FILES))
        out = {}
        for n, r in zip(FILES, rs):
            r.raise_for_status()
            json.loads(r.text)  # only save valid files
            out[n] = r.text
        return out
    finally:
        await dns.aclose()


def _run(coro):
    """Run a coroutine from sync code, also when called inside a running event loop (the crawler)."""
    import asyncio
    box: dict = {}
    th = threading.Thread(target=lambda: box.update(v=asyncio.run(coro)))
    th.start()
    th.join()
    if "v" not in box:
        raise RuntimeError("download failed")
    return box["v"]


def _pattern(p: str) -> tuple[re.Pattern | None, str]:
    """A Wappalyzer pattern is a JS regex plus optional ';version:...' / ';confidence:...' tags."""
    regex, *tags = str(p).split("\\;")
    version = next((t.split(":", 1)[1] for t in tags if t.startswith("version:")), "")
    try:
        return re.compile(regex, re.I), version
    except re.error:
        return None, ""


RE_SELECTOR = re.compile(r"^\s*([a-z][a-z0-9-]*)?\[([a-z0-9_:-]+)(?:([*^$]?)=\s*['\"]?([^'\"\]]*)['\"]?)?\]\s*$", re.I)


class _AttrRule:
    """A selector whose attribute value must also match a Wappalyzer regex (behaves like a compiled pattern)."""
    def __init__(self, rx: re.Pattern, value_rx: re.Pattern, op: str = "", val: str | None = None):
        self.rx, self.value_rx, self.op, self.val = rx, value_rx, op, val

    def _selector_ok(self, v: str) -> bool:  # the selector's own test: [href*='x'], [href^='x'], [href$='x'], [href='x']
        if self.val is None:
            return True
        low, want = v.lower(), self.val.lower()
        return {"*": want in low, "^": low.startswith(want), "$": low.endswith(want), "": low == want}[self.op or ""]

    def search(self, text: str):
        for m in self.rx.finditer(text):
            if self._selector_ok(m.group(1)) and self.value_rx.search(m.group(1)):
                return m
        return None


def _dom(selector: str, attributes: dict | None = None):
    """A simple CSS selector as a regex over the HTML (one tag with one attribute test); anything else is skipped.
    With `attributes` ({attr: regex}), the attribute's value must match that regex as well."""
    m = RE_SELECTOR.match(str(selector).split(",")[0])
    if not m:
        return None
    tag, attr, op, val = m.group(1) or r"[a-z][a-z0-9-]*", m.group(2), m.group(3), m.group(4)
    need = {k: v for k, v in (attributes or {}).items() if v}
    if need:
        a, pattern = next(iter(need.items()))
        if a == "text":
            return None  # link text tests need a real page parser
        value_rx, _ = _pattern(pattern)
        if not value_rx:
            return None
        same = a.lower() == attr.lower()
        try:
            return _AttrRule(re.compile(rf"<{tag}\b[^>]*\b{re.escape(a)}\s*=\s*[\"']([^\"']*)[\"']", re.I), value_rx,
                             op if same else "", val if same else None)
        except re.error:
            return None
    if val is None:
        value = r"[^\"']*"
    else:
        v = re.escape(val)
        value = {"*": rf"[^\"']*{v}[^\"']*", "^": rf"{v}[^\"']*", "$": rf"[^\"']*{v}", "": v}[op or ""]
    try:
        return re.compile(rf"<{tag}\b[^>]*\b{re.escape(attr)}\s*=\s*[\"']{value}[\"']", re.I)
    except re.error:
        return None


def _load() -> dict:
    global _db
    with _lock:
        if _db is not None:
            return _db
        if _stale():
            try:
                update()
            except Exception:  # noqa: BLE001  no network: work with what's cached, or nothing
                pass
        cats = json.loads((DIR / "categories.json").read_text()) if (DIR / "categories.json").exists() else {}
        techs: dict[str, dict] = {}
        for f in sorted((DIR / "technologies").glob("*.json")) if (DIR / "technologies").exists() else []:
            techs.update(json.loads(f.read_text()))
        _db = {"cats": {k: v.get("name", k) for k, v in cats.items()}, "techs": techs, "compiled": {}}
        return _db


def _compiled(name: str, spec: dict) -> dict:
    db = _load()
    if name in db["compiled"]:
        return db["compiled"][name]

    def many(v):
        return [x for x in (_pattern(p) for p in (v if isinstance(v, list) else [v])) if x[0]]

    def keyed(v):
        return {k.lower(): many(p) for k, p in (v or {}).items()} if isinstance(v, dict) else {}

    dns = spec.get("dns") or {}
    dom = spec.get("dom") or []
    if isinstance(dom, dict):  # {selector: {"attributes": {attr: regex}}}: the attribute must match too
        dom_rx = [_dom(sel, (rule or {}).get("attributes")) for sel, rule in dom.items()
                  if not (rule or {}).get("text") or (rule or {}).get("attributes")]
    else:
        dom_rx = [_dom(s) for s in (dom if isinstance(dom, list) else [dom])]
    out = {"html": many(spec.get("html") or []) + [(r, "") for r in dom_rx if r], "scriptSrc": many(spec.get("scriptSrc") or []),
           "url": many(spec.get("url") or []), "meta": keyed(spec.get("meta")), "headers": keyed(spec.get("headers")),
           "dns": {t.upper(): many(v) for t, v in dns.items()} if isinstance(dns, dict) else {}}
    db["compiled"][name] = out
    return out


def analyze(pages: dict[str, str] | None = None, headers: dict | None = None, dns: dict | None = None,
            url: str = "") -> dict[str, dict]:
    """Technologies found, as {name: {"categories": [...], "how": "html on https://...", "version": ""}}.
    pages = {url: html}; dns = {"TXT": [...], "MX": [...]}."""
    if not ENABLED:
        return {}
    db = _load()
    if not db["techs"]:
        return {}
    pages = pages or {}
    scripts = {u: RE_SCRIPT.findall(h) for u, h in pages.items()}
    metas = {}
    for u, h in pages.items():
        for tag in RE_META.findall(h):
            attrs = {k.lower(): v for k, v in RE_ATTR.findall(tag)}
            key = (attrs.get("name") or attrs.get("property") or "").lower()
            if key:
                metas.setdefault(key, (attrs.get("content", ""), u))
    heads = {k.lower(): str(v) for k, v in (headers or {}).items()}
    found: dict[str, dict] = {}

    def hit(name, how, m, version_tpl):
        version = ""
        if version_tpl and m is not None:
            try:
                version = re.sub(r"\\(\d)", lambda g: m.group(int(g.group(1))) or "", version_tpl)
            except (IndexError, re.error):
                version = ""
        spec = db["techs"].get(name, {})
        found.setdefault(name, {"categories": [db["cats"].get(str(c), str(c)) for c in spec.get("cats", [])],
                                "how": how, "version": version[:20]})

    for name, spec in db["techs"].items():
        c = _compiled(name, spec)
        done = False
        for rx, v in c["html"]:
            for u, h in pages.items():
                m = rx.search(h)
                if m:
                    hit(name, f"page code on {u}", m, v)
                    done = True
                    break
            if done:
                break
        if not done and c["scriptSrc"]:
            for u, srcs in scripts.items():
                for s in srcs:
                    for rx, v in c["scriptSrc"]:
                        m = rx.search(s)
                        if m:
                            hit(name, f"script {s[:100]} on {u}", m, v)
                            done = True
                            break
                    if done:
                        break
                if done:
                    break
        if not done:
            for key, pats in c["meta"].items():
                if key in metas:
                    for rx, v in pats:
                        m = rx.search(metas[key][0])
                        if m:
                            hit(name, f"meta {key} on {metas[key][1]}", m, v)
                            done = True
                            break
                if done:
                    break
        if not done:
            for key, pats in c["headers"].items():
                if key in heads:
                    for rx, v in pats:
                        m = rx.search(heads[key])
                        if m:
                            hit(name, f"response header {key}", m, v)
                            done = True
                            break
                if done:
                    break
        if not done and url:
            for rx, v in c["url"]:
                m = rx.search(url)
                if m:
                    hit(name, "site address", m, v)
                    done = True
                    break
        if not done and dns:
            for rtype, pats in c["dns"].items():
                for rec in dns.get(rtype, []):
                    for rx, v in pats:
                        m = rx.search(rec)
                        if m:
                            hit(name, f"DNS {rtype} record {rec[:80]}", m, v)
                            done = True
                            break
                    if done:
                        break
                if done:
                    break

    for name in list(found):  # 'implies': WordPress implies PHP and MySQL, and so on
        for imp in db["techs"].get(name, {}).get("implies") or []:
            imp = str(imp).split("\\;")[0]
            if imp in db["techs"] and imp not in found:
                found[imp] = {"categories": [db["cats"].get(str(c), str(c)) for c in db["techs"][imp].get("cats", [])],
                              "how": f"implied by {name}", "version": ""}
    return found


def summary(found: dict[str, dict], limit: int = 25) -> str:
    """'Next.js (JavaScript frameworks); HubSpot (Marketing automation)...' for a CSV cell."""
    direct = [n for n, v in found.items() if not v["how"].startswith("implied")]
    return "; ".join(f"{n} ({', '.join(found[n]['categories'][:1])})" if found[n]["categories"] else n for n in direct[:limit])
