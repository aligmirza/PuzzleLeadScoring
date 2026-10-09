"""Download the important pages of a company website, plus its open jobs.

For each domain:
  1. load the homepage (https, then www, then http)
  2. find important pages from homepage links, then the sitemap, then common paths
  3. download them (respecting robots.txt)
  4. follow links one level deeper for pages still missing (team, careers, legal)
  5. detect the job board (Greenhouse, Lever, Ashby, Workable) and pull jobs from its free public feed
"""
import asyncio
import contextvars
import hashlib
import html as htmllib
import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpcore
import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
MAX_BYTES = 3_000_000

# page kind -> (path regex, link text regex)
PAGE_RULES = {
    "about": (r"(^|/)(about|about-us|company|our-story|who-we-are|mission)(/|$|\.)", r"^(about|about us|company|our story|who we are)$"),
    "team": (r"(^|/)(team|our-team|people|leadership|founders|meet-the-team)(/|$|\.)", r"^(team|our team|leadership|people|meet the team)$"),
    "pricing": (r"(^|/)(pricing|plans|plans-and-pricing|prices)(/|$|\.)", r"^(pricing|plans|plans (&|and) pricing)$"),
    "careers": (r"(^|/)(careers|career|jobs|join-us|join|work-with-us|hiring|open-positions|open-roles)(/|$|\.)", r"^(careers|jobs|we.?re hiring|join us|join the team|open roles)$"),
    "privacy": (r"privacy", r"privacy"),
    "terms": (r"(^|/)(terms|tos|terms-of-service|terms-of-use|terms-and-conditions|terms-conditions)(/|$|\.|-)", r"^terms"),
    "legal": (r"(^|/)(legal|imprint|impressum)(/|$|\.)", r"^(legal|imprint|impressum)$"),
    "contact": (r"(^|/)(contact|contact-us)(/|$|\.)", r"^contact"),
    "security": (r"(sub-?processors|(^|/)security(/|$)|(^|/)trust(/|$)|(^|/)dpa(/|$))", r"(sub-?processors|^security$|trust center)"),
    "blog": (r"^(blog|news|newsroom|press)/?$", r"^(blog|news|press|newsroom)$"),
    "engineering": (r"(^|/)(engineering|eng-blog|tech-blog)(/|$)", r"^engineering( blog)?$"),
    "integrations": (r"(^|/)(integrations|partners)(/|$|\.)", r"^(integrations|partners)$"),
    "customers": (r"^(customers|case-studies|stories)/?$", r"^(customers|case studies)$"),
    "investors": (r"(^|/)(investors|investor-relations)(/|$|\.)", r"^investor relations$"),
    "locations": (r"(^|/)(locations|areas-we-serve|service-areas?|our-locations)(/|$|\.)", r"(locations|areas we serve|service areas?)"),
    "shipping": (r"(^|/)(shipping|returns|shipping-policy|refund-policy|shipping-returns)(/|$|\.)", r"(shipping|returns)"),
    "donate": (r"(^|/)(donate|donation|donations|give)(/|$|\.)", r"^(donate|give|give now|donate now)$"),
    "menu": (r"(^|/)(menu|menus|our-menu)(/|$|\.)", r"^(menu|our menu)$"),
}
KEY_KINDS = ["about", "pricing", "careers", "privacy", "terms"]
# Phase 1 (ICP check) reads only what must-haves and exclusions need.
ICP_KINDS = {"about", "pricing", "careers", "privacy", "terms", "legal", "contact", "security", "investors",
             "donate", "shipping"}
# Phase 2 (signals) adds the pages fit, buying and weak signals need.
SIGNAL_KINDS = {"team", "blog", "engineering", "integrations", "customers", "locations", "menu"}
GUESSES = {
    "about": ["/about", "/about-us"],
    "pricing": ["/pricing"],
    "careers": ["/careers", "/jobs"],
    "privacy": ["/privacy", "/privacy-policy"],
    "terms": ["/terms", "/terms-of-service"],
}
SECOND_HOP = ["team", "careers", "privacy", "terms", "legal", "security"]

ATS_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)")),
    ("rippling", re.compile(r"ats\.rippling\.com/([A-Za-z0-9_-]+)")),
]
BAD_SLUGS = {"embed", "v1", "api", "j", "jobs", "js", "static", "assets"}
_REQ_COUNT: contextvars.ContextVar[list] = contextvars.ContextVar("req_count")
BLOCK_TITLES = re.compile(r"just a moment|attention required|access denied|verify you are human|ddos protection", re.I)


