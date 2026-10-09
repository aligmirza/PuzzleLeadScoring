"""HTTP API for Clay and other tools.

Start it:   python -m enrich serve            (http://127.0.0.1:8787, docs at /docs)
Every /v1 call needs the API key (created once, kept in .env): header `x-api-key: <key>`
or `Authorization: Bearer <key>`. Show it with `python -m enrich settings`.
"""
import csv
import io
import secrets
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import clay, service, settings

app = FastAPI(title="PuzzleLeadScoring API", version="1.0",
              description="Check companies against Puzzle's ICP, score them, and exchange lists with Clay.")


def require_key(x_api_key: str | None = Header(None), authorization: str | None = Header(None)) -> None:
    expected = settings.api_key(create=False)
    given = x_api_key or (authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else "")
    if not expected or not secrets.compare_digest(given or "", expected):
        raise HTTPException(401, "Missing or wrong API key. Send it as the x-api-key header.")


def _bad(e: Exception):
    raise HTTPException(400, str(e))


class CheckIn(BaseModel):
    domain: str = Field(description="Company website, e.g. acme.com")
    company: str = ""
    employees: str = Field("", description="Employee count, used for the size segment")
    signals: bool = Field(True, description="Also run phase 2 (signals and lead score) if the company is ICP")
    ai: bool = Field(False, description="Also run the AI step (needs OPENAI_API_KEY; costs money)")
    web: bool = Field(True, description="Allow the AI web search step")
    refresh: bool = Field(False, description="Download the website again instead of using the cache")
    wait_seconds: float = Field(45, description="Answer within this time; longer checks keep running, ask again")


class ListIn(BaseModel):
    name: str | None = Field(None, description="List name; default is generated")
    rows: list[dict[str, Any]] = Field(description="Rows with at least a Domain (or Website) column")
    signals: bool = True
    ai: bool = False
    web: bool = True
    push_to_clay: bool = Field(False, description="When done, push every row to a Clay table webhook")
    clay_webhook_url: str | None = Field(None, description="Clay webhook; default is CLAY_WEBHOOK_URL from settings")


class PushIn(BaseModel):
    clay_webhook_url: str | None = None
    only_icp: bool = Field(False, description="Push only rows where ICP = YES")


class PullIn(BaseModel):
    table_id: str = Field(description="Clay table id, e.g. t_abc123 (see GET /v1/clay/tables)")
    name: str | None = None
    limit: int = Field(0, description="Only the first N rows (0 = all)")
    signals: bool = True
    ai: bool = False
    web: bool = True
    push_to_clay: bool = False
    clay_webhook_url: str | None = None


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/v1/check", dependencies=[Depends(require_key)])
async def check(body: CheckIn):
    """One company, answered while Clay waits. Use this from a Clay HTTP API column."""
    try:
        return await service.check_one(body.domain, body.company, body.employees, body.signals, body.ai,
                                       body.web, body.refresh, body.wait_seconds)
    except (ValueError, RuntimeError) as e:
        _bad(e)


@app.post("/v1/lists", dependencies=[Depends(require_key)], status_code=202)
async def create_list(body: ListIn):
    """Start a whole list in the background. Poll GET /v1/lists/{name}."""
    try:
        return service.start_list(body.name, body.rows, body.signals, body.ai, body.web, body.push_to_clay,
                                  body.clay_webhook_url)
    except (ValueError, RuntimeError) as e:
        _bad(e)


@app.post("/v1/lists/upload", dependencies=[Depends(require_key)], status_code=202)
async def upload_list(file: UploadFile = File(...), name: str | None = Form(None), signals: bool = Form(True),
                      ai: bool = Form(False), push_to_clay: bool = Form(False), clay_webhook_url: str | None = Form(None)):
    """Same as POST /v1/lists, with a CSV file upload."""
    text = (await file.read()).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    try:
        return service.start_list(name or (file.filename or "upload").rsplit(".", 1)[0], rows, signals, ai, True,
                                  push_to_clay, clay_webhook_url)
    except (ValueError, RuntimeError) as e:
        _bad(e)


@app.get("/v1/lists/{name}", dependencies=[Depends(require_key)])
def get_list(name: str):
    try:
        return service.list_status(name)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/v1/lists/{name}/results", dependencies=[Depends(require_key)])
def get_results(name: str, format: str = "json", only_icp: bool = False, limit: int = 0):
    """Every row of the list with ICP, score and findings. format=json or csv."""
    try:
        rows = service.list_results(name, only_icp, limit)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(404, str(e))
    if format == "csv":
        buf = io.StringIO()
        if rows:
            w = csv.DictWriter(buf, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        return PlainTextResponse(buf.getvalue(), media_type="text/csv")
    return {"list": name, "count": len(rows), "rows": rows}


@app.post("/v1/lists/{name}/push", dependencies=[Depends(require_key)])
async def push(name: str, body: PushIn):
    """Send the list's rows to a Clay table webhook, one row per request."""
    try:
        return await service.push_list(name, body.clay_webhook_url, body.only_icp)
    except (ValueError, RuntimeError) as e:
        _bad(e)


@app.get("/v1/clay/tables", dependencies=[Depends(require_key)])
def clay_tables():
    """Tables in the signed-in Clay workspace."""
    try:
        return {"tables": clay.list_tables()}
    except RuntimeError as e:
        _bad(e)


@app.post("/v1/clay/pull", dependencies=[Depends(require_key)], status_code=202)
async def clay_pull(body: PullIn):
    """Read a Clay table and process it as a new list."""
    try:
        return service.start_from_clay(body.table_id, body.name, body.limit, signals=body.signals, use_ai=body.ai,
                                       web=body.web, push_to_clay=body.push_to_clay, clay_webhook_url=body.clay_webhook_url)
    except (ValueError, RuntimeError) as e:
        _bad(e)
