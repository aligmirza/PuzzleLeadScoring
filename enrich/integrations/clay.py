"""Exchange data with Clay.

Pull: read a Clay table with the `clay` command-line tool (signed in with `clay login`).
Push: send rows to a Clay table's webhook source, one row per request (Clay's webhook format).
      A webhook accepts up to 50,000 rows in total.
"""
import asyncio
import csv
import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx

CLAY = shutil.which("clay") or str(Path.home() / ".local" / "bin" / "clay")


def _clay(*args: str) -> dict:
    try:
        out = subprocess.run([CLAY, *args], capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        raise RuntimeError("The clay command-line tool isn't installed. See README, Integrations.")
    if out.returncode != 0:
        raise RuntimeError(f"clay {' '.join(args[:3])} failed: {(out.stdout or out.stderr).strip()[:400]}")
    return json.loads(out.stdout)


def _cell(cell: dict) -> str:
    if cell.get("status") != "success":
        return ""
    value = cell.get("value")
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return "" if value is None else str(value)


def list_tables() -> list[dict]:
    return [{"id": t["id"], "name": t["name"], "workbook": (t.get("workbook") or {}).get("name", "")}
            for t in _clay("tables", "list").get("data", [])]


def pull_table(table_id: str, limit: int = 0) -> tuple[list[dict], dict]:
    """All rows of a Clay table as {column name: value}. Returns (rows, info)."""
    columns = _clay("tables", "columns", "list", table_id).get("data", [])
    names = {c["id"]: c["name"] for c in columns if not c.get("system")}
    rows, cursor, truncated = [], None, False
    while True:
        args = ["tables", "rows", "list", table_id, "--limit", "100"] + (["--cursor", cursor] if cursor else [])
        page = _clay(*args)
        for r in page.get("data", []):
            rows.append({names[cid]: _cell(cell) for cid, cell in r.get("cells", {}).items() if cid in names})
            if limit and len(rows) >= limit:
                return rows, {"columns": list(names.values()), "truncated": truncated}
        truncated = truncated or bool(page.get("truncated"))
        cursor = page.get("cursor")
        if not cursor:
            break
    return rows, {"columns": list(names.values()), "truncated": truncated}


async def push_rows(rows: list[dict], webhook_url: str | None = None, token: str | None = None,
                    concurrency: int = 5) -> dict:
    """POST each row to a Clay table webhook. Retries on rate limits."""
    url = webhook_url or os.environ.get("CLAY_WEBHOOK_URL")
    if not url:
        raise RuntimeError("No Clay webhook URL. Pass one, or set CLAY_WEBHOOK_URL once with "
                           "`python -m enrich settings --clay-webhook URL`.")
    token = token or os.environ.get("CLAY_WEBHOOK_TOKEN")
    headers = {"x-clay-webhook-auth": token} if token else {}
    sem = asyncio.Semaphore(concurrency)
    sent, failed, errors = 0, 0, []

    async with httpx.AsyncClient(timeout=30) as client:
        async def one(row):
            nonlocal sent, failed
            async with sem:
                for attempt in range(5):
                    try:
                        r = await client.post(url, json=row, headers=headers)
                    except httpx.HTTPError as e:
                        r, err = None, str(e)
                    else:
                        err = f"HTTP {r.status_code}: {r.text[:200]}"
                        if r.status_code < 300:
                            sent += 1
                            return
                        if r.status_code not in (429, 500, 502, 503, 504):
                            break
                    await asyncio.sleep(2 ** attempt)
                failed += 1
                if len(errors) < 5:
                    errors.append(err)
        await asyncio.gather(*(one(r) for r in rows))
    return {"sent": sent, "failed": failed, "errors": errors}


def final_rows(folder: Path, only_icp: bool = False) -> list[dict]:
    path = folder / "output" / f"{folder.name}_enriched.csv"
    if not path.exists():
        raise RuntimeError(f"No results yet for list '{folder.name}'.")
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r.get("ICP") == "YES"] if only_icp else rows
