"""HTTP API: use the pipeline from outside this codebase (Clay, scripts, other tools).

Start it:   python -m enrich serve            (http://127.0.0.1:8787, interactive docs at /docs)
Every /v1 call needs the API key (created once, kept in .env): header `x-api-key: <key>`
or `Authorization: Bearer <key>`. Show it with `python -m enrich settings`.

Safety: AI (which costs money) only runs when a request says so, and every AI run is estimated first and refused
above `max_cost_usd` (default API_AI_MAX_COST_USD in .env, else $5). Nothing can be deleted through the API.
"""
import csv
import io
import secrets
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import clay
from . import service
from ..core import settings

TAGS = [
    {"name": "Companies", "description": "Check one company while you wait."},
    {"name": "Lists", "description": "Run whole lists in the background, follow progress, get results."},
    {"name": "AI", "description": "Cost estimates and the AI step for an existing list. Always estimated first and capped."},
    {"name": "Quality", "description": "Accuracy against right answers, and the run log."},
    {"name": "Reference", "description": "Free directory lookups and the list of data points."},
    {"name": "Clay", "description": "Pull lists from Clay and push results to a Clay table."},
]
app = FastAPI(title="PuzzleLeadScoring API", version="2.0", openapi_tags=TAGS,
              description="Check companies against Puzzle's ICP, score them, and exchange lists with other tools. "
                          "Every /v1 call needs the header `x-api-key`. AI only runs when asked, within a cost limit.")


def require_key(x_api_key: str | None = Header(None), authorization: str | None = Header(None)) -> None:
    expected = settings.api_key(create=False)
    given = x_api_key or (authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else "")
    if not expected or not secrets.compare_digest(given or "", expected):
        raise HTTPException(401, "Missing or wrong API key. Send it as the x-api-key header.")


AUTH = [Depends(require_key)]


def _fail(e: Exception):
    if isinstance(e, service.CostLimit):
        raise HTTPException(402, str(e))
    if isinstance(e, ValueError) and str(e).startswith("No "):
        raise HTTPException(404, str(e))
    raise HTTPException(400, str(e))


class AiOptions(BaseModel):
    mode: Literal["accurate", "cheap"] | None = Field(None, description="accurate: one AI call per data point (default). "
                                                                        "cheap: grouped calls, about 3 times cheaper")
    web: bool = Field(True, description="Allow web search (step 2)")
    search_signals: bool = Field(True, description="Phase 2: ask the buying signals only web search can answer "
                                                   "(about $0.10 per ICP company in accurate mode)")
    max_cost_usd: float | None = Field(None, description="Refuse the AI step if the estimate could exceed this. "
                                                         "Default: API_AI_MAX_COST_USD in .env, else 5")


class CheckIn(BaseModel):
    domain: str = Field(description="Company website, e.g. acme.com", examples=["linear.app"])
    company: str = ""
    employees: str = Field("", description="Employee count, used for the size segment")
    signals: bool = Field(True, description="Also run phase 2 (signals and lead score) if the company is ICP")
    ai: bool = Field(False, description="Also run the AI step (costs money; capped by ai_options.max_cost_usd)")
    ai_options: AiOptions = AiOptions()
    refresh: bool = Field(False, description="Download the website again instead of using the cache")
    wait_seconds: float = Field(45, description="Answer within this time; longer checks keep running, ask again")


class ListIn(BaseModel):
    name: str | None = Field(None, description="List name; default is generated")
    rows: list[dict[str, Any]] = Field(description="Rows with at least a Domain (or Website) column; other columns are kept",
                                       examples=[[{"Company Name": "Linear", "Domain": "linear.app", "Owner": "Sam"}]])
    signals: bool = True
    ai: bool = Field(False, description="Run the AI step after each phase (costs money; capped)")
    ai_options: AiOptions = AiOptions()
    push_to_clay: bool = Field(False, description="When done, push every row to a Clay table webhook")
    clay_webhook_url: str | None = Field(None, description="Clay webhook; default is CLAY_WEBHOOK_URL from settings")
    callback_url: str | None = Field(None, description="When done, POST the list's status and summary to this URL")


class AiIn(BaseModel):
    phase: Literal["icp", "signals"] = Field(description="icp: settle PENDING verdicts; signals: AI-only signals and score")
    ai_options: AiOptions = AiOptions()


class RulesIn(BaseModel):
    phase: Literal["icp", "signals", "both"] = "both"


