"""Load the AI prompts from prompts/*.json (one file per data point) and build messages from them.

Each data point is answered in up to two steps:
  step 1  build()         reads OUR saved data (pages, job posts). Cheap, small model.
  step 2  build_search()  searches the INTERNET using the company's details as variables
                          ({company}, {domain}, {legal_name}, {year}). Runs when step 1 found nothing
                          or only low confidence, or straight away for "primary" web data points.

Step 1 message layout (so a company's page text can be cached and reused across its prompts):
  system          shared role, ICP summary, ground rules
  context         company, domain, today, facts known from code, page texts   <- same for every prompt of a company
  instruction     the data point's sections and output schema                  <- changes per prompt
"""
import json
from datetime import date
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "config" / "prompts"
REQUIRED = ["id", "data_point", "group", "stage", "model", "run_when", "input", "prompt", "output", "web_search"]
REQUIRED_SECTIONS = ["task", "definition", "yes_when", "no_when", "unknown_when", "watch_out_for", "where_to_look"]
SECTION_TITLES = {
    "task": "Task", "definition": "Definition", "yes_when": "Answer yes when", "no_when": "Answer no when",
    "unknown_when": "Answer unknown when", "watch_out_for": "Watch out for", "where_to_look": "Where to look",
}


@lru_cache(maxsize=1)
def load() -> tuple[dict, dict]:
    """Return (shared, prompts by id). Raises with a clear message if a file is broken."""
    shared = json.loads((PROMPTS_DIR / "_shared.json").read_text())
    prompts = {}
    for path in sorted(PROMPTS_DIR.rglob("*.json")):
        if path.name in ("_shared.json", "index.json"):
            continue
        try:
            p = json.loads(path.read_text())
        except ValueError as e:
            raise SystemExit(f"Prompt file {path} is not valid JSON: {e}")
        missing = [k for k in REQUIRED if k not in p] + [f"prompt.{k}" for k in REQUIRED_SECTIONS if k not in p.get("prompt", {})]
        if missing:
            raise SystemExit(f"Prompt file {path} is missing: {', '.join(missing)}")
        expected_id = str(path.relative_to(PROMPTS_DIR).with_suffix(""))
        if p["id"] != expected_id:
            raise SystemExit(f"Prompt file {path} has id '{p['id']}', expected '{expected_id}'")
        prompts[p["id"]] = p
    return shared, prompts


def provider() -> dict:
    """The active AI provider's settings from _shared.json (models, key name, prices)."""
    shared = load()[0]
    name = shared.get("provider", "openai")
    return {"name": name, **shared["providers"][name]}


def model_for(tier: str) -> str:
    """'small', 'large' or 'web' -> the model name for the active provider."""
    return provider().get(tier, tier)


def by_signal() -> dict[str, str]:
    """Signal ID -> prompt id."""
    return {p["signal_id"]: pid for pid, p in load()[1].items() if p.get("signal_id")}


def name(prompt_id: str) -> str:
    p = load()[1].get(prompt_id)
    return p["data_point"] if p else prompt_id


def pages_needed(prompt_ids: list[str]) -> tuple[list[str], list[str], int]:
    """Union of pages and extras the prompts read, in first-seen order, and the largest per-page limit."""
    prompts = load()[1]
    pages, extras, limit = [], [], 0
    for pid in prompt_ids:
        p = prompts[pid]
        pages += [x for x in p["input"]["pages"] if x not in pages]
        extras += [x for x in p["input"].get("extra", []) if x not in extras]
        limit = max(limit, p["input"].get("max_chars_per_page", 4000))
    return pages, extras, limit


def system_text() -> str:
    s = load()[0]["system"]
    rules = "\n".join(f"- {r}" for r in s["ground_rules"])
    conf = "\n".join(f"- {k}: {v}" for k, v in s["confidence_scale"].items())
    return f"{s['role']}\n\nAbout the ideal customer:\n{s['icp_summary']}\n\nRules:\n{rules}\n\nConfidence:\n{conf}"


def context_text(company: str, domain: str, known_facts: dict, context: dict, today: str | None = None) -> str:
    shared = load()[0]
    tpl, names = shared["context_template"], shared["page_names"]
    parts = [tpl["header"].format(company=company or domain, domain=domain, today=today or date.today().isoformat())]
    if known_facts:
        facts = "\n".join(f"- {k}: {v}" for k, v in known_facts.items())
        parts.append(tpl["known_facts"].format(known_facts=facts))
    for key, text in context.items():
        if text:
            parts.append(tpl["page"].format(page=names.get(key, key), text=text))
    return "\n\n".join(parts)


