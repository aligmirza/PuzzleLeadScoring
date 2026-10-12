"""DNS check: the company's public DNS records, read for free from public DNS-over-HTTPS (Cloudflare, then Google).

Companies prove they own their domain to the SaaS tools they sign up for by adding a TXT record, and list their email
senders in SPF. Those records name the vendor, so they are proof the company has an account there:
    rippling-domain-verification=...     a Rippling account (HR, payroll, IT)
    stripe-verification=...              a Stripe account
    v=spf1 include:...hubspotemail.net   HubSpot sends its email
Two requests per company (TXT and MX), no key, no cost. Website technologies use the same records through the
Wappalyzer fingerprints (sources/wappalyzer.py). Run as part of the directories layer (step 2).
"""
from __future__ import annotations

import re

import httpx

DOH = ["https://cloudflare-dns.com/dns-query", "https://dns.google/resolve"]

# Puzzle-relevant tools, matched only when the record itself names the vendor
TOOLS = {
    "Rippling": r"^rippling[-_]", "Gusto": r"^gusto[-_]", "Deel": r"^deel[-_]", "Stripe": r"^stripe[-_]",
    "Brex": r"^brex[-_]", "Ramp": r"^ramp[-_](?:domain|site)?[-_]?verification", "Mercury": r"^mercury[-_]",
    "Carta": r"^carta[-_]", "Pulley": r"^pulley[-_]", "QuickBooks Online": r"^(?:quickbooks|qbo)[-_]",
    "Justworks": r"^justworks[-_]", "TriNet": r"^trinet[-_]", "Bill.com": r"^bill(?:com)?[-_]verification",
    "Expensify": r"^expensify[-_]",
}
RE_VERIFY = re.compile(r"^\"?([a-z0-9][a-z0-9._-]*?)[-_](?:domain[-_]|site[-_])?verification(?:[-_=:]|$)", re.I)
RE_SPF_INCLUDE = re.compile(r"include:([^\s\"]+)", re.I)
MAIL = {"google.com": "Google Workspace", "googlemail.com": "Google Workspace", "outlook.com": "Microsoft 365",
        "protection.outlook.com": "Microsoft 365", "zoho": "Zoho Mail", "messagingengine.com": "Fastmail",
        "pphosted.com": "Proofpoint", "mimecast": "Mimecast"}


def _query(c: httpx.Client, name: str, rtype: str) -> list[str]:
    last = None
    for url in DOH:
        try:
            r = c.get(url, params={"name": name, "type": rtype}, headers={"accept": "application/dns-json"})
            r.raise_for_status()
            return [a["data"].strip('"').replace('" "', "") for a in r.json().get("Answer", []) if a.get("data")]
        except Exception as e:  # noqa: BLE001  try the next resolver
            last = e
    raise RuntimeError(f"DNS lookup failed for {name} {rtype}: {last!r}"[:200])


def lookup(domain: str) -> dict:
    with httpx.Client(timeout=10) as c:
        txt, mx = _query(c, domain, "TXT"), _query(c, domain, "MX")
    tools, verified = {}, []
    for rec in txt:
        low = rec.strip('"').lower()
        m = RE_VERIFY.match(low)
        if m:
            verified.append(m.group(1))
        for tool, rx in TOOLS.items():
            if re.search(rx, low) and tool not in tools:
                tools[tool] = rec[:120]
    spf = [inc for rec in txt if rec.lower().startswith("v=spf1") for inc in RE_SPF_INCLUDE.findall(rec)]
    mail = next((name for rec in mx for key, name in MAIL.items() if key in rec.lower()), "")
    return {"txt": txt[:30], "mx": mx[:10], "spf_includes": spf, "verified_with": sorted(set(verified)),
            "tools": tools, "email": mail}


def proof_url(domain: str) -> str:
    """A page anyone can open to see the same records."""
    return f"https://dns.google/query?name={domain}&rr_type=TXT"
