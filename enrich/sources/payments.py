"""Payment gateways found in a company's own code: every way a site or app wires up Stripe (and the other gateways).

Text on a page ("payments are processed by Stripe") is a statement; this is proof in code. Checked at download time,
before pages are slimmed (inline scripts and data attributes are dropped then), on:
    1 the raw HTML of every page downloaded
    2 the site's own JavaScript bundles (same domain or its subdomains/CDN), where loadStripe() usually lives
    3 the product app's sign-in or sign-up page (app.<domain>, /login...) and its bundles, the usual home of billing code
Findings are saved in the crawl record (record["payment_code"]) and read by the rules (F2 Stripe, M3 fintech tools).
Old saved pages are still checked by the rules, but slimmed pages only keep script sources and links.
"""
from __future__ import annotations

import re

# gateway -> [(how it shows in code, pattern)], strongest first. Publishable keys only count next to the gateway's
# own name: Clerk, PostHog and others also use pk_live_ keys.
GATEWAYS: dict[str, list[tuple[str, str]]] = {
    "Stripe": [
        ("Stripe.js loaded", r"js\.stripe\.com/v\d|js\.stripe\.com/(?:basil|acacia|dahlia|clover)?/?stripe\.js"),
        ("Stripe initialised with a publishable key", r"\bStripe\s*\(\s*[\"'`]pk_(?:live|test)_"),
        ("loadStripe() in the app code", r"\bloadStripe\s*\("),
        ("Stripe Elements / Payment Element", r"\bstripe\.elements\s*\(|\bcreatePaymentMethod\s*\(|\bconfirmCardPayment\s*\(|\bconfirmPayment\s*\("),
        ("Stripe pricing table or buy button", r"<stripe-(?:pricing-table|buy-button)\b"),
        ("Stripe Checkout link", r"checkout\.stripe\.com/"),
        ("Stripe Payment Link", r"buy\.stripe\.com/"),
        ("Stripe customer billing portal", r"billing\.stripe\.com/"),
        ("Stripe hosted invoice", r"invoice\.stripe\.com/|pay\.stripe\.com/"),
        ("Stripe Connect", r"connect\.stripe\.com/"),
        ("Stripe fraud-detection frame", r"m\.stripe\.network|m\.stripe\.com"),
        ("Stripe API called from code", r"api\.stripe\.com/v1"),
        ("data-stripe attributes", r"\bdata-stripe(?:-[a-z]+)?="),
        ("Stripe publishable key", r"stripe[^<>\n]{0,120}?pk_(?:live|test)_[A-Za-z0-9]{10,}|pk_(?:live|test)_[A-Za-z0-9]{10,}[^<>\n]{0,60}?stripe"),
    ],
    "Paddle": [("Paddle.js loaded", r"cdn\.paddle\.com/paddle"), ("Paddle initialised", r"\bPaddle\.(?:Setup|Initialize|Checkout\.open)\s*\(")],
    "Chargebee": [("Chargebee.js loaded", r"js\.chargebee\.com"), ("Chargebee initialised", r"\bChargebee\.init\s*\("),
                  ("Chargebee hosted page", r"\.chargebee\.com/hosted_pages")],
    "Braintree": [("Braintree loaded", r"js\.braintreegateway\.com|braintree-web"), ("Braintree drop-in", r"\bbraintree\.dropin\.create\s*\(")],
    "PayPal": [("PayPal SDK loaded", r"paypal\.com/sdk/js|paypalobjects\.com/api/checkout"), ("PayPal buttons", r"\bpaypal\.Buttons\s*\(")],
    "Square": [("Square Web Payments loaded", r"web\.squarecdn\.com|js\.squareup\.com"), ("Square checkout link", r"square\.link/|checkout\.square\.site")],
    "Lemon Squeezy": [("Lemon Squeezy loaded", r"app\.lemonsqueezy\.com/js|assets\.lemonsqueezy\.com"),
                      ("Lemon Squeezy checkout", r"\.lemonsqueezy\.com/(?:checkout|buy)/")],
    "Recurly": [("Recurly.js loaded", r"js\.recurly\.com")],
    "Adyen": [("Adyen checkout loaded", r"checkoutshopper-(?:live|test)\.adyen\.com")],
    "Authorize.net": [("Authorize.net Accept.js", r"js\.authorize\.net|jstest\.authorize\.net|accept\.authorize\.net")],
    "Checkout.com": [("Checkout.com Frames", r"cdn\.checkout\.com/js/framesv2")],
    "Razorpay": [("Razorpay checkout", r"checkout\.razorpay\.com")],
    "Gumroad": [("Gumroad overlay", r"gumroad\.com/js/gumroad|gumroad\.com/l/")],
}
_COMPILED = {g: [(how, re.compile(p, re.I)) for how, p in pats] for g, pats in GATEWAYS.items()}
NOT_STRIPE_KEY = re.compile(r"clerk|posthog|publishable[-_]?key=[\"']pk_(?:live|test)_[^\"']*[\"'][^>]*clerk", re.I)