class EvaluateIn(BaseModel):
    labels: list[dict[str, Any]] | None = Field(
        None, description="Right answers: Domain, Expected ICP (YES/NO), Expected tier (Strong fit/Weak fit), "
                          "'Expected: <data point>' (Yes/No). Leave out to use Expected columns in the list itself",
        examples=[[{"Domain": "linear.app", "Expected ICP": "YES", "Expected tier": "Strong fit",
                    "Expected: Delaware C-Corp": "Yes"}]])


class PushIn(BaseModel):
    clay_webhook_url: str | None = None
    only_icp: bool = Field(False, description="Push only rows where ICP = YES")


class PullIn(BaseModel):
    table_id: str = Field(description="Clay table id, e.g. t_abc123 (see GET /v1/clay/tables)")
    name: str | None = None
    limit: int = Field(0, description="Only the first N rows (0 = all)")
    signals: bool = True
    ai: bool = False
    ai_options: AiOptions = AiOptions()
    push_to_clay: bool = False
    clay_webhook_url: str | None = None
    callback_url: str | None = None


# --- health --------------------------------------------------------------------
@app.get("/health")
def health():
    """Is the API up (no key needed)."""
    return {"ok": True}


# --- companies -----------------------------------------------------------------
@app.post("/v1/check", dependencies=AUTH, tags=["Companies"])
async def check(body: CheckIn):
    """One company, answered while you wait: ICP YES/NO/PENDING with the reason, lead score, tier, reasoning and
    every data point. Cached, so asking again is instant. Use this from a Clay HTTP API column."""
    try:
        return await service.check_one(body.domain, body.company, body.employees, body.signals, body.ai,
                                       body.ai_options.web, body.refresh, body.wait_seconds,
                                       ai=body.ai_options.model_dump())
    except (ValueError, RuntimeError) as e:
        _fail(e)


# --- lists ---------------------------------------------------------------------
@app.get("/v1/lists", dependencies=AUTH, tags=["Lists"])
def all_lists():
    """Every list with its state and counts (ICP YES / NO / PENDING, Strong fit)."""
    return {"lists": service.lists_overview()}


@app.post("/v1/lists", dependencies=AUTH, status_code=202, tags=["Lists"])
async def create_list(body: ListIn):
    """Start a whole list in the background: phase 1, optional AI, phase 2, optional AI, final list, optional Clay
    push and callback. Follow it with GET /v1/lists/{name}."""
    try:
        return service.start_list(body.name, body.rows, body.signals, body.ai, body.ai_options.web, body.push_to_clay,
                                  body.clay_webhook_url, ai=body.ai_options.model_dump(), callback_url=body.callback_url)
    except (ValueError, RuntimeError) as e:
        _fail(e)


@app.post("/v1/lists/upload", dependencies=AUTH, status_code=202, tags=["Lists"])
async def upload_list(file: UploadFile = File(...), name: str | None = Form(None), signals: bool = Form(True),
                      ai: bool = Form(False), mode: str | None = Form(None), max_cost_usd: float | None = Form(None),
                      push_to_clay: bool = Form(False), clay_webhook_url: str | None = Form(None),
                      callback_url: str | None = Form(None)):
    """Same as POST /v1/lists, with a CSV file upload."""
    text = (await file.read()).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    try:
        return service.start_list(name or (file.filename or "upload").rsplit(".", 1)[0], rows, signals, ai, True,
                                  push_to_clay, clay_webhook_url, ai={"mode": mode, "max_cost_usd": max_cost_usd},
                                  callback_url=callback_url)
    except (ValueError, RuntimeError) as e:
        _fail(e)


@app.get("/v1/lists/{name}", dependencies=AUTH, tags=["Lists"])
def get_list(name: str):
    """Progress: state, current step, AI usage, ICP YES / NO / PENDING counts, push and callback result."""
    try:
        return service.list_status(name)
    except ValueError as e:
        _fail(e)


