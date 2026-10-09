"""Rule-based extraction from saved pages and jobs. No AI, no cost.

Every signal gets one of:
  yes      proof found
  no       proof of the opposite found
  unknown  nothing found (never counts against the company)
  check    a clue was found but a person or AI must confirm it

Signal IDs match SIGNALS.md.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from . import prompts as prompt_files

YES, NO, UNKNOWN, CHECK = "yes", "no", "unknown", "check"

SIGNALS = {
    # must-haves
    "M1": ("must", "Based and operating in the US"),
    "M2": ("must", "Live website with a real product"),
    "M3": ("must", "Uses a fintech tool Puzzle connects to"),
    "M4": ("must", "Real transaction activity"),
    # exclusions
    "E1": ("exclusion", "Series C or later, or public"),
    "E2": ("exclusion", "New company, no website, no funding"),
    "E3": ("exclusion", "Accounting or bookkeeping firm"),
    "E4": ("exclusion", "Mainly operates outside the US"),
    "E5": ("exclusion", "Non-profit or government body"),
    "E6": ("exclusion", "E-commerce holding physical stock"),
    # fit
    "F1": ("fit", "Venture-backed, pre-seed to Series B"),
    "F2": ("fit", "Uses Stripe for billing"),
    "F3": ("fit", "Delaware C-Corp"),
    "F4": ("fit", "Uses QuickBooks Online"),
    "F5": ("fit", "Startup selling nationally"),
    "F6": ("fit", "B2B SaaS or tech-enabled service"),
    "F7": ("fit", "Local professional services firm"),
    "F8": ("fit", "Publishes real paid pricing"),
    "F9": ("fit", "Payroll on Gusto, Rippling or Deel"),
    "F10": ("fit", "AI is core to the product"),
    "F11": ("fit", "YC or similar accelerator"),
    "F12": ("fit", "Several open roles"),
    "F13": ("fit", "No finance person visible"),
    "F14": ("fit", "Has a finance lead (mid-size and up)"),
    "F15": ("fit", "Publishes revenue or growth numbers"),
    "F16": ("fit", "Remote-first or hybrid"),
    "F17": ("fit", "Runs several US companies"),
    "F18": ("fit", "Early adopter"),
    "F19": ("fit", "Banks with Mercury or Brex"),
    "F20": ("fit", "Uses Ramp or Brex cards"),
    "F21": ("fit", "Cap table in Carta or Pulley"),
    # buying
    "B3": ("buying", "Hiring for several roles"),
    "B4": ("buying", "Hiring a finance lead"),
    "B5": ("buying", "Hiring ops or chief of staff"),
    "B9": ("buying", "Lost or replacing a bookkeeper"),
    "B10": ("buying", "Tax deadline coming up"),
    "B14": ("buying", "Preparing for a first audit"),
    # weak
    "W1": ("weak", "Serves one local area"),
    "W2": ("weak", "Sells hours of labour, not a product"),
    "W3": ("weak", "Physical or single-location business"),
    "W4": ("weak", "LLC or sole owner, no venture funding"),
    "W5": ("weak", "Not making money yet"),
    "W6": ("weak", "Needs more than one currency"),
    "W7": ("weak", "Manufacturing or hardware"),
    "W8": ("weak", "Heavy invoicing and bill-paying"),
    "W9": ("weak", "Shopify store without stock"),
    "W10": ("weak", "Already on NetSuite or similar"),
}

GROUP_NAMES = {"must": "Must-have", "exclusion": "Exclusion", "fit": "Fit", "buying": "Buying signal", "weak": "Weak fit"}
VALUE_NAMES = {"yes": "Yes", "no": "No", "check": "Needs check", "unknown": "Unknown"}
CELL_NAMES = {**VALUE_NAMES, "unknown": ""}  # CSV cells: left empty until something is confirmed
GATE_NAMES = {"pass": "Pass", "pass (some unknown)": "Pass (some checks unknown)", "failed must-have": "Failed a must-have",
              "excluded": "Excluded", "not reached (retry)": "Site not reached (retry)"}


def signal_column(sid: str) -> str:
    group, label = SIGNALS[sid]
    return f"{GROUP_NAMES[group]}: {label}"


# AI prompts live in prompts/<group>/<data point>.json. These run for every company still in the running;
# others are added when a rule returns "Needs check" or a condition in Extractor._ai_prompts matches.
ALWAYS_PROMPTS = [
    "fit/startup_selling_nationally", "fit/b2b_saas_or_tech_service", "fit/local_professional_services",
    "weak/billed_labour_not_product", "fields/vertical", "fit/ai_core_to_product",
    "weak/manufacturing_hardware", "weak/heavy_ar_ap",
]

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
STATE_NAMES = set(US_STATES.values())
_ABBR = "|".join(US_STATES)
_NAMES = "|".join(sorted(STATE_NAMES, key=len, reverse=True))
RE_US_ADDR = re.compile(rf"\b(?:{_ABBR}|{_NAMES}),?\s+\d{{5}}(?:-\d{{4}})?\b")
RE_GOVLAW = re.compile(r"(?:laws? of|governed by)\s+(?:the\s+)?(?:State of\s+|Commonwealth of\s+|Province of\s+)?([A-Z][a-zA-Z]+(?:\s(?:and\s)?[A-Z][a-z]+)?)")
RE_STATE_ENTITY = re.compile(rf"\b(?:an?|the)\s+({_NAMES})\s+(public benefit corporation|corporation|limited liability company|C[- ]?corp(?:oration)?)\b", re.I)
RE_ENTITY = re.compile(
    r"([A-Z][\w&'.\-]*(?:\s+(?:[A-Z][\w&'.\-]*|of|and|&)){0,5}),?\s+"
    r"(Inc\.?|Incorporated|Corp\.?|Corporation|LLC|L\.L\.C\.|PBC|Ltd\.?|Limited|GmbH|S\.A\.S\.?|SAS|B\.V\.|BV|"
    r"Pty\.?\s?Ltd\.?|PLC|plc|AG|S\.r\.l\.|SRL|Oy|AB|ApS|Pvt\.?\s?Ltd\.?|Private Limited)(?![A-Za-z])")
SUFFIX_KIND = [
    (r"^(Inc|Incorporated|Corp|Corporation)", "corp"),
    (r"^(LLC|L\.L\.C)", "llc"),
    (r"^PBC", "pbc"),
]
FOREIGN_JURIS = {
    "England", "England and Wales", "Wales", "Scotland", "United Kingdom", "Ireland", "Germany", "France", "India",
    "Canada", "Ontario", "British Columbia", "Quebec", "Alberta", "Australia", "New South Wales", "Singapore", "Israel",
    "Netherlands", "Spain", "Sweden", "Switzerland", "Estonia", "Poland", "Brazil", "Mexico", "Japan", "Hong Kong",
    "Denmark", "Norway", "Finland", "Italy", "Portugal", "Belgium", "Austria", "New Zealand", "Cayman Islands",
    "Pakistan", "Lithuania", "Latvia", "Ukraine", "Romania", "Greece", "Cyprus", "Malta", "Luxembourg", "Nigeria",
    "Kenya", "South Africa", "Argentina", "Colombia", "Chile", "Philippines", "Vietnam", "Indonesia", "Turkey",
    "Egypt", "United Arab Emirates", "Dubai", "Czech Republic", "Bulgaria", "Hungary", "Croatia", "Serbia",
}
RE_FOREIGN_PLACE = re.compile(r"\b(" + "|".join(sorted(FOREIGN_JURIS | {
    "London", "Berlin", "Munich", "Paris", "Bangalore", "Bengaluru", "Mumbai", "Delhi", "Toronto", "Vancouver",
    "Montreal", "Sydney", "Melbourne", "Amsterdam", "Madrid", "Barcelona", "Stockholm", "Zurich", "Tallinn",
    "Warsaw", "Lisbon", "Dublin", "Tel Aviv", "Karachi", "Lahore", "Lagos", "Nairobi", "Cape Town", "Bogota",
    "Buenos Aires", "Sao Paulo", "Manila", "Jakarta", "Istanbul", "Cairo", "Copenhagen", "Oslo", "Helsinki",
    "Brussels", "Vienna", "Prague", "Kyiv", "Bucharest"}, key=len, reverse=True)) + r")\b")
STRONG_FOREIGN = ("foreign legal entity", "governing law", "structured data address country")
FOREIGN_TLDS = (".uk", ".de", ".fr", ".in", ".ca", ".au", ".nl", ".es", ".it", ".se", ".ch", ".pk", ".br", ".mx",
                ".pl", ".pt", ".ie", ".dk", ".no", ".fi", ".be", ".at", ".nz", ".sg", ".jp", ".cn", ".ru", ".za",
                ".ng", ".ke", ".ae", ".il", ".tr", ".ar", ".cl", ".ee", ".lt", ".lv", ".cz", ".ro", ".gr", ".hu")

RE_PRICE = re.compile(
    r"(?:US\$|\$)\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:USD)?\s*(?:/|per|a|an)\s*"
    r"(?:mo\b|month|yr\b|year|annum|user|seat|member|editor|license|week|employee|location)", re.I)
RE_PRICE_LOOSE = re.compile(r"(?:US\$|\$)\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?(?=[^$]{0,60}\b(?:month|monthly|year|yearly|annually|/mo|/yr)\b)", re.I)
RE_FOREIGN_PRICE = re.compile(r"(?:€|£|₹|¥|A\$|C\$|R\$)\s?\d|\d\s?(?:€|£)|\b\d+(?:[.,]\d+)?\s?(?:EUR|GBP|INR|AUD|CAD)\b")
RE_CONTACT_ONLY = re.compile(r"contact (?:us|sales)|talk to (?:sales|us)|request a (?:quote|demo)|get a quote|custom pricing", re.I)
RE_WAITLIST = re.compile(r"join (?:the|our)? ?wait ?list|request (?:early )?access|get early access|coming soon|launching soon|private beta|join the beta|sign up for early access", re.I)

CHECKOUT_FP = {
    "Stripe checkout": r"(?:checkout|buy|billing)\.stripe\.com|js\.stripe\.com",
    "Paddle": r"cdn\.paddle\.com|paddle\.js",
    "Chargebee": r"js\.chargebee\.com|chargebee\.com/hosted",
    "Recurly": r"js\.recurly\.com",
    "Lemon Squeezy": r"lemonsqueezy\.com",
    "Gumroad": r"gumroad\.com/l/",
    "Shopify checkout": r"cdn\.shopify\.com|myshopify\.com",
}
STORE_FP = {
    "Shopify": r"cdn\.shopify\.com|Shopify\.theme|myshopify\.com",
    "WooCommerce": r"woocommerce|wc-cart",
    "BigCommerce": r"cdn\d*\.bigcommerce\.com",
    "Magento": r"Magento_|mage/cookies",
}
RE_POD = re.compile(r"printful|printify|gelato\.com|teespring|print[- ]on[- ]demand|gooten", re.I)
RE_DIGITAL = re.compile(r"instant download|digital download|downloadable|digital product", re.I)
RE_STOCK = re.compile(r"\bsold out\b|\bout of stock\b|\bin stock\b|\bonly \d+ left\b|\bback in stock\b", re.I)
RE_CART = re.compile(r"add to (?:cart|bag|basket)", re.I)

TOOL_RULES = {
    "Stripe": {"raw": r"js\.stripe\.com|(?:checkout|buy|billing)\.stripe\.com", "text": r"\bStripe\b"},
    "Gusto": {"raw": None, "text": r"\bGusto\b"},
    "Rippling": {"raw": r"ats\.rippling\.com", "text": r"\bRippling\b"},
    "Deel": {"raw": None, "text": r"\bDeel\b"},
    "Mercury": {"raw": None, "text": r"\bMercury\b(?=[^.]{0,40}\b(?:bank|banking|account|treasury)\b)|\b(?:bank|banking|banks)\b[^.]{0,40}\bMercury\b"},
    "Brex": {"raw": None, "text": r"\bBrex\b"},
    "Ramp": {"raw": None, "text": r"\bRamp\b(?!\s+up)"},
    "Carta": {"raw": None, "text": r"\bCarta\b"},
    "Pulley": {"raw": None, "text": r"\bPulley\b"},
    "Chase": {"raw": None, "text": r"\bChase (?:Bank|Business|for Business|business banking)\b|\bJPMorgan Chase\b"},
    "Wise": {"raw": None, "text": r"\bWise (?:Business|Platform)\b|\bTransferWise\b"},
}
COMPETITOR_RULES = {
    "QuickBooks Online": r"\bQuickBooks\b(?! Enterprise)|\bQBO\b",
    "QuickBooks Enterprise": r"\bQuickBooks Enterprise\b",
    "Xero": r"\bXero\b",
    "Wave": r"\bWave (?:Accounting|Financial|Apps)\b|waveapps",
    "Zoho Books": r"\bZoho Books\b",
    "Bench": r"\bBench (?:Accounting|bookkeeping)\b|bench\.co\b",
    "Pilot": r"\bPilot\.com\b|\bPilot (?:bookkeeping|accounting)\b",
    "NetSuite": r"\bNet[Ss]uite\b",
    "Rillet": r"\bRillet\b",
    "Kick": r"\bKick (?:bookkeeping|accounting)\b|\bkick\.co\b",
    "Inkle": r"\bInkle\b",
    "Pry": r"\bPry Financials\b|\bpry\.co\b",
}
USAGE_KINDS = {"privacy", "security", "terms", "legal"}
INTEGRATION_CONTEXT = re.compile(r"connect|integrat|sync|import|your [\w ]{0,20}account|plug(?:s|in)|(?<!we )works? (?:best |seamlessly |great )?with|supports?|partners?|investors?|backed by|financing", re.I)
USAGE_CONTEXT = re.compile(r"benefit|payroll|we use|our (?:\w+ )?stack|tools? (?:we|like)|experience (?:with|in|using)|familiar|proficien", re.I)

RE_FIN_ROLE = re.compile(r"\b(controller|comptroller|head of finance|vp,? finance|finance|financial|accountant|accounting|bookkeep\w*|fp&a|cfo|chief financial)\b", re.I)
RE_LEAD_FIN = re.compile(r"\b(controller|comptroller|head of finance|vp,? finance|director of finance|finance (?:lead|manager)|cfo|chief financial|first finance)\b", re.I)
RE_OPS_ROLE = re.compile(r"\b(operations|bizops|biz ops|business operations|chief of staff|office manager)\b", re.I)
RE_PEOPLE_ROLE = re.compile(r"\b(people|hr|human resources|talent)\b", re.I)
RE_MFG_ROLE = re.compile(r"manufactur|hardware engineer|mechanical engineer|supply chain|production (?:technician|associate|manager)|assembly|warehouse|fulfil?ment|inventory", re.I)
RE_AUDIT = re.compile(r"\b(?:first|annual|financial) audit|audit[- ]read|prepare for (?:an )?audit", re.I)

RE_FUND_SENT = re.compile(r"\b(raised|raise|raising|funding|round|backed by|investors? include|led by|seed|series [a-h])\b", re.I)
RE_STAGE = re.compile(r"\b(pre-?seed|seed|series ([a-h]))\b", re.I)
RE_FIRST_PERSON = re.compile(r"\b(we|we've|we're|our|us)\b", re.I)
RE_TICKER = re.compile(r"\b(?:NASDAQ|Nasdaq|NYSE(?: American)?)\s?:\s?[A-Z]{1,5}\b")
RE_YC_STRONG = re.compile(r"\b(?:backed by|funded by|part of|alum(?:ni)? of|graduated from|went through|participated in|member of)\s+(?:the\s+)?(?:Y Combinator|YC)\b"
                          r"|\b(?:Y Combinator|YC)[- ]backed\b|\bYC\s?[WSFX]\d{2}\b|\b(?:Y Combinator|YC)\s+\(?(?:Winter|Summer|Fall|Spring|[WSFX])\s?'?\d{2,4}\)?", re.I)
RE_YC_WEAK = re.compile(r"\bY Combinator\b")
RE_TECHSTARS_STRONG = re.compile(r"\b(?:backed by|part of|alum(?:ni)? of|graduated from|went through)\s+(?:the\s+)?Techstars\b|\bTechstars[- ]backed\b|\bTechstars\s+[A-Z][a-z]+\s+(?:Accelerator\s+)?'?\d{2,4}\b", re.I)
RE_METRIC = re.compile(
    r"\$\s?\d+(?:\.\d+)?\s?(?:M|MM|million|B|billion)\+?\s+(?:in\s+)?(?:ARR|annual recurring revenue|revenue|in sales)"
    r"|\b\d{1,3}(?:,\d{3})+\+?\s+(?:customers|companies|businesses|teams|paying)"
    r"|\b\d+(?:\.\d+)?[kKM]\+?\s+(?:customers|companies|businesses|teams|users)\b"
    r"|\bgrew\s+\d+(?:\.\d+)?\s?[%x]|\b\d+x\s+(?:growth|year[- ]over[- ]year)", re.I)
RE_REMOTE = re.compile(r"remote[- ]first|fully remote|remote[- ]friendly|distributed (?:team|company)|work from anywhere|\bhybrid\b", re.I)
RE_SHUTDOWN = re.compile(r"\b(?:is|are|will be)\s+(?:shutting down|winding down|sunsetting|closing (?:its|our) doors)"
                         r"|\bshutting down on\b|\bwe(?:'re| are| have| 've)\s+(?:shut down|closed (?:our|the) (?:doors|company))"
                         r"|\bceased operations\b", re.I)
RE_HOLDING = re.compile(r"a portfolio company of|\bHoldings?,? (?:Inc|LLC)|family of (?:companies|brands)", re.I)

RE_501 = re.compile(r"501\s?\(?c\)?\s?\(?3\)?", re.I)
RE_TAXDED = re.compile(r"tax[- ]deductible", re.I)
RE_CHARITY = re.compile(r"registered charity|charity (?:number|no\.|registration)", re.I)
RE_EIN = re.compile(r"\b(?:EIN|Tax ID|Federal Tax ID)\b[^0-9]{0,15}\d{2}-\d{7}\b")
RE_990 = re.compile(r"\bForm 990\b")
RE_SELLS_TO_NP = re.compile(r"discount|pricing|for (?:non-?profits|charities)|non-?profit (?:plan|pricing|customers|organizations? like)|our customers|clients", re.I)

RE_ACCOUNTING_FIRM = re.compile(
    r"\b(CPAs?|certified public accountants?|bookkeeping (?:services|firm|company)|accounting firm|accounting (?:services|practice)"
    r"|fractional CFOs?|outsourced (?:accounting|CFO|controller|bookkeeping)|tax (?:preparation|prep|services|advisory)|enrolled agent)\b")
RE_SERVICE_AREA = re.compile(r"areas? we serve|service areas?|serving the greater|proudly serving|locally owned|serving [A-Z][a-z]+ (?:and|&) (?:surrounding|nearby)", re.I)
RE_HOURS = re.compile(r"\b(?:Mon|Monday)\s?(?:-|–|to|thru)\s?(?:Fri|Friday|Sat|Saturday)\b[^.]{0,30}\d{1,2}(?::\d{2})?\s?(?:am|a\.m\.)", re.I)

LD_PHYSICAL = {"Restaurant", "CafeOrCoffeeShop", "BarOrPub", "Bakery", "FoodEstablishment", "Store", "ClothingStore",
               "HealthClub", "ExerciseGym", "BeautySalon", "HairSalon", "DaySpa", "NailSalon", "Dentist", "MedicalClinic",
               "Physician", "Plumber", "Electrician", "HVACBusiness", "RoofingContractor", "HomeAndConstructionBusiness",
               "GeneralContractor", "AutoRepair", "AutomotiveBusiness", "LodgingBusiness", "Hotel", "VeterinaryCare",
               "ChildCare", "Florist", "HomeGoodsStore", "SportsActivityLocation", "MedicalBusiness", "Optician",
               "Winery", "Brewery", "Farm"}
LD_LOCAL_SERVICE = {"LegalService", "Attorney", "RealEstateAgent", "ProfessionalService", "InsuranceAgency",
                    "EmploymentAgency", "LocalBusiness"}


# ---------------------------------------------------------------------------
def decide(signals: dict, site_status: str | None) -> dict:
    """The ICP verdict from must-have and exclusion answers. Used by the rules and again after AI answers."""
    def val(sid):
        return signals.get(sid, {}).get("value", UNKNOWN)
    excluded = [sid for sid, (g, _) in SIGNALS.items() if g == "exclusion" and val(sid) == YES]
    failed = [sid for sid, (g, _) in SIGNALS.items() if g == "must" and val(sid) == NO]
    confirmed = [sid for sid, (g, _) in SIGNALS.items() if g == "must" and val(sid) == YES]
    checks = [sid for sid, (g, _) in SIGNALS.items() if g in ("must", "exclusion") and val(sid) == CHECK]
    if site_status in ("blocked", "unreachable", "error"):
        gate = "not reached (retry)"
    elif excluded:
        gate = "excluded"
    elif failed:
        gate = "failed must-have"
    elif len(confirmed) == 4:
        gate = "pass"
    else:
        gate = "pass (some unknown)"
    if gate == "not reached (retry)":
        verdict = "Unknown: site not reached"
    elif gate in ("excluded", "failed must-have"):
        verdict = "No"
    elif checks:
        verdict = "Needs AI check"
    elif gate == "pass":
        verdict = "Yes"
    else:
        verdict = "Yes (some must-haves unconfirmed)"
    return {"gate": gate, "verdict": verdict, "checks": checks, "excluded": excluded, "failed": failed,
            "confirmed": confirmed,
            "exclusion_reason": "; ".join(f"{SIGNALS[sid][1]}: {signals[sid]['evidence']}" for sid in excluded)}


@dataclass
class Page:
    kind: str
    url: str
    html: str
    text: str
    title: str
    desc: str
    h1: str
    footer: str
    header: str
    ld: list


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _node_text(node) -> str:
    return _norm(node.text(separator=" ")) if node else ""


def _flatten_ld(obj, out: list):
    if isinstance(obj, list):
        for x in obj:
            _flatten_ld(x, out)
    elif isinstance(obj, dict):
        out.append(obj)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _flatten_ld(v, out)


def parse_page(kind: str, url: str, html: str) -> Page:
    tree = HTMLParser(html)
    ld: list = []
    for n in tree.css('script[type="application/ld+json"]'):
        try:
            _flatten_ld(json.loads(n.text()), ld)
        except Exception:
            pass
    title = _node_text(tree.css_first("title"))
    meta = tree.css_first('meta[name="description"]') or tree.css_first('meta[property="og:description"]')
    desc = (meta.attributes.get("content") or "") if meta else ""
    for n in tree.css("script, style, noscript, svg, template, iframe"):
        n.decompose()
    return Page(kind, url, html, _node_text(tree.body), title, _norm(desc), _node_text(tree.css_first("h1")),
                _node_text(tree.css_first("footer")), _node_text(tree.css_first("header") or tree.css_first("nav")), ld)


def _snip(text: str, m: re.Match, width: int = 90) -> str:
    a, b = max(0, m.start() - width), min(len(text), m.end() + width)
    return ("..." if a else "") + text[a:b].strip() + ("..." if b < len(text) else "")


def sig(value: str, evidence: str = "", url: str = "", note: str = "", method: str = "code") -> dict:
    return {"value": value, "evidence": evidence[:300], "url": url, "note": note, "method": method}


def _ld_types(ld: list) -> set[str]:
    types = set()
    for d in ld:
        t = d.get("@type")
        for x in t if isinstance(t, list) else [t]:
            if isinstance(x, str):
                types.add(x)
    return types


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ---------------------------------------------------------------------------
class Extractor:
    def __init__(self, domain: str, company: str, employees: str, record: dict, pages: dict[str, str], jobs: list[dict],
                 phase: str = "icp", directory_fn=None):
        self.phase = phase
        self.directory_fn = directory_fn  # (legal_name, us_clues, skip_sec) -> directory findings; see directories.py
        self.directories_found = None  # "icp": must-haves and exclusions only; "signals": everything
        self.domain, self.company, self.employees = domain, company, employees
        self.record, self.jobs = record, jobs
        order = ["home", "about", "team", "pricing", "careers", "privacy", "terms", "legal", "contact", "security",
                 "investors", "integrations", "customers", "blog", "engineering", "locations", "shipping", "donate", "menu"]
        urls = record.get("pages", {})
        self.pages: dict[str, Page] = {}
        for k in sorted(pages, key=lambda k: order.index(k) if k in order else 99):
            self.pages[k] = parse_page(k, urls.get(k, ""), pages[k])
        self.ld = [d for p in self.pages.values() for d in p.ld]
        self.ld_types = _ld_types(self.ld)
        self.raw_all = "\n".join(p.html for p in self.pages.values())
        self.label = domain.split(".")[0]
        self.s: dict[str, dict] = {}
        self.partners: dict[str, str] = {}
        self.competitors: dict[str, str] = {}
        self.competitor_mentions: dict[str, str] = {}
        self.ai: list[str] = []
        self.facts: dict = {}

    # helpers ---------------------------------------------------------
    def find(self, pattern, kinds=None, raw=False, where="text"):
        rx = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)
        for p in self.pages.values():
            if kinds and p.kind not in kinds:
                continue
            src = p.html if raw else getattr(p, where)
            m = rx.search(src)
            if m:
                return p, _snip(src, m) if not raw else m.group(0)
        return None, ""

    def page(self, kind) -> Page | None:
        return self.pages.get(kind)

    def mentions_company(self, s: str) -> bool:
        k = _key(s)
        first = _key(s.split()[0]) if s.split() else ""
        if self.label and len(self.label) >= 3 and (self.label in k or (len(first) >= 3 and first in self.label)):
            return True
        ck = _key(self.company)[:8]
        return bool(ck) and ck in k

    # run -------------------------------------------------------------
    def run(self) -> dict:
        status = self.record.get("status")
        if status != "live":
            return self._not_live(status)
        self.site()
        self.location_and_entity()
        self.pricing_and_transactions()
        self.tools()
        self.jobs_signals()
        self.funding()
        self.nonprofit()
        self.inventory()
        self.business_type()
        if self.phase == "signals":
            self.misc_fit()
        self.must_haves()
        if self.phase == "signals":
            self.weak_derived()
        self.directories()
        return self._result()

    def _not_live(self, status) -> dict:
        err = self.record.get("error") or ""
        if status == "blocked":
            self.s["M2"] = sig(UNKNOWN, note=f"site blocks automated visits ({err}); retry with a real browser")
        elif status != "dead":
            self.s["M2"] = sig(UNKNOWN, note=f"site could not be reached ({err}); retry later")
        else:
            self.s["M2"] = sig(NO, evidence=f"site did not load: {err}")
            self.s["E2"] = sig(CHECK, evidence=f"site did not load: {err}",
                               note="also needs proof of no funding and a new incorporation")
        return self._result()

    # checks ----------------------------------------------------------
    def site(self):
        home = self.page("home")
        text = home.text
        parked = re.search(r"this domain (?:is|may be) for sale|buy this domain|domain (?:is )?parked|sedoparking|hugedomains|"
                           r"afternic|dan\.com|parkingcrew|bodis\.com|domain has expired", home.html, re.I)
        coming = len(text) < 600 and re.search(r"coming soon|launching soon|under construction", text, re.I)
        self.facts["text_chars"] = len(text)
        self.facts["needs_js"] = len(text) < 250 and len(home.html) > 2000
        shutdown = None
        for src in (home.header, text[:3000], home.footer):
            for m in RE_SHUTDOWN.finditer(src or ""):
                before = src[max(0, m.start() - 40): m.start()]
                if self.mentions_company(before + m.group(0)) or RE_FIRST_PERSON.search(before + m.group(0)):
                    shutdown = _snip(src, m, 60)
                    break
            if shutdown:
                break
        if parked:
            self.s["M2"] = sig(NO, evidence=f"parked or for-sale page: {parked.group(0)}", url=home.url)
        elif shutdown:
            self.s["M2"] = sig(NO, evidence=f"the company says it is shutting down: {shutdown}", url=home.url)
            self.facts["shutting_down"] = shutdown
        elif coming:
            self.s["M2"] = sig(NO, evidence="only a coming-soon page", url=home.url)
        elif self.facts["needs_js"]:
            self.s["M2"] = sig(UNKNOWN, note="page content needs JavaScript; retry with a real browser", url=home.url)
        elif len(self.pages) >= 3 or len(text) > 1500:
            self.s["M2"] = sig(YES, evidence=f"site loads with {len(self.pages)} key pages found", url=home.url)
        else:
            self.s["M2"] = sig(CHECK, evidence=f"thin site: {len(text)} characters of text", url=home.url)

    def location_and_entity(self):
        legal_kinds = ["home", "terms", "privacy", "legal", "contact", "about", "security"]
        us, foreign = [], []

        # addresses
        for p in self.pages.values():
            if p.kind not in legal_kinds:
                continue
            for src in (p.footer, p.text if p.kind != "home" else p.footer):
                m = RE_US_ADDR.search(src or "")
                if m:
                    us.append((f"US address: {_snip(src, m, 60)}", p.url))
                    break
            if us:
                break
        for d in self.ld:
            addr = d.get("address")
            if isinstance(addr, dict):
                c = str(addr.get("addressCountry", "")).strip()
                c = c.get("name", "") if isinstance(addr.get("addressCountry"), dict) else c
                if c.upper() in ("US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"):
                    us.append((f"structured data address country: {c}", ""))
                elif c:
                    foreign.append((f"structured data address country: {c}", ""))

        # governing law and "a Delaware corporation"
        gov_state, gov_foreign, state_entity = None, None, None
        for kind in ("terms", "legal", "privacy", "home"):
            p = self.page(kind)
            if not p:
                continue
            src = p.text if kind != "home" else p.footer
            if not state_entity:
                m = RE_STATE_ENTITY.search(src)
                if m:
                    state_entity = (m.group(1).title(), m.group(2).lower(), _snip(src, m), p.url)
            for m in RE_GOVLAW.finditer(src):
                cap = m.group(1)
                cands = [cap, " ".join(cap.split()[:2]), cap.split()[0]]
                hit_us = next((c for c in cands if c in STATE_NAMES), None)
                hit_for = next((c for c in cands if c in FOREIGN_JURIS), None)
                if hit_us and not gov_state:
                    gov_state = (hit_us, _snip(src, m), p.url)
                elif hit_for and not gov_foreign:
                    gov_foreign = (hit_for, _snip(src, m), p.url)
        if gov_state:
            us.append((f"governing law: {gov_state[1]}", gov_state[2]))
        if gov_foreign:
            foreign.append((f"governing law: {gov_foreign[1]}", gov_foreign[2]))
        if state_entity:
            us.append((f"incorporation: {state_entity[2]}", state_entity[3]))

        # legal entity name
        legal_name, suffix_kind, name_src = None, None, ""
        for d in self.ld:
            if d.get("legalName") and isinstance(d["legalName"], str):
                legal_name, name_src = d["legalName"], "structured data legalName"
                break
        if not legal_name:
            for p in self.pages.values():
                for src in (p.footer, p.text[-1500:] if p.kind == "home" else ""):
                    i = re.search(r"©|\(c\)|copyright", src or "", re.I)
                    if i:
                        m = RE_ENTITY.search(src[i.end(): i.end() + 160])
                        if m:
                            legal_name, name_src = f"{m.group(1)} {m.group(2)}".strip(), f"footer on {p.kind} page"
                            break
                if legal_name:
                    break
        if not legal_name:
            for kind in ("terms", "privacy", "legal"):
                p = self.page(kind)
                if not p:
                    continue
                for m in RE_ENTITY.finditer(p.text[:6000]):
                    cand = f"{m.group(1)} {m.group(2)}"
                    if self.mentions_company(cand):
                        legal_name, name_src = cand, f"{kind} page"
                        break
                if legal_name:
                    break
        if legal_name:
            legal_name = clean_legal_name(legal_name)
            suf = RE_ENTITY.search(legal_name)
            suffix = suf.group(2) if suf else legal_name.split()[-1]
            suffix_kind = next((k for rx, k in SUFFIX_KIND if re.match(rx, suffix)), "foreign")
            if suffix_kind == "foreign" and not re.match(r"^(Ltd|Limited|GmbH|S\.A|SAS|B\.V|BV|Pty|PLC|plc|AG|S\.r\.l|SRL|Oy|AB|ApS|Pvt|Private)", suffix):
                suffix_kind = None
            if suffix_kind == "foreign":
                foreign.append((f"foreign legal entity: {legal_name}", ""))
        self.facts["legal_name"] = legal_name
        self.facts["legal_name_source"] = name_src

        # other foreign clues (only count when another clue agrees)
        if self.domain.endswith(FOREIGN_TLDS):
            foreign.append((f"country domain: .{self.domain.rsplit('.', 1)[-1]}", ""))
        p, snip = self.find(RE_FOREIGN_PLACE, kinds=["contact", "legal", "home"], where="footer")
        if not p:
            p, snip = self.find(RE_FOREIGN_PLACE, kinds=["contact", "legal"])
        if p:
            foreign.append((f"foreign address mention: {snip}", p.url))
        pricing = self.page("pricing")
        if pricing and RE_FOREIGN_PRICE.search(pricing.text) and not re.search(r"\$\s?\d", pricing.text):
            foreign.append(("prices shown only in non-US currency", pricing.url))

        # entity type (7 allowed values)
        etype, basis, ev = "Unknown", "", ""
        if state_entity:
            st, kind_txt, ev, _ = state_entity
            if "benefit" in kind_txt:
                etype = "Public Benefit Corporation"
            elif "limited liability" in kind_txt:
                etype = "LLC"
            else:
                etype = "Delaware C-Corp" if st == "Delaware" else "Other US C-Corp"
            basis = "confirmed"
        elif legal_name and suffix_kind == "pbc":
            etype, basis, ev = "Public Benefit Corporation", "confirmed", legal_name
        elif legal_name and suffix_kind == "corp" and gov_state and gov_state[0] == "Delaware":
            etype, basis, ev = "Delaware C-Corp", "confirmed", f"{legal_name} + {gov_state[1]}"
        # Inc. + another state's governing law proves nothing about where it's incorporated (many Delaware
        # companies use California law in their terms), so it stays open: "Needs check" below, for SEC or AI
        elif legal_name and suffix_kind == "llc":
            etype, basis, ev = "LLC", "confirmed", legal_name
        elif legal_name and suffix_kind == "foreign" and len(foreign) >= 2:
            etype, basis, ev = "Non-US entity", "confirmed", legal_name
        self.facts.update(entity_type=etype, entity_basis=basis, entity_evidence=ev, entity_suffix=suffix_kind)

        if etype == "Delaware C-Corp":
            self.s["F3"] = sig(YES, evidence=ev, note=basis)
        elif etype in ("LLC", "Other US C-Corp", "Non-US entity", "Public Benefit Corporation"):
            self.s["F3"] = sig(NO, evidence=ev, note=etype)
        elif legal_name and suffix_kind == "corp":
            self.s["F3"] = sig(CHECK, evidence=legal_name, note="Inc. found but no Delaware governing law; check SEC Form D")

        # M1 / E4
        self.facts["us_clues"] = [e for e, _ in us]
        self.facts["foreign_clues"] = [e for e, _ in foreign]
        if us:
            self.s["M1"] = sig(YES, evidence=us[0][0], url=us[0][1])
            if len(foreign) >= 2:
                self.s["E4"] = sig(CHECK, evidence="; ".join(e for e, _ in foreign[:3]), note="both US and foreign clues")
            else:
                self.s["E4"] = sig(NO, evidence=us[0][0])
        elif len(foreign) >= 2 and any(e.startswith(STRONG_FOREIGN) for e, _ in foreign):
            self.s["M1"] = sig(NO, evidence="; ".join(e for e, _ in foreign[:3]))
            self.s["E4"] = sig(YES, evidence="; ".join(e for e, _ in foreign[:3]))
        elif foreign:
            self.s["M1"] = sig(CHECK, evidence=foreign[0][0])
            self.s["E4"] = sig(CHECK, evidence=foreign[0][0])
        if self.s.get("M1", {}).get("value", UNKNOWN) in (UNKNOWN, CHECK):
            self.ai.append("must_haves/based_in_us")

    def pricing_and_transactions(self):
        p = self.page("pricing")
        prices = []
        if p:
            prices = [m.group(0) for m in RE_PRICE.finditer(p.text)][:8] or [m.group(0) for m in RE_PRICE_LOOSE.finditer(p.text)][:8]
            if prices:
                self.s["F8"] = sig(YES, evidence=", ".join(dict.fromkeys(prices))[:200], url=p.url)
            elif RE_CONTACT_ONLY.search(p.text):
                self.s["F8"] = sig(NO, evidence="pricing page says contact sales, no prices", url=p.url)
            else:
                self.s["F8"] = sig(CHECK, note="pricing page found but no prices read; may be in images or scripts", url=p.url)
        self.facts["prices"] = list(dict.fromkeys(prices))
        self.facts["checkout"] = [n for n, rx in CHECKOUT_FP.items() if re.search(rx, self.raw_all)]
        self.facts["app_store"] = bool(re.search(r"apps\.apple\.com/[^\"']*app/", self.raw_all))
        self.facts["play_store"] = bool(re.search(r"play\.google\.com/store/apps/details", self.raw_all))

        if p and pricing_has_foreign_and_usd(p.text):
            self.s["W6"] = sig(YES, evidence="prices shown in USD and other currencies", url=p.url)
        elif re.search(r"\b(?:USD|EUR|GBP)\s?(?:/|\|)\s?(?:USD|EUR|GBP)\b", self.raw_all):
            self.s["W6"] = sig(CHECK, evidence="currency switcher on site")

        home = self.page("home")
        m = RE_WAITLIST.search(home.text[:3000])
        self.facts["waitlist"] = bool(m)
        if m:
            self.facts["waitlist_evidence"] = _snip(home.text, m)

    def tools(self):
        for name, rule in TOOL_RULES.items():
            hit = None
            if rule["raw"]:
                m = re.search(rule["raw"], self.raw_all)
                if m:
                    hit = (YES, f"page code loads {m.group(0)}", "")
            if not hit:
                rx = re.compile(rule["text"])
                for p in self.pages.values():
                    if p.kind not in USAGE_KINDS:
                        continue
                    m = rx.search(p.text)
                    if m:
                        snip = _snip(p.text, m)
                        hit = (CHECK if INTEGRATION_CONTEXT.search(snip) else YES, snip, p.url)
                        break
            if not hit or hit[0] == CHECK:
                for j in self.jobs:
                    m = re.search(rule["text"], j["description"]) or re.search(rule["text"], j["title"])
                    if m:
                        src = j["description"] if m.string is j["description"] else j["title"]
                        snip = _snip(src, m)
                        strong = (RE_FIN_ROLE.search(j["title"]) or RE_OPS_ROLE.search(j["title"])
                                  or RE_PEOPLE_ROLE.search(j["title"]) or USAGE_CONTEXT.search(snip))
                        hit = (YES if strong else CHECK, f"job post '{j['title']}': {snip}", "")
                        break
            if not hit:
                p, snip = self.find(re.compile(rule["text"]), kinds=["home", "about", "integrations", "careers", "blog", "pricing"])
                if p:
                    hit = (CHECK, snip, p.url)
            if hit:
                self.facts.setdefault("tools", {})[name] = {"value": hit[0], "evidence": hit[1][:300], "url": hit[2]}
                if hit[0] == YES:
                    self.partners[name] = hit[1]

        tools = self.facts.get("tools", {})
        stripe = tools.get("Stripe")
        if stripe:
            self.s["F2"] = sig(stripe["value"], stripe["evidence"], stripe["url"])

        def combine(sid, names):
            found = [(n, tools[n]) for n in names if n in tools]
            yes = [(n, t) for n, t in found if t["value"] == YES]
            if yes:
                self.s[sid] = sig(YES, f"{yes[0][0]}: {yes[0][1]['evidence']}", yes[0][1]["url"])
            elif found:
                self.s[sid] = sig(CHECK, f"{found[0][0]}: {found[0][1]['evidence']}", found[0][1]["url"])

        combine("F9", ["Gusto", "Rippling", "Deel"])
        combine("F19", ["Mercury", "Brex"])
        combine("F20", ["Ramp", "Brex"])
        combine("F21", ["Carta", "Pulley"])
        if any(t["value"] == CHECK for t in tools.values()) and not any(t["value"] == YES for t in tools.values()):
            self.ai.append("must_haves/uses_fintech_tool")

        # competitors
        for name, rx in COMPETITOR_RULES.items():
            for j in self.jobs:
                m = re.search(rx, j["description"]) or re.search(rx, j["title"])
                if m:
                    src = j["description"] if m.string is j["description"] else j["title"]
                    snip = _snip(src, m)
                    if not INTEGRATION_CONTEXT.search(snip):
                        self.competitors[name] = f"job post '{j['title']}': {snip}"
                        break
            if name not in self.competitors:
                p, snip = self.find(re.compile(rx), kinds=["home", "about", "integrations", "pricing", "careers", "blog"])
                if p:
                    self.competitor_mentions[name] = snip
        qbo = self.competitors.get("QuickBooks Online")
        if qbo:
            self.s["F4"] = sig(YES, qbo)
        elif "QuickBooks Online" in self.competitor_mentions:
            self.s["F4"] = sig(CHECK, self.competitor_mentions["QuickBooks Online"], note="mentioned on site; may be an integration")
        erp = self.competitors.get("NetSuite") or self.competitors.get("QuickBooks Enterprise")
        if erp:
            self.s["W10"] = sig(CHECK, erp, note="ERP found in job posts; confirm a full in-house finance team")

    def jobs_signals(self):
        ats, count = self.record.get("ats"), self.record.get("jobs_count")
        self.facts["ats"], self.facts["jobs_count"] = ats, count
        if count is not None:
            titles = ", ".join(j["title"] for j in self.jobs[:6])
            val = YES if count >= 3 else NO
            self.s["F12"] = sig(val, f"{count} open roles on {ats}: {titles}")
            self.s["B3"] = sig(val, f"{count} open roles on {ats}")
        elif ats:
            self.s["F12"] = sig(CHECK, f"job board on {ats}, but its jobs feed couldn't be read")
        elif self.page("careers"):
            self.s["F12"] = sig(CHECK, "careers page found, no known job board", url=self.page("careers").url)

        fin = [j for j in self.jobs if RE_LEAD_FIN.search(j["title"])]
        fin_any = [j for j in self.jobs if RE_FIN_ROLE.search(j["title"])]
        if fin or fin_any:
            j = (fin or fin_any)[0]
            self.s["B4"] = sig(YES, f"open role: {j['title']}")
        ops = [j for j in self.jobs if RE_OPS_ROLE.search(j["title"])]
        if ops:
            self.s["B5"] = sig(YES, f"open role: {ops[0]['title']}")
        book = [j for j in self.jobs if re.search(r"bookkeep", j["title"], re.I)]
        if book:
            self.s["B9"] = sig(CHECK, f"open role: {book[0]['title']}", note="may be replacing a bookkeeper")
        for j in fin_any:
            m = RE_AUDIT.search(j["description"])
            if m:
                self.s["B14"] = sig(YES, f"job post '{j['title']}': {_snip(j['description'], m)}")
                break
        mfg = [j for j in self.jobs if RE_MFG_ROLE.search(j["title"])]
        if mfg:
            self.s["W7"] = sig(CHECK, f"open role: {mfg[0]['title']}")
            self.facts["inventory_roles"] = [j["title"] for j in mfg[:5]]

        remote_jobs = [j for j in self.jobs if j.get("remote")]
        if self.jobs and len(remote_jobs) >= max(1, len(self.jobs) // 2):
            self.s["F16"] = sig(YES, f"{len(remote_jobs)} of {len(self.jobs)} jobs are remote")
        else:
            p, snip = self.find(RE_REMOTE, kinds=["careers", "about", "home"])
            if p:
                self.s["F16"] = sig(YES, snip, p.url)

    def funding(self):
        stages = {"pre-seed": 0, "preseed": 0, "seed": 1, "series a": 2, "series b": 3}
        best, best_ev, best_url, backed = None, "", "", None
        c_plus = None
        for kind in ("home", "about", "team", "careers", "blog", "investors"):
            p = self.page(kind)
            if not p:
                continue
            for sent in re.split(r"(?<=[.!?])\s+", p.text[:20000]):
                if len(sent) > 400 or not RE_FUND_SENT.search(sent):
                    continue
                if not (RE_FIRST_PERSON.search(sent) or self.mentions_company(sent)):
                    continue
                for m in RE_STAGE.finditer(sent):
                    st = m.group(1).lower().replace("pre seed", "pre-seed")
                    if st.startswith("series") and m.group(2).lower() >= "c":
                        c_plus = c_plus or (sent.strip()[:250], p.url)
                        continue
                    rank = stages.get(st)
                    if rank is not None and (best is None or rank > stages[best]):
                        if re.search(r"rais|round|funding|led by|closed|announc", sent, re.I):
                            best, best_ev, best_url = st, sent.strip()[:250], p.url
                if not backed and re.search(r"backed by|investors include|funded by|led by", sent, re.I):
                    backed = (sent.strip()[:250], p.url)

        label = {"pre-seed": "Pre-Seed", "preseed": "Pre-Seed", "seed": "Seed", "series a": "Series A", "series b": "Series B"}
        if best and c_plus:
            self.s["F1"] = sig(CHECK, c_plus[0], c_plus[1], note="early and Series C+ rounds both mentioned")
        elif best:
            self.s["F1"] = sig(YES, best_ev, best_url, note=f"{label[best]} (confirmed on own site)")
            self.facts["funding_stage"], self.facts["funding_basis"] = label[best], "confirmed"
        elif backed:
            self.s["F1"] = sig(YES, backed[0], backed[1], note="investors named, stage not stated")
        ticker = self.find(RE_TICKER, kinds=["home", "about", "investors"])
        if ticker[0]:
            self.s["E1"] = sig(YES, f"publicly traded: {ticker[1]}", ticker[0].url)
            self.facts["funding_stage"], self.facts["funding_basis"] = "Series C or later", "confirmed"
        elif c_plus:
            self.s["E1"] = sig(CHECK, c_plus[0], c_plus[1], note="Series C+ mentioned; confirm it is this company")
            self.ai.append("exclusions/series_c_or_public")
        elif self.page("investors"):
            self.s["E1"] = sig(CHECK, "has an investor relations page", self.page("investors").url)
            self.ai.append("exclusions/series_c_or_public")

        accel_kinds = ["home", "about", "team"]
        p, snip = None, ""
        for page in (self.pages[k] for k in accel_kinds if k in self.pages):
            for m in RE_YC_STRONG.finditer(page.text):
                cand = _snip(page.text, m, 60)
                # batch codes like "(YC X25)" next to another company's name are customer stories, not backing
                before = page.text[max(0, m.start() - 35): m.start()]
                if re.match(r"(?i)YC\s?[WSFX]\d", m.group(0)) and not (self.mentions_company(before) or RE_FIRST_PERSON.search(before)):
                    continue
                p, snip = page, cand
                break
            if p:
                break
        yc_link = re.search(r"ycombinator\.com/companies/[\w-]+", self.page("home").html)
        if p or yc_link:
            self.s["F11"] = sig(YES, snip or f"links to its YC company page: {yc_link.group(0)}", p.url if p else "")
            self.partners["Y Combinator"] = self.s["F11"]["evidence"]
        else:
            p, snip = self.find(RE_YC_WEAK, kinds=accel_kinds)
            if p:
                self.s["F11"] = sig(CHECK, snip, p.url, note="Y Combinator mentioned, but not clearly as the company's backer; check the YC directory")
        p, snip = self.find(RE_TECHSTARS_STRONG, kinds=accel_kinds)
        if p:
            self.s["F11"] = self.s["F11"] if self.s.get("F11", {}).get("value") == YES else sig(YES, snip, p.url)
            self.partners["Techstars"] = snip

    def nonprofit(self):
        if re.search(r"\.(gov|mil)$|\.gov\.[a-z]{2}$", self.domain):
            self.s["E5"] = sig(YES, f"government domain: {self.domain}")
            self.facts["entity_type"] = "Non-profit"
            return
        if {"NGO", "NonprofitType"} & self.ld_types or any("nonprofitStatus" in d for d in self.ld):
            self.s["E5"] = sig(YES, "structured data marks the site as an NGO / non-profit")
            self.facts["entity_type"] = "Non-profit"
            return
        evidence = []
        for kind in ("home", "about", "donate", "contact", "legal", "terms", "privacy"):
            p = self.page(kind)
            if not p:
                continue
            for rx in (RE_501, RE_CHARITY, RE_EIN, RE_990, RE_TAXDED):
                for src in ((p.footer, p.text) if kind == "home" else (p.text,)):
                    m = rx.search(src)
                    if m and not RE_SELLS_TO_NP.search(_snip(src, m)):
                        evidence.append((_snip(src, m), p.url))
                        break
        donate_cta = bool(self.page("donate")) or bool(re.search(r"\bDonate\b", self.page("home").header))
        commercial = bool(self.facts.get("prices")) or any(c != "Stripe checkout" for c in self.facts.get("checkout", []))
        if evidence and (donate_cta or any("deductible" in e.lower() for e, _ in evidence)) and not commercial:
            self.s["E5"] = sig(YES, evidence[0][0], evidence[0][1])
            self.facts["entity_type"] = "Non-profit"
        elif evidence or (donate_cta and not commercial):
            self.s["E5"] = sig(CHECK, evidence[0][0] if evidence else "donate button in main menu",
                               evidence[0][1] if evidence else "")
            self.ai.append("exclusions/nonprofit_or_government")
        elif self.facts.get("entity_suffix") in ("corp", "llc", "pbc"):
            self.s["E5"] = sig(NO, f"for-profit legal entity: {self.facts.get('legal_name')}")

    def inventory(self):
        platform = [n for n, rx in STORE_FP.items() if re.search(rx, self.raw_all)]
        avail = [str(d.get("availability", "")) for d in self.ld if d.get("availability")]
        stock_ld = any(re.search(r"InStock|OutOfStock|LimitedAvailability|PreOrder", a) for a in avail)
        p_stock, stock_snip = self.find(RE_STOCK, kinds=["home"])
        p_cart, _ = self.find(RE_CART, kinds=["home"])
        shipping = bool(self.page("shipping")) or bool(re.search(r"free shipping|ships (?:in|within|free)|shipping (?:policy|& returns)", self.page("home").text, re.I))
        pod = bool(RE_POD.search(self.raw_all))
        digital = bool(RE_DIGITAL.search(self.page("home").text))
        self.facts["store_platform"] = platform
        self.facts.update(pod=pod, digital_goods=digital)
        inv_roles = self.facts.get("inventory_roles")

        if (platform or p_cart) and (stock_ld or p_stock or shipping or inv_roles) and not pod and not digital:
            ev = ", ".join(platform) or "add to cart"
            ev += "; " + ("stock status in product data" if stock_ld else stock_snip if p_stock else "shipping policy" if shipping else f"roles: {inv_roles}")
            self.s["E6"] = sig(YES, ev)
        elif platform or p_cart or inv_roles:
            self.s["E6"] = sig(CHECK, ", ".join(platform) or "add to cart button" if (platform or p_cart) else f"roles: {inv_roles}")
            self.ai.append("exclusions/holds_physical_stock")
        if platform and self.s.get("E6", {}).get("value") != YES:
            self.s["W9"] = sig(YES if (pod or digital) else CHECK,
                               f"{', '.join(platform)} store" + (" with print-on-demand" if pod else " selling digital goods" if digital else ""))

    def business_type(self):
        home = self.page("home")
        head = " ".join([home.title, home.desc, home.h1, home.text[:800]])
        m = RE_ACCOUNTING_FIRM.search(head)
        if m or "AccountingService" in self.ld_types:
            ev = _snip(head, m) if m else "structured data type AccountingService"
            self.s["E3"] = sig(CHECK, ev, home.url, note="sounds like an accounting firm; confirm it sells services, not software")
            self.ai.append("exclusions/accounting_firm")

        physical = LD_PHYSICAL & self.ld_types
        hours = any("openingHours" in d or "openingHoursSpecification" in d for d in self.ld)
        if physical or self.page("menu"):
            self.s["W3"] = sig(YES, f"structured data type: {', '.join(sorted(physical))}" if physical else "has a menu page",
                               note="opening hours listed" if hours else "")
        elif hours or RE_HOURS.search(home.text):
            self.s["W3"] = sig(CHECK, "opening hours listed on site")
        local_ld = LD_LOCAL_SERVICE & self.ld_types
        p, snip = self.find(RE_SERVICE_AREA, kinds=["home", "about", "locations", "contact"])
        if p or self.page("locations") or local_ld:
            self.s["W1"] = sig(CHECK, snip or ("locations page" if self.page("locations") else f"structured data type: {', '.join(local_ld)}"),
                               p.url if p else "", note="AI decides, since local professional services firms aren't marked down for being local")
        if any(self.s.get(k, {}).get("value") in (YES, CHECK) for k in ("W1", "W3")):
            self.ai.append("weak/serves_one_local_area")

    def misc_fit(self):
        p, snip = self.find(RE_METRIC, kinds=["home", "about", "customers", "blog", "careers"])
        if p:
            self.s["F15"] = sig(YES, snip, p.url)
        if "producthunt.com" in self.raw_all:
            self.s["F18"] = sig(YES, "links to a Product Hunt launch")
        elif self.page("engineering"):
            self.s["F18"] = sig(YES, "has an engineering blog", self.page("engineering").url)
        p, snip = self.find(RE_HOLDING, kinds=["home", "about", "legal", "terms"])
        if p:
            self.s["F17"] = sig(CHECK, snip, p.url)
            self.ai.append("fit/runs_several_us_companies")

        # B10: tax deadlines for US calendar-year C-Corps (Apr 15, Oct 15 extension)
        today = date.today()
        for month, day in ((4, 15), (10, 15)):
            deadline = date(today.year, month, day)
            if 0 <= (deadline - today).days <= 45 and self.s.get("M1", {}).get("value") != NO:
                self.s["B10"] = sig(YES, f"{(deadline - today).days} days to the {deadline:%b %d} filing deadline",
                                    note="calendar based; applies to almost every US company")

        if self.page("team"):
            self.ai += ["fit/no_finance_person_visible", "fit/finance_lead_present", "fields/segment_headcount"]

    def must_haves(self):
        tools = self.facts.get("tools", {})
        if any(t["value"] == YES for t in tools.values()):
            n, t = next((n, t) for n, t in tools.items() if t["value"] == YES)
            self.s["M3"] = sig(YES, f"{n}: {t['evidence']}", t["url"])
        elif tools:
            n, t = next(iter(tools.items()))
            self.s["M3"] = sig(CHECK, f"{n}: {t['evidence']}", t["url"])

        proof = []
        if self.facts.get("prices"):
            proof.append(f"paid pricing ({self.facts['prices'][0]})")
        if self.facts.get("checkout"):
            proof.append(f"checkout: {', '.join(self.facts['checkout'])}")
        if self.facts.get("app_store") or self.facts.get("play_store"):
            proof.append("app store listing")
        if self.facts.get("jobs_count"):
            proof.append(f"{self.facts['jobs_count']} open roles")
        if proof:
            self.s["M4"] = sig(YES, "; ".join(proof))
        elif self.facts.get("waitlist"):
            self.s["M4"] = sig(NO, f"waitlist only: {self.facts.get('waitlist_evidence', '')}")
        if self.facts.get("waitlist") and not proof:
            self.s["W5"] = sig(YES, self.facts.get("waitlist_evidence", ""))
            self.ai.append("weak/pre_revenue")

    def weak_derived(self):
        if self.facts.get("entity_type") == "LLC" and self.s.get("F1", {}).get("value") != YES:
            self.s["W4"] = sig(YES, self.facts.get("entity_evidence", ""), note="provisional until the funding search runs")

    def directories(self):
        """Free official directories (YC, SEC, IRS). They only fill what the rules above left open."""
        if not self.directory_fn:
            return
        from . import directories
        gate = decide(self.s, self.record.get("status"))["gate"]
        self.directories_found = self.directory_fn(self.facts.get("legal_name"), self.facts.get("us_clues", []),
                                                   gate in ("excluded", "failed must-have"))
        directories.apply(self)

    # result ----------------------------------------------------------
    def _result(self) -> dict:
        for sid in SIGNALS:
            self.s.setdefault(sid, sig(UNKNOWN))
        live = self.record.get("status") == "live"
        # funding found (site, Form D, YC) but no early stage confirmed: it could be Series C or later
        self.facts["funded_stage_open"] = self.s.get("F1", {}).get("value") == YES and not self.facts.get("funding_stage")
        if self.phase == "icp":
            # phase 1 reports only must-haves and exclusions
            self.s = {sid: v for sid, v in self.s.items() if SIGNALS[sid][0] in ("must", "exclusion")}
        d = decide(self.s, self.record.get("status"))
        gate, verdict, checks, excluded, confirmed = d["gate"], d["verdict"], d["checks"], d["excluded"], d["confirmed"]

        ai_prompts = self._ai_prompts() if live and gate not in ("excluded", "failed must-have") else []
        ai_context = self._ai_context(ai_prompts) if ai_prompts else {}
        tokens = cached = output = 0
        if ai_prompts:
            # every call sends system + company context + one data point's instructions.
            # system + context are identical across the company's calls, so calls 2..n read them from the cache.
            shared = len(prompt_files.system_text()) // 4 + sum(len(v) for v in ai_context.values()) // 4 + 60
            tokens = shared * len(ai_prompts) + sum(len(prompt_files.instruction_text(pid)) // 4 for pid in ai_prompts)
            cached = shared * (len(ai_prompts) - 1)
            output = 150 * len(ai_prompts)

        stage = self.facts.get("funding_stage")
        basis = self.facts.get("funding_basis", "")
        return {
            "domain": self.domain,
            "company": self.company,
            "employees_input": self.employees,
            "crawl": {k: self.record.get(k) for k in ("status", "error", "final_url", "ats", "jobs_count", "requests")}
                     | {"pages": sorted(self.record.get("pages", {}))},
            "phase": self.phase,
            "gate": gate,
            "icp_verdict": verdict,
            "icp_checks": [SIGNALS[sid][1] for sid in checks],
            "exclusion_reason": "; ".join(f"{SIGNALS[sid][1]}: {self.s[sid]['evidence']}" for sid in excluded),
            "must_haves_confirmed": len(confirmed),
            "entity_type": self.facts.get("entity_type", "Unknown"),
            "legal_name": self.facts.get("legal_name"),
            "funding_stage": f"{stage} ({basis})" if stage else "Unknown (not found)",
            "directories": self.facts.get("directories", ""),
            "partners": sorted(self.partners),
            "competitors": sorted(self.competitors),
            "competitors_mentioned": sorted(set(self.competitor_mentions) - set(self.competitors)),
            "signals": self.s,
            "facts": self.facts,
            "ai": {"needed": bool(ai_prompts), "prompts": ai_prompts, "calls": len(ai_prompts), "tokens_est": tokens,
                   "cached_tokens_est": cached, "output_tokens_est": output},
            "ai_context": ai_context,
        }

    def _ai_prompts(self) -> list[str]:
        """Which data-point prompts this company still needs (one AI call each)."""
        known = prompt_files.load()[1]
        chosen = (list(ALWAYS_PROMPTS) if self.phase == "signals" else []) + self.ai
        signal_prompt = prompt_files.by_signal()
        for sid, v in self.s.items():
            if v["value"] == CHECK and sid in signal_prompt:
                chosen.append(signal_prompt[sid])
        if self.phase == "icp" and self.facts.get("funded_stage_open") and self.s.get("E1", {}).get("value") == UNKNOWN:
            chosen.append("exclusions/series_c_or_public")
        if self.s.get("F1", {}).get("value") == UNKNOWN or (
                self.s.get("F1", {}).get("method") == "directory" and not self.facts.get("funding_stage")):
            chosen.append("fit/venture_backed_stage")  # a directory proves funding, but not the stage
        if self.facts.get("entity_type", "Unknown") == "Unknown":
            chosen.append("fields/legal_entity_type")
        if not self.employees:
            chosen.append("fields/segment_headcount")
        if self.competitor_mentions or self.competitors:
            chosen.append("fields/competitors_used")
        # phase 1 asks only must-have and exclusion prompts; phase 2 asks everything else.
        # Only prompts that read the website or job posts; search and final-output prompts run in later steps.
        icp_prompt = lambda pid: pid.startswith(("must_haves/", "exclusions/"))  # noqa: E731
        settled = set()
        if self.directories_found:
            from .directories import settled_prompts
            settled = settled_prompts(self.s) - {"fit/venture_backed_stage"}
        return [pid for pid in dict.fromkeys(chosen)
                if pid in known and pid not in settled and known[pid]["stage"] in ("site_text", "job_posts")
                and icp_prompt(pid) == (self.phase == "icp")]

    def _job_snippets(self) -> str:
        tool_rx = re.compile("|".join(r["text"] for r in TOOL_RULES.values()) + "|" + "|".join(COMPETITOR_RULES.values()))
        out = []
        for j in self.jobs[:40]:
            if RE_FIN_ROLE.search(j["title"]) or RE_OPS_ROLE.search(j["title"]) or RE_PEOPLE_ROLE.search(j["title"]):
                out.append(f"{j['title']}: {j['description'][:600]}")
                continue
            hits = [_snip(j["description"], m, 80) for m in list(tool_rx.finditer(j["description"]))[:2]]
            if hits:
                out.append(f"{j['title']}: " + " | ".join(hits))
        return "\n".join(out)[:5000]

    def _ai_context(self, prompt_ids: list[str]) -> dict[str, str]:
        """The page text the chosen prompts read, trimmed. Shared by all of the company's prompts so it can be cached."""
        pages, extras, limit = prompt_files.pages_needed(prompt_ids)
        ctx: dict[str, str] = {}
        home = self.pages.get("home")
        for key in pages:
            if key == "home_meta" and home:
                ctx[key] = f"title: {home.title}\ndescription: {home.desc}\nfooter: {home.footer[:800]}"
            elif key in self.pages:
                ctx[key] = self.pages[key].text[: 6000 if key == "home" else limit]
        if "tool_mentions" in extras and self.facts.get("tools"):
            ctx["tool_mentions"] = "\n".join(f"{n}: {t['evidence']}" for n, t in self.facts["tools"].items())
        if self.jobs and ("job_titles" in extras or "job_snippets" in extras):
            ctx["job_titles"] = ", ".join(j["title"] for j in self.jobs[:40])
        if "job_snippets" in extras and self.jobs:
            ctx["job_snippets"] = self._job_snippets()
        if "employees_from_list" in extras and self.employees:
            ctx["employees_from_list"] = f"Employee count given in the lead list: {self.employees}"
        return {k: v for k, v in ctx.items() if v}