# links that lead to the product app's sign-in / sign-up page
RE_APP_LINK = re.compile(r"href=[\"']([^\"'#]+)[\"']", re.I)
RE_APP_HINT = re.compile(r"(?:^https?://(?:app|dashboard|my|portal|console|account)\.)|/(?:login|log-in|signin|sign-in|signup|sign-up|register)\b", re.I)
RE_SCRIPT_SRC = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.I)


def detect(text: str, where: str) -> list[dict]:
    """Every gateway method found in this page or script. `where` is its URL."""
    out = []
    for gateway, pats in _COMPILED.items():
        for how, rx in pats:
            m = rx.search(text)
            if not m:
                continue
            snippet = re.sub(r"\s+", " ", text[max(0, m.start() - 60):m.end() + 60]).strip()
            if how == "Stripe publishable key" and NOT_STRIPE_KEY.search(snippet):
                continue
            snippet = re.sub(r"(pk_(?:live|test)_)[A-Za-z0-9]+", r"\1…", snippet)  # never store keys
            out.append({"gateway": gateway, "how": how, "where": where, "snippet": snippet[:220]})
    return out


def summarize(findings: list[dict]) -> dict[str, dict]:
    """Per gateway: the methods found and the first page or script that shows it."""
    out: dict[str, dict] = {}
    for f in findings:
        g = out.setdefault(f["gateway"], {"methods": [], "where": f["where"], "snippet": f["snippet"]})
        if f["how"] not in g["methods"]:
            g["methods"].append(f["how"])
    return out


def own_scripts(html: str, base_url: str, hosts: set[str], limit: int = 6) -> list[str]:
    """The site's own JavaScript files (same domain, its subdomains or its CDN subdomain), biggest bundles first."""
    from urllib.parse import urljoin, urlparse
    out = []
    for src in RE_SCRIPT_SRC.findall(html):
        url = urljoin(base_url, src)
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if any(host == h or host.endswith("." + h) for h in hosts) and url not in out:
            out.append(url)
    # framework bundles (_next/static/chunks, assets/index-*.js) hold the app code; analytics snippets don't
    out.sort(key=lambda u: (not re.search(r"_next/static|/assets/|/static/js/|chunk|bundle|main|app", u), u))
    return out[:limit]


def app_pages(html: str, base_url: str, hosts: set[str], limit: int = 2) -> list[str]:
    """Links to the product app's sign-in or sign-up page on the company's own domain."""
    from urllib.parse import urljoin, urlparse
    out = []
    for href in RE_APP_LINK.findall(html):
        url = urljoin(base_url, href)
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if any(host == h or host.endswith("." + h) for h in hosts) and RE_APP_HINT.search(url) and url not in out:
            out.append(url)
    return out[:limit]