@app.get("/v1/lists/{name}/results", dependencies=AUTH, tags=["Lists"])
def get_results(name: str, format: Literal["json", "csv"] = "json", only_icp: bool = False, limit: int = 0):
    """Every row of the list: your original columns plus ICP, score, tier, reasoning and every data point."""
    try:
        rows = service.list_results(name, only_icp, limit)
    except (ValueError, RuntimeError) as e:
        _fail(e)
    if format == "csv":
        buf = io.StringIO()
        if rows:
            w = csv.DictWriter(buf, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        return PlainTextResponse(buf.getvalue(), media_type="text/csv")
    return {"list": name, "count": len(rows), "rows": rows}


@app.get("/v1/lists/{name}/companies/{domain}", dependencies=AUTH, tags=["Lists"])
def company(name: str, domain: str):
    """One company in a list: its result row, every answer with its proof (quote, page, source: code, directory
    or AI) and what the free directories found."""
    try:
        return service.company_detail(name, domain)
    except ValueError as e:
        _fail(e)


@app.post("/v1/lists/{name}/rules", dependencies=AUTH, tags=["Lists"])
async def rules(name: str, body: RulesIn):
    """Re-apply the rules to saved pages, e.g. after changing scoring.json. Free: no downloading, no AI."""
    try:
        return await service.rerun_rules(name, body.phase)
    except ValueError as e:
        _fail(e)


@app.post("/v1/lists/{name}/push", dependencies=AUTH, tags=["Clay"])
async def push(name: str, body: PushIn):
    """Send the list's rows to a Clay table webhook, one row per request."""
    try:
        return await service.push_list(name, body.clay_webhook_url, body.only_icp)
    except (ValueError, RuntimeError) as e:
        _fail(e)


# --- AI ------------------------------------------------------------------------
@app.get("/v1/lists/{name}/ai/estimate", dependencies=AUTH, tags=["AI"])
def ai_estimate(name: str, phase: Literal["icp", "signals"], search_signals: bool = True, web: bool = True):
    """What the AI step would cost for this list, in both modes. Nothing is sent."""
    try:
        return service.estimate_ai(name, phase, None, search_signals, web)
    except ValueError as e:
        _fail(e)


@app.post("/v1/lists/{name}/ai", dependencies=AUTH, status_code=202, tags=["AI"])
async def ai_run(name: str, body: AiIn):
    """Run the AI step on an existing list in the background (after phase 1: phase=icp; after phase 2:
    phase=signals). Refused if the estimate could exceed ai_options.max_cost_usd. Follow it with GET /v1/lists/{name}."""
    try:
        return service.start_ai(name, body.phase, body.ai_options.model_dump())
    except (ValueError, RuntimeError) as e:
        _fail(e)


# --- quality -------------------------------------------------------------------
@app.post("/v1/lists/{name}/evaluate", dependencies=AUTH, tags=["Quality"])
def evaluate(name: str, body: EvaluateIn):
    """Accuracy against the right answers: ICP, tier and any data point, plus every disagreement with our proof."""
    try:
        return service.evaluate_list(name, body.labels)
    except (ValueError, SystemExit) as e:
        _fail(ValueError(str(e)))


@app.get("/v1/lists/{name}/runs", dependencies=AUTH, tags=["Quality"])
def runs(name: str):
    """The run log: every phase, AI and evaluate run with counts, AI cost and time."""
    try:
        return service.runs(name)
    except ValueError as e:
        _fail(e)


# --- reference -----------------------------------------------------------------
@app.get("/v1/lookup", dependencies=AUTH, tags=["Reference"])
def lookup(domain: str, company: str = "", legal_name: str | None = None, place: str = ""):
    """The free directories for one company: YC (by domain), SEC EDGAR and the IRS non-profit list (by legal name).
    Free; no AI."""
    try:
        return service.lookup(domain, company, legal_name, place)
    except ValueError as e:
        _fail(e)


@app.get("/v1/signals", dependencies=AUTH, tags=["Reference"])
def signals():
    """Every data point: its column name in the results, group (must-have, exclusion, fit, buying, weak) and points."""
    return {"signals": service.signals_reference()}


# --- Clay ----------------------------------------------------------------------
@app.get("/v1/clay/tables", dependencies=AUTH, tags=["Clay"])
def clay_tables():
    """Tables in the signed-in Clay workspace."""
    try:
        return {"tables": clay.list_tables()}
    except RuntimeError as e:
        _fail(e)


@app.post("/v1/clay/pull", dependencies=AUTH, status_code=202, tags=["Clay"])
async def clay_pull(body: PullIn):
    """Read a Clay table and process it as a new list."""
    try:
        return service.start_from_clay(body.table_id, body.name, body.limit, signals=body.signals, use_ai=body.ai,
                                       web=body.ai_options.web, push_to_clay=body.push_to_clay,
                                       clay_webhook_url=body.clay_webhook_url, ai=body.ai_options.model_dump(),
                                       callback_url=body.callback_url)
    except (ValueError, RuntimeError) as e:
        _fail(e)
