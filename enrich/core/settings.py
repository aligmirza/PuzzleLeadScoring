"""Settings and secrets, configured once in a .env file at the project root.

.env is never committed (see .gitignore); .env.example is the blank copy in git that lists every key.
Values already set in the environment win over .env. Every key the project knows is listed in ENV_KEYS,
and `python -m enrich settings` keeps .env and .env.example in line with it.
"""
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
EXAMPLE_FILE = ROOT / ".env.example"

# (section, key, what it's for, where to get it, used now?)
ENV_KEYS = [
    ("This project", "PUZZLE_API_KEY", "Key callers (Clay, teammates, tools) send to our API as the x-api-key header",
     "Created automatically by `python -m enrich settings`", True),
    ("This project", "MCP_CONNECTOR_PASSWORD", "Password people enter once to connect Claude to the hosted MCP connector",
     "Created automatically by `python -m enrich mcp --http`", True),
    ("This project", "MCP_ADMIN_PASSWORD", "Admin password for the hosted connector's settings page (/settings), where API keys are "
     "added. Not the team's connector password", "Created automatically by `python -m enrich mcp --http`", True),
    ("AI", "OPENAI_API_KEY", "AI step (gpt-4o-mini): answers prompts and runs the web search step",
     "platform.openai.com > API keys", True),
    ("AI", "ANTHROPIC_API_KEY", "Alternative AI provider (Claude); set provider to anthropic in config/prompts/_shared.json",
     "console.anthropic.com > API keys", False),
    ("Clay", "CLAY_WEBHOOK_URL", "Default Clay table to push results to (the table's Webhook source URL)",
     "Clay table > Add source > Webhook > copy URL", True),
    ("Clay", "CLAY_WEBHOOK_TOKEN", "Auth token of that webhook, if you turned one on",
     "Clay table > Webhook source settings", True),
    ("Clay", "CLAY_CONTACTS_WEBHOOK_URL", "Clay table for contacts (one row per person, from LeadMagic or Prospeo), its own Webhook source URL",
     "contacts table > Add source > Webhook > copy URL", True),
    ("Clay", "CLAY_API_KEY", "Clay Public API (search, tables, routines over HTTP). Pull and push work without it, through `clay login`",
     "`clay api-keys create --name puzzle-lead-scoring` (shown once)", False),
    ("This project", "DATA_DIR", "Folder for all data (lists, caches). Default: the project folder",
     "any folder, e.g. /Volumes/External/puzzle-data", True),
    ("This project", "AI_MONTHLY_BUDGET_USD", "Cap on AI spending per calendar month across terminal, API, MCP and trace (blank: no cap)",
     "your choice, e.g. 50", True),
    ("This project", "SHARED_CACHE", "Reuse pages and AI answers across lists: on (default) or off. Freshness: SHARED_PAGES_DAYS (30), "
     "SHARED_AI_DAYS (60), SHARED_WEB_DAYS (30), SHARED_RECENT_DAYS (14)", "on or off", True),
    ("This project", "API_AI_MAX_COST_USD", "Highest AI cost one API request may run (estimated first; above it the request is refused). Default 5",
     "your choice, e.g. 5", True),
    ("This project", "LEARNING_AUTO_APPLY", "Learning loop: 1 (default) applies safe fixes automatically and reports them; 0 = everything waits for approval",
     "1 or 0", True),
    ("Free directories", "SEC_CONTACT_EMAIL", "Contact email sent to SEC EDGAR in the User-Agent, as SEC asks of automated tools (optional; lookups work without it)",
     "any email the SEC can reach you at", True),
    ("Data providers", "LEADMAGIC_API_KEY", "LeadMagic (Puzzle's provider, used first): people at a company (contacts and the "
     "finance check), company data, work emails and mobile numbers", "app.leadmagic.io > API > API key", True),
    ("Data providers", "LEADMAGIC_MONTHLY_CREDITS", "Cap on LeadMagic credits per calendar month (blank: no cap)", "your choice, e.g. 500", True),
    ("Data providers", "LEADMAGIC_PEOPLE_LIMIT", "People asked for per company search (default 25; 1 credit each unless the plan "
     "includes search)", "a number from 1 to 50", True),
    ("Data providers", "PROSPEO_API_KEY", "Prospeo: the same people and company data, used only without a LeadMagic key",
     "app.prospeo.io > API > API key", True),
    ("Data providers", "PROVIDER_MONTHLY_CREDITS", "Cap on Prospeo credits per calendar month (blank: no cap)", "your choice, e.g. 500", True),
    ("Data providers", "CONTACTS_PROVIDER", "Force one provider for people and company data: leadmagic or prospeo "
     "(blank: LeadMagic when its key is set)", "leadmagic or prospeo", True),
    ("Other data tools (planned)", "CRUNCHBASE_API_KEY", "Funding rounds and stage", "crunchbase.com > API", False),
    ("Other data tools (planned)", "APOLLO_API_KEY", "Headcount, LinkedIn, people at the company", "apollo.io > Settings > API", False),
    ("Other data tools (planned)", "PDL_API_KEY", "People Data Labs: company size, industry", "peopledatalabs.com > API keys", False),
    ("Other data tools (planned)", "SEARCH_API_KEY", "A separate web search service (Exa, Tavily or Serper), if not using OpenAI's", "the service's dashboard", False),
]
KNOWN = {k for _, k, *_ in ENV_KEYS}


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if value.strip():
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _render(values: dict[str, str], blank: bool) -> str:
    out = ["# PuzzleLeadScoring settings. Set each key once; never commit the filled-in .env.",
           "# Keys marked 'not used yet' are reserved for planned integrations.", ""]
    section = None
    for sec, key, what, where, used in ENV_KEYS:
        if sec != section:
            out += ["", f"# --- {sec} " + "-" * max(3, 60 - len(sec))]
            section = sec
        out.append(f"# {what}{'' if used else ' (not used yet)'}")
        out.append(f"# Get it: {where}")
        out.append(f"{key}={'' if blank else values.get(key, '')}")
    extra = {k: v for k, v in values.items() if k not in KNOWN}
    if extra and not blank:
        out += ["", "# --- Other " + "-" * 54] + [f"{k}={v}" for k, v in extra.items()]
    return "\n".join(out) + "\n"


