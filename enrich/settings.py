"""Settings and secrets, configured once in a .env file at the project root.

.env is never committed (see .gitignore); .env.example is the blank copy in git that lists every key.
Values already set in the environment win over .env. Every key the project knows is listed in ENV_KEYS,
and `python -m enrich settings` keeps .env and .env.example in line with it.
"""
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
EXAMPLE_FILE = ROOT / ".env.example"

# (section, key, what it's for, where to get it, used now?)
ENV_KEYS = [
    ("This project", "PUZZLE_API_KEY", "Key callers (Clay, teammates, tools) send to our API as the x-api-key header",
     "Created automatically by `python -m enrich settings`", True),
    ("AI", "OPENAI_API_KEY", "AI step (gpt-4o-mini): answers prompts and runs the web search step",
     "platform.openai.com > API keys", True),
    ("AI", "ANTHROPIC_API_KEY", "Alternative AI provider (Claude); set provider to anthropic in config/prompts/_shared.json",
     "console.anthropic.com > API keys", False),
    ("Clay", "CLAY_WEBHOOK_URL", "Default Clay table to push results to (the table's Webhook source URL)",
     "Clay table > Add source > Webhook > copy URL", True),
    ("Clay", "CLAY_WEBHOOK_TOKEN", "Auth token of that webhook, if you turned one on",
     "Clay table > Webhook source settings", True),
    ("Clay", "CLAY_API_KEY", "Clay Public API (search, tables, routines over HTTP). Pull and push work without it, through `clay login`",
     "`clay api-keys create --name puzzle-lead-scoring` (shown once)", False),
    ("Other data tools (planned)", "CRUNCHBASE_API_KEY", "Funding rounds and stage", "crunchbase.com > API", False),
    ("Other data tools (planned)", "APOLLO_API_KEY", "Headcount, LinkedIn, people at the company", "apollo.io > Settings > API", False),
    ("Other data tools (planned)", "PDL_API_KEY", "People Data Labs: company size, industry", "peopledatalabs.com > API keys", False),
    ("Other data tools (planned)", "SEARCH_API_KEY", "A separate web search service (Exa, Tavily or Serper), if not using OpenAI's", "the service's dashboard", False),
]
KNOWN = {k for _, k, *_ in ENV_KEYS}


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
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
        for line in ENV_FILE.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values


def write_files(values: dict[str, str] | None = None) -> None:
    """Rewrite .env (keeping its values) and .env.example (blank) with every known key and its comment."""
    values = _read_values() if values is None else values
    ENV_FILE.write_text(_render(values, blank=False))
    os.chmod(ENV_FILE, 0o600)
    EXAMPLE_FILE.write_text(_render({}, blank=True))


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


load_env()