NAME_STOPWORDS = {"privacy", "policy", "overview", "terms", "service", "services", "of", "use", "copyright", "all",
                  "rights", "reserved", "the", "by", "and", "&", "website", "site", "legal", "notice", "last", "updated",
                  "effective", "date", "agreement", "this", "these", "welcome", "to", "home", "contact", "us", "about"}


def clean_legal_name(name: str) -> str:
    """Drop page words caught before the real name: 'Privacy Policy Overview Acme Ltd.' -> 'Acme Ltd.'"""
    tokens = name.split()
    suffix = tokens[-1]
    body = tokens[:-1]
    while body and body[0].lower().strip(",.") in NAME_STOPWORDS:
        body = body[1:]
    for i in range(len(body) - 1, -1, -1):
        if body[i].lower().strip(",.") in NAME_STOPWORDS - {"of", "and", "&"}:
            body = body[i + 1:]
            break
    return " ".join(body + [suffix]) if body else name


def pricing_has_foreign_and_usd(text: str) -> bool:
    return bool(RE_FOREIGN_PRICE.search(text)) and bool(re.search(r"\$\s?\d", text))


def extract(domain: str, company: str, employees: str, record: dict, pages: dict[str, str], jobs: list[dict],
            phase: str = "icp", directory_fn=None) -> dict:
    return Extractor(domain, company, employees, record, pages, jobs, phase, directory_fn).run()
