"""Optional list viewer: a local web page that opens an enriched list as a table.

    .venv/bin/python viewer/server.py            ->  http://127.0.0.1:8790

Click any cell to see its details (answer, reasoning, confidence, proof quote, source link) in a side panel.
Read-only: it only reads the saved lists in lists/, nothing is changed, no AI or paid step runs.
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from enrich.checks.rules import SIGNALS, VALUE_NAMES, signal_column  # noqa: E402
from enrich.core.store import Store  # noqa: E402
from enrich.integrations import service  # noqa: E402

PORT = 8790
CONFIDENCE = re.compile(r"with (very high|high|medium|low) confidence")
LINK_COLUMNS = {"Website"}
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8")}


def _domain_of(row: dict) -> str:
    raw = next((row[k] for k in row if k.strip().lower() in ("domain", "website", "url")), "")
    return service.normalize_domain(raw) or raw


def _answers(store: Store, domain: str) -> dict:
    """Column name -> details of the answer, with its proof, for both phases."""
    out = {}
    icp, sig = store.get_result(domain, "icp"), store.get_result(domain, "signals")
    for phase, r in (("icp", icp), ("signals", sig)):
        if not r:
            continue
        for sid, v in r["signals"].items():
            if phase == "signals" and SIGNALS[sid][0] in ("must", "exclusion"):
                continue
            note = v.get("note") or ""
            m = CONFIDENCE.search(note)
            out[signal_column(sid)] = {
                "Response": VALUE_NAMES[v["value"]],
                "Reasoning": note or "Decided by a rule or directory check.",
                "Confidence": m.group(1) if m else ("rule / directory" if v.get("method") != "ai" else ""),
                "Proof": v.get("evidence") or "",
                "Source": v.get("url") or "",
                "Method": v.get("method", "code"),
                "Phase": phase,
            }
    return out


def table(list_name: str) -> dict:
    folder = service._folder(list_name)
    rows = service.list_results(folder.name)
    store = Store(folder)
    columns = list(rows[0].keys()) if rows else []
    out_rows = []
    for row in rows:
        domain = _domain_of(row)
        try:
            answers = _answers(store, domain)
        except Exception:  # noqa: BLE001  (one broken company must not hide the list)
            answers = {}
        cells = {}
        for col in columns:
            value = row.get(col, "")
            if col in answers:
                cells[col] = {"value": answers[col]["Response"], "details": answers[col]}
            else:
                cells[col] = {"value": "" if value is None else str(value), "details": None}
        out_rows.append({"domain": domain, "cells": cells})
    return {"list": folder.name, "columns": columns, "link_columns": sorted(LINK_COLUMNS), "rows": out_rows,
            "answer_columns": sorted({c for r in out_rows for c, x in r["cells"].items() if x["details"]})}


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, kind: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        try:
            if url.path in STATIC:  # only these three files are ever served
                name, kind = STATIC[url.path]
                return self._send((Path(__file__).parent / name).read_bytes(), kind)
            if url.path == "/favicon.ico":
                return self._send(b"", "image/x-icon", 204)
            if url.path == "/api/lists":
                return self._send(json.dumps(service.lists_overview()).encode(), "application/json")
            if url.path == "/api/table":
                name = parse_qs(url.query).get("list", [""])[0]
                return self._send(json.dumps(table(name)).encode(), "application/json")
            self._send(b"not found", "text/plain", 404)
        except Exception as e:  # noqa: BLE001
            self._send(json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(), "application/json", 500)

    def log_message(self, *args):  # quiet
        pass


if __name__ == "__main__":
    print(f"List viewer on http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