def _read_values() -> dict[str, str]:
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values


def write_files(values: dict[str, str] | None = None) -> None:
    """Rewrite .env (keeping its values) and .env.example (blank) with every known key and its comment."""
    values = _read_values() if values is None else values
    ENV_FILE.write_text(_render(values, blank=False), encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)
    EXAMPLE_FILE.write_text(_render({}, blank=True), encoding="utf-8")


def set_env(key: str, value: str) -> None:
    """Save KEY=value in .env, in its place in the list."""
    values = _read_values()
    values[key] = value
    write_files(values)
    os.environ[key] = value


def api_key(create: bool = True, rotate: bool = False) -> str | None:
    """The key callers must send. Created once and saved to .env; the same key is used from then on."""
    key = os.environ.get("PUZZLE_API_KEY")
    if (not key and create) or rotate:
        key = "pls_" + secrets.token_urlsafe(32)
        set_env("PUZZLE_API_KEY", key)
    return key


# Keys people may set without a terminal: the Claude Desktop extension's Configure form (scripts/build_mcpb.py) and
# the hosted connector's /settings page (integrations/settings_page.py). (env var, title, description, secret, kind)
EDITABLE = [
    ("LEADMAGIC_API_KEY", "LeadMagic API key",
     "Contacts, emails, mobiles and the finance check. app.leadmagic.io > API > API key.", True, "key"),
    ("LEADMAGIC_MONTHLY_CREDITS", "LeadMagic monthly credit cap", "Most LeadMagic credits per calendar month. Blank: no cap.",
     False, "number"),
    ("CLAY_WEBHOOK_URL", "Clay webhook URL (companies)",
     "Clay table > Add source > Webhook > copy URL. Scored companies are pushed here.", True, "url"),
    ("CLAY_CONTACTS_WEBHOOK_URL", "Clay webhook URL (contacts)", "A second Clay table for people, one row per person.",
     True, "url"),
    ("CLAY_WEBHOOK_TOKEN", "Clay webhook token", "Only if you turned on auth for the webhook (Clay > Webhook source settings).",
     True, "key"),
    ("OPENAI_API_KEY", "OpenAI API key (big lists only)",
     "Only for lists over about 100 companies; Claude answers smaller ones for free. platform.openai.com > API keys.",
     True, "key"),
    ("AI_MONTHLY_BUDGET_USD", "OpenAI monthly budget (USD)", "Most OpenAI spend per calendar month. Blank: no cap.",
     False, "number"),
]


def unset_env(key: str) -> None:
    """Remove KEY from .env and from this process."""
    values = _read_values()
    values.pop(key, None)
    write_files(values)
    os.environ.pop(key, None)


def admin_password(create: bool = True) -> str | None:
    """The settings page's admin password (separate from the team's connector password). Created once, saved to .env."""
    pw = os.environ.get("MCP_ADMIN_PASSWORD")
    if not pw and create:
        pw = secrets.token_urlsafe(18)
        set_env("MCP_ADMIN_PASSWORD", pw)
    return pw


def connector_password(create: bool = True) -> str | None:
    """The hosted MCP connector's sign-in password. Created once and saved to .env."""
    pw = os.environ.get("MCP_CONNECTOR_PASSWORD")
    if not pw and create:
        pw = secrets.token_urlsafe(18)
        set_env("MCP_CONNECTOR_PASSWORD", pw)
    return pw


load_env()