def instruction_text(prompt_id: str) -> str:
    p = load()[1][prompt_id]
    out = [f"Data point: {p['data_point']} ({p['group']})"]
    for key, title in SECTION_TITLES.items():
        val = p["prompt"].get(key)
        if not val:
            continue
        out.append(f"{title}:\n" + ("\n".join(f"- {v}" for v in val) if isinstance(val, list) else val))
    for i, ex in enumerate(p.get("examples", []), 1):
        out.append(f"Example {i}:\nText: {ex['input_excerpt']}\nAnswer: {json.dumps(ex['output'])}")
    out.append("Return only JSON matching this schema:\n" + json.dumps(p["output"]["schema"]))
    if p["output"].get("notes"):
        out.append(p["output"]["notes"])
    return "\n\n".join(out)


def build(prompt_id: str, company: str, domain: str, known_facts: dict, context: dict) -> dict:
    """Everything needed for one AI call."""
    shared, prompts = load()
    p = prompts[prompt_id]
    return {
        "prompt_id": prompt_id,
        "model": model_for(p["model"]),
        "system": system_text(),
        "context": context_text(company, domain, known_facts, context),
        "instruction": instruction_text(prompt_id),
        "schema": p["output"]["schema"],
    }


# ---------------------------------------------------------------------------
# Step 2: web search
def web_mode(prompt_id: str) -> str:
    """fallback, primary or off."""
    return load()[1][prompt_id]["web_search"]["mode"]


def step_plan(prompt_id: str) -> str:
    return {"fallback": "our data, then web search if nothing found",
            "primary": "web search", "off": "our data only"}[web_mode(prompt_id)]


def needs_web(prompt_id: str, step1: dict | None) -> bool:
    """Should step 2 run, given the step 1 answer (None if step 1 hasn't run)?"""
    mode = web_mode(prompt_id)
    if mode == "off":
        return False
    if mode == "primary":
        return True
    if not step1:
        return False
    value = step1.get("answer", step1.get("value"))
    return value in (None, "", "unknown", "Unknown") or step1.get("confidence") == "low"


def variables(company: str, domain: str, legal_name: str | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    return {"company": company or domain, "domain": domain, "legal_name": legal_name or company or domain,
            "year": str(today.year), "last_year": str(today.year - 1)}


def queries(prompt_id: str, vars_: dict) -> list[str]:
    return [q.format(**vars_) for q in load()[1][prompt_id]["web_search"].get("queries", [])]


def search_schema(prompt_id: str) -> dict:
    """The step 1 schema plus entity_match, sources and queries_used."""
    schema = json.loads(json.dumps(load()[1][prompt_id]["output"]["schema"]))
    extra = load()[0]["web_search_step"]["extra_output"]
    if "properties" in schema:
        schema["properties"].update(extra)
        schema["required"] = list(dict.fromkeys(schema.get("required", []) + list(extra)))
    return schema


def build_search(prompt_id: str, company: str, domain: str, known_facts: dict, legal_name: str | None = None,
                 step1: dict | None = None) -> dict:
    """Everything needed for one step 2 call: Claude with the web search tool."""
    shared, prompts = load()
    ws, cfg = prompts[prompt_id]["web_search"], shared["web_search_step"]
    if ws["mode"] == "off":
        raise ValueError(f"{prompt_id} doesn't use web search: {ws.get('why', '')}")
    vars_ = variables(company, domain, legal_name)
    parts = [f"Company: {vars_['company']}\nDomain: {vars_['domain']}\nLegal name (if known): {vars_['legal_name']}\n"
             f"Today: {date.today().isoformat()}"]
    if known_facts:
        parts.append("Facts already confirmed (don't contradict them):\n" + "\n".join(f"- {k}: {v}" for k, v in known_facts.items()))
    if step1:
        parts.append(f"Step 1 (the company's own website) found: {json.dumps(step1)}\nSearch the web to fill the gap.")
    parts.append(instruction_text(prompt_id).rsplit("Return only JSON", 1)[0].rstrip())
    parts.append("Suggested searches:\n" + "\n".join(f"- {q}" for q in queries(prompt_id, vars_)))
    parts.append("Good sources for this data point:\n" + "\n".join(f"- {x}" for x in ws.get("good_sources", [])))
    if ws.get("note"):
        parts.append(ws["note"])
    parts.append("How to search:\n" + "\n".join(f"- {r}" for r in cfg["instructions"]))
    parts.append("Entity match:\n" + "\n".join(f"- {k}: {v}" for k, v in cfg["entity_match"].items()))
    parts.append("Confidence:\n" + "\n".join(f"- {k}: {v}" for k, v in cfg["confidence"].items()))
    parts.append("Return only JSON matching this schema:\n" + json.dumps(search_schema(prompt_id)))
    return {
        "prompt_id": prompt_id,
        "step": 2,
        "model": model_for(cfg["model"]),
        "system": system_text(),
        "instruction": "\n\n".join(parts),
        "tools": [provider()["web_search_tool"]],
        "schema": search_schema(prompt_id),
        "queries": queries(prompt_id, vars_),
    }