@dataclass
class Fetched:
    url: str
    status: int
    ctype: str
    text: str
    headers: dict


def strip_www(host: str | None) -> str:
    return (host or "").lower().removeprefix("www.")


def page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html[:20000], re.S | re.I)
    return htmllib.unescape(m.group(1)).strip() if m else ""


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    out = []
    try:
        tree = HTMLParser(html)
    except Exception:
        return out
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        if url.startswith("http"):
            out.append((url, " ".join(a.text(separator=" ").split()).lower()[:60]))
    return out


def classify(links: list[tuple[str, str]]) -> dict[str, str]:
    """Pick the best URL for each page kind. Shallower paths win."""
    best: dict[str, tuple[int, int, str]] = {}
    for url, anchor in links:
        path = urlparse(url).path.lower().strip("/")
        depth = path.count("/") + 1 if path else 0
        if depth == 0 or depth > 3:
            continue
        for kind, (path_re, anchor_re) in PAGE_RULES.items():
            if re.search(path_re, path) or (anchor and re.search(anchor_re, anchor)):
                score = (depth, len(url), url)
                if kind not in best or score < best[kind]:
                    best[kind] = score
    return {k: v[2] for k, v in best.items()}


def text_hash(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return hashlib.md5(" ".join(text.split()).encode()).hexdigest()


DNS_FAIL = ("nodename", "name or service", "name resolution", "getaddrinfo", "no address associated")


class PublicDNSFallback(httpcore.AsyncNetworkBackend):
    """Connects as usual; if this computer's DNS can't find a host, asks public DNS (Google, then Cloudflare) over
    HTTPS and connects to that address. TLS still checks the certificate against the real host name, so this is as
    safe as a normal connection. Some routers and Mac DNS caches fail on real domains; this keeps them from being
    reported as unreachable."""

    def __init__(self):
        self.inner = httpcore.AnyIOBackend()
        self.addresses: dict[str, str | None] = {}
        self.used: set[str] = set()
        self._doh = httpx.AsyncClient(timeout=8)

    async def _lookup(self, host: str) -> str | None:
        if host not in self.addresses:
            self.addresses[host] = None
            for url in (f"https://dns.google/resolve?name={host}&type=A", f"https://cloudflare-dns.com/dns-query?name={host}&type=A"):
                try:
                    data = (await self._doh.get(url, headers={"Accept": "application/dns-json"})).json()
                except Exception:  # noqa: BLE001
                    continue
                ips = [a["data"] for a in data.get("Answer", []) if a.get("type") == 1]
                if ips:
                    self.addresses[host] = ips[0]
                    break
        return self.addresses[host]

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host not in self.addresses:  # hosts that already failed locally go straight to public DNS
            try:
                return await self.inner.connect_tcp(host, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as e:
                if not any(s in str(e).lower() for s in DNS_FAIL):
                    raise
        ip = await self._lookup(host)
        if not ip:
            raise httpcore.ConnectError(f"[Errno 8] nodename nor servname provided, or not known: {host}")
        self.used.add(host)
        return await self.inner.connect_tcp(ip, port, timeout, local_address, socket_options)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return await self.inner.connect_unix_socket(path, timeout, socket_options)

    async def sleep(self, seconds):
        await self.inner.sleep(seconds)

    async def aclose(self):
        await self._doh.aclose()


class Crawler:
    def __init__(self, max_requests: int = 200, timeout: float = 15.0, respect_robots: bool = True):
        self.sem = asyncio.Semaphore(max_requests)
        self.respect_robots = respect_robots
        self.client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"},
            limits=httpx.Limits(max_connections=max_requests, max_keepalive_connections=max_requests // 4),
        )
        # httpx has no option for a custom resolver, so the fallback is set on its connection pool
        self.dns = PublicDNSFallback()
        self.client._transport._pool._network_backend = self.dns
        self.requests = 0

    async def close(self):
        await self.client.aclose()
        await self.dns.aclose()

    async def fetch(self, url: str) -> tuple[Fetched | None, str | None]:
        async with self.sem:
            self.requests += 1
            counter = _REQ_COUNT.get(None)
            if counter is not None:
                counter[0] += 1
            try:
                async with self.client.stream("GET", url) as r:
                    chunks, size = [], 0
                    async for chunk in r.aiter_bytes():
                        size += len(chunk)
                        chunks.append(chunk)
                        if size > MAX_BYTES:
                            break
                    body = b"".join(chunks)
                    enc = r.charset_encoding or "utf-8"
                    try:
                        text = body.decode(enc, errors="replace")
                    except LookupError:
                        text = body.decode("utf-8", errors="replace")
                    keep = {k: v for k, v in r.headers.items() if k.lower() in (
                        "server", "x-powered-by", "via", "x-shopify-stage", "x-wix-request-id", "x-generator", "cf-mitigated")}
                    return Fetched(str(r.url), r.status_code, r.headers.get("content-type", ""), text, keep), None
            except httpx.TimeoutException:
                return None, "timeout"
            except httpx.ConnectError as e:
                msg = str(e).lower()
                if "ssl" in msg or "certificate" in msg:
                    return None, "ssl error"
                if any(s in msg for s in DNS_FAIL):
                    return None, "domain does not resolve"
                return None, "cannot connect"
            except Exception as e:  # noqa: BLE001  (bad redirects, broken encodings, etc.)
                return None, f"{type(e).__name__}: {str(e)[:120]}"

    async def _public_dns(self, domain: str) -> str:
        """Ask public DNS (Google, then Cloudflare) over HTTPS. Returns exists, nxdomain or unknown."""
        for url in (f"https://dns.google/resolve?name={domain}&type=A",
                    f"https://cloudflare-dns.com/dns-query?name={domain}&type=A"):
            try:
                r = await self.client.get(url, headers={"Accept": "application/dns-json"}, timeout=8)
                data = r.json()
            except Exception:  # noqa: BLE001
                continue
            if data.get("Status") == 0 and data.get("Answer"):
                return "exists"
            if data.get("Status") == 3:
                return "nxdomain"
        return "unknown"

    async def _robots(self, base: str) -> tuple[RobotFileParser | None, list[str]]:
        f, _ = await self.fetch(urljoin(base, "/robots.txt"))
        if not f or f.status != 200 or "<html" in f.text[:500].lower():
            return None, []
        rp = RobotFileParser()
        rp.parse(f.text.splitlines())
        sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", f.text)
        return rp, sitemaps

    async def _sitemap_urls(self, base: str, listed: list[str]) -> list[str]:
        queue = (listed or [urljoin(base, "/sitemap.xml")])[:2]
        urls: list[str] = []
        for sm in queue:
            f, _ = await self.fetch(sm)
            if not f or f.status != 200:
                continue
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", f.text)
            children = [u for u in locs if u.endswith(".xml") or ".xml?" in u]
            if children and len(children) == len(locs):
                # sitemap index: read the most "page-like" child sitemap
                children.sort(key=lambda u: (not re.search(r"page|main|static|site", u), len(u)))
                f2, _ = await self.fetch(children[0])
                if f2 and f2.status == 200:
                    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", f2.text)
            urls.extend(htmllib.unescape(u) for u in locs[:5000])
            if urls:
                break
        return urls

    async def _get_page(self, url: str, home_hash: str, robots: RobotFileParser | None, record: dict) -> Fetched | None:
        if self.respect_robots and robots is not None and not robots.can_fetch("*", url):
            record["robots_blocked"].append(url)
            return None
        f, _ = await self.fetch(url)
        if not f or f.status >= 400 or "html" not in f.ctype:
            return None
        # soft 404s: redirected home, same content as home, or a "not found" title
        if urlparse(f.url).path.strip("/") == "" and urlparse(url).path.strip("/") != "":
            return None
        if text_hash(f.text) == home_hash or re.search(r"\b404\b|not found", page_title(f.text), re.I):
            return None
        return f

    async def _jobs(self, provider: str, slug: str) -> list[dict] | None:
        api = {
            "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
            "lever": f"https://api.lever.co/v0/postings/{slug}?mode=json",
            "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            "workable": f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
        }.get(provider)
        if not api:
            return None
        f, _ = await self.fetch(api)
        if not f or f.status != 200:
            return None
        try:
            data = json.loads(f.text)
        except ValueError:
            return None
        return normalize_jobs(provider, data)

    async def crawl(self, domain: str, kinds: set[str]) -> tuple[dict, dict[str, str], list[dict]]:
        """Phase 1: load the homepage, then only the page kinds asked for, plus open jobs."""
        record = {"domain": domain, "status": None, "error": None, "final_url": None, "pages": {},
                  "ats": None, "ats_slug": None, "jobs_count": None, "headers": {}, "robots_blocked": [], "requests": 0}
        counter = [0]
        _REQ_COUNT.set(counter)
        home, err = None, None
        for url in (f"https://{domain}/", f"https://www.{domain}/", f"http://{domain}/"):
            f, e = await self.fetch(url)
            if f and f.status < 400 and "html" in f.ctype and not BLOCK_TITLES.search(page_title(f.text)):
                home = f
                break
            if f:
                blocked = f.status in (401, 403, 405, 406, 429, 503) or BLOCK_TITLES.search(page_title(f.text)) or f.headers.get("cf-mitigated")
                err = "blocked by bot protection" if blocked else f"http {f.status}"
            else:
                err = e
        if not home:
            # dead = provably gone. Anything else (timeouts, odd status codes) is "unreachable": unknown, retry later
            if err == "domain does not resolve":
                # local DNS servers sometimes fail on real domains; ask public DNS before calling it dead
                public = await self._public_dns(domain)
                if public == "exists":
                    err = "local DNS couldn't find this domain but public DNS can (network DNS problem); retry later"
                elif public == "unknown":
                    err = "domain did not resolve and public DNS couldn't confirm; retry later"
            if err == "blocked by bot protection":
                record["status"] = "blocked"
            elif err in ("domain does not resolve", "http 404", "http 410"):
                record["status"] = "dead"
            else:
                record["status"] = "unreachable"
            record["error"] = err
            record["requests"] = counter[0]
            return record, {}, []

        record["status"] = "live"
        record["final_url"] = home.url
        record["headers"] = home.headers
        pages = await self._fetch_kinds(domain, home.url, home.text, kinds, {}, record, guess=True)
        pages = {"home": home.text, **pages}

        # job board
        all_html = " ".join(pages.values())
        jobs: list[dict] = []
        for provider, pattern in ATS_PATTERNS:
            slugs = [s for s in pattern.findall(all_html) if s.lower() not in BAD_SLUGS]
            if slugs:
                record["ats"], record["ats_slug"] = provider, slugs[0]
                result = await self._jobs(provider, slugs[0])
                if result is not None:
                    jobs = result
                    record["jobs_count"] = len(jobs)
                break

        record["pages"] = {"home": home.url, **record["pages"]}
        record["requests"] = counter[0]
        return record, pages, jobs

    async def crawl_more(self, record: dict, home_html: str, kinds: set[str]) -> dict[str, str]:
        """Phase 2: fetch extra page kinds for a company phase 1 already downloaded, starting from its saved homepage."""
        counter = [0]
        _REQ_COUNT.set(counter)
        have = set(record.get("pages", {}))
        want = {k for k in kinds if k not in have}
        pages = {}
        if want and record.get("final_url"):
            pages = await self._fetch_kinds(record["domain"], record["final_url"], home_html, want, have, record, guess=False)
        record["requests"] = record.get("requests", 0) + counter[0]
        record["requests_phase2"] = counter[0]
        return pages

    async def _fetch_kinds(self, domain: str, home_url: str, home_html: str, kinds: set[str], have: set,
                           record: dict, guess: bool) -> dict[str, str]:
        """Find and download the wanted page kinds: homepage links, then sitemap, then one level deeper, then common paths."""
        hosts = {strip_www(urlparse(home_url).hostname), domain}

        def internal(links):
            return [(u, a) for u, a in links if strip_www(urlparse(u).hostname) in hosts
                    or any(strip_www(urlparse(u).hostname).endswith("." + h) for h in hosts)]

        got: dict[str, Fetched] = {}
        home_hash = text_hash(home_html)
        robots, listed_sitemaps = await self._robots(home_url)
        chosen = {k: u for k, u in classify(internal(extract_links(home_html, home_url))).items() if k in kinds}

        if any(k not in chosen for k in kinds if k in KEY_KINDS) or (not guess and len(chosen) < len(kinds)):
            sm = await self._sitemap_urls(home_url, listed_sitemaps)
            for kind, url in classify(internal([(u, "") for u in sm])).items():
                if kind in kinds:
                    chosen.setdefault(kind, url)

        async def grab(kind, url):
            f = await self._get_page(url, home_hash, robots, record)
            if f:
                got[kind] = f

        await asyncio.gather(*(grab(k, u) for k, u in chosen.items()))

        # one level deeper: about/careers/footer pages often link team, jobs and legal pages
        found_links = []
        for f in list(got.values()):
            found_links.extend(extract_links(f.text, f.url))
        deeper = {k: u for k, u in classify(internal(found_links)).items()
                  if k in kinds and k in SECOND_HOP and k not in got and k not in have}
        await asyncio.gather(*(grab(k, u) for k, u in deeper.items()))

        # last resort: common paths for key pages
        async def try_paths(kind):
            for path in GUESSES[kind]:
                f = await self._get_page(urljoin(home_url, path), home_hash, robots, record)
                if f:
                    got[kind] = f
                    return
        if guess:
            await asyncio.gather(*(try_paths(k) for k in KEY_KINDS if k in kinds and k not in got))

        record.setdefault("pages", {}).update({k: f.url for k, f in got.items()})
        return {k: f.text for k, f in got.items()}


def _plain(html_text: str | None) -> str:
    if not html_text:
        return ""
    text = htmllib.unescape(html_text)
    try:
        text = HTMLParser(text).text(separator=" ")
    except Exception:
        text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())[:4000]


def normalize_jobs(provider: str, data) -> list[dict]:
    jobs = []
    if provider == "greenhouse":
        for j in (data or {}).get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            jobs.append({"title": j.get("title", ""), "location": loc, "remote": "remote" in loc.lower(),
                         "description": _plain(j.get("content"))})
    elif provider == "lever":
        for j in data if isinstance(data, list) else []:
            cats = j.get("categories") or {}
            loc = cats.get("location", "") or ""
            desc = " ".join([j.get("descriptionPlain", "") or ""] + [
                _plain(x.get("content")) for x in j.get("lists", []) or []] + [j.get("additionalPlain", "") or ""])
            jobs.append({"title": j.get("text", ""), "location": loc,
                         "remote": j.get("workplaceType") == "remote" or "remote" in loc.lower(),
                         "description": " ".join(desc.split())[:4000]})
    elif provider == "ashby":
        for j in (data or {}).get("jobs", []):
            loc = j.get("location", "") or ""
            jobs.append({"title": j.get("title", ""), "location": loc,
                         "remote": bool(j.get("isRemote")) or "remote" in loc.lower(),
                         "description": (j.get("descriptionPlain") or _plain(j.get("descriptionHtml")))[:4000]})
    elif provider == "workable":
        for j in (data or {}).get("jobs", []):
            loc = ", ".join(x for x in [j.get("city"), j.get("country")] if x)
            jobs.append({"title": j.get("title", ""), "location": loc, "remote": bool(j.get("telecommuting")),
                         "description": _plain(j.get("description"))})
    return jobs[:200]


# ---------------------------------------------------------------------------
# Slimming: keep only what the rules read, so saved pages stay small.
KEEP_ATTRS = {"href", "src", "type", "content", "name", "property", "rel", "itemprop", "itemtype", "alt", "title",
              "id", "action", "data-src"}
DROP_TAGS = "style, svg, noscript, template, link[rel='preload'], link[rel='stylesheet'], link[rel='icon'], meta[name='viewport'], path, picture source"


def slim_html(html: str) -> str:
    """Remove styles, inline scripts, icons and unused attributes. Keeps text, links,
    script/iframe sources, JSON-LD structured data and page structure (header, footer, h1)."""
    try:
        tree = HTMLParser(html)
    except Exception:
        return html
    for n in tree.css(DROP_TAGS):
        n.decompose()
    for n in tree.css("script"):
        if (n.attributes.get("type") or "").lower() == "application/ld+json":
            continue
        src = n.attributes.get("src")
        if src:
            n.replace_with(HTMLParser(f'<script src="{htmllib.escape(src)}"></script>').head.child)
        else:
            n.decompose()
    for n in tree.root.traverse():
        attrs = n.attributes
        for k in [k for k in attrs if k not in KEEP_ATTRS]:
            del n.attrs[k]
        for k in ("src", "href", "content"):
            v = n.attributes.get(k)
            if v and v.startswith("data:"):
                del n.attrs[k]
    out = tree.html or html
    out = re.sub(r"<!--.*?-->", "", out, flags=re.S)
    return re.sub(r"\n\s*\n+", "\n", out)
