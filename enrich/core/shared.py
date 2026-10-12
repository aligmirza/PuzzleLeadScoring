"""One company cache shared by every list: a company in several lists is downloaded and AI-checked once.

    cache/companies/<domain>/record.json, <kind>.html.gz, jobs.json.gz   saved pages (hard-linked into lists: no extra disk)
    cache/ai_answers.sqlite                                              AI answers with their prompt version

Reused only while fresh (days, set in .env):
    SHARED_PAGES_DAYS   30   website pages and job feeds
    SHARED_AI_DAYS      60   AI answers read from the company's pages
    SHARED_WEB_DAYS     30   AI answers from web search
    SHARED_RECENT_DAYS  14   "recent" signals (funding in 6 months, new investor, launched pricing...)
and only when made with the same prompt version. --refresh skips the cache. Only live sites are shared.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from .paths import CACHE

COMPANIES = CACHE / "companies"
AI_DB = CACHE / "ai_answers.sqlite"
RECENT_PROMPTS = ("buying/raised_last_6_months", "buying/new_investor_or_accelerator", "buying/launched_paid_pricing",
                  "buying/new_board_member_or_investor_updates", "buying/converted_llc_to_delaware",
                  "buying/incorporated_last_12_months")


def _days(name: str, default: int) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return float(default)


def enabled() -> bool:
    return os.environ.get("SHARED_CACHE", "on").lower() not in ("off", "0", "false", "no")


def _link(src: Path, dst: Path) -> None:
    """Same file in both places (no extra disk). Falls back to a copy across disks."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        os.link(src, tmp)
    except OSError:
        shutil.copy2(src, tmp)
    tmp.replace(dst)


# pages ------------------------------------------------------------------------
def publish_site(store, domain: str, record: dict) -> None:
    """After a download: make the list's pages and record available to every other list."""
    if not enabled() or record.get("status") != "live":
        return
    folder = COMPANIES / domain
    folder.mkdir(parents=True, exist_ok=True)
    src = store.pages_dir / domain
    for f in src.glob("*.gz"):
        _link(f, folder / f.name)
    keep = {k: v for k, v in record.items() if k != "input"}
    tmp = folder / "record.json.tmp"
    tmp.write_text(json.dumps({**keep, "shared_at": time.time()}))
    tmp.replace(folder / "record.json")


def adopt_site(store, domain: str, kinds: set[str], need_signals: bool = False) -> dict | None:
    """Before a download: if another list has this company's pages (fresh, live, with the page kinds asked for),
    link them into this list and return the record. Otherwise None."""
    if not enabled():
        return None
    folder = COMPANIES / domain
    path = folder / "record.json"
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text())
    except ValueError:
        return None
    if time.time() - record.get("shared_at", 0) > _days("SHARED_PAGES_DAYS", 30) * 86400:
        return None
    if record.get("status") != "live" or (need_signals and not record.get("signals_crawled")):
        return None
    if not (folder / "home.html.gz").exists():
        return None
    for f in folder.glob("*.gz"):
        _link(f, store.pages_dir / domain / f.name)
    record.pop("pages_deleted", None)
    record["from_shared_cache"] = time.strftime("%Y-%m-%d", time.localtime(record["shared_at"]))
    return record


# AI answers -------------------------------------------------------------------
def _db() -> sqlite3.Connection:
    CACHE.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(AI_DB, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS answers (domain TEXT, phase TEXT, prompt_id TEXT, step INTEGER, "
               "prompt_version TEXT, answer TEXT, usage TEXT, created REAL, "
               "PRIMARY KEY (domain, phase, prompt_id, step, prompt_version))")
    return db


def put_ai(domain: str, phase: str, prompt_id: str, step: int, answer: dict, usage: dict) -> None:
    if not enabled() or "error" in answer or not usage.get("prompt_version"):
        return
    db = _db()
    try:
        db.execute("INSERT OR REPLACE INTO answers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (domain, phase, prompt_id, step, usage["prompt_version"], json.dumps(answer), json.dumps(usage), time.time()))
        db.commit()
    finally:
        db.close()


def _max_age(prompt_id: str, step: int) -> float:
    if prompt_id in RECENT_PROMPTS:
        return _days("SHARED_RECENT_DAYS", 14) * 86400
    return _days("SHARED_WEB_DAYS", 30) * 86400 if step == 2 else _days("SHARED_AI_DAYS", 60) * 86400


def adopt_ai(store, domain: str, phase: str, prompt_ids: list[str]) -> int:
    """Copy fresh answers made with the current prompt version into this list. Returns how many data points."""
    if not enabled() or not prompt_ids or not AI_DB.exists():
        return 0
    from . import versions
    have = store.get_ai(domain, phase)
    db = _db()
    reused = 0
    try:
        for pid in prompt_ids:
            if pid in have:
                continue
            rows = db.execute("SELECT step, answer, usage, created FROM answers WHERE domain = ? AND phase = ? AND "
                              "prompt_id = ? AND prompt_version = ?", (domain, phase, pid, versions.prompt(pid))).fetchall()
            fresh = [r for r in rows if time.time() - r[3] <= _max_age(pid, r[0])]
            if not fresh or len(fresh) != len(rows):
                continue  # all of a data point's steps, or none: never half an answer
            for step, answer, usage, created in fresh:
                u = {**json.loads(usage), "cost": 0, "reused_from_cache": time.strftime("%Y-%m-%d", time.localtime(created))}
                store.save_ai(domain, phase, pid, step, json.loads(answer), u)
            reused += 1
    finally:
        db.close()
    return reused


def import_lists(lists_dir: Path) -> int:
    """Seed the cache with pages lists already downloaded (live sites with pages still on disk). Newest list wins."""
    from .store import Store
    n = 0
    for folder in sorted((f for f in lists_dir.glob("*") if (f / "cache.sqlite").exists()), key=lambda f: f.stat().st_mtime):
        store = Store(folder)
        for domain in store.all_domains():
            record = store.get_site(domain) or {}
            if record.get("status") == "live" and not record.get("pages_deleted") and (store.pages_dir / domain / "home.html.gz").exists():
                publish_site(store, domain, record)
                n += 1
    return n


# housekeeping -----------------------------------------------------------------
def status() -> dict:
    sites = [d for d in COMPANIES.glob("*") if d.is_dir()] if COMPANIES.exists() else []
    size = sum(f.stat().st_size for d in sites for f in d.glob("*"))
    answers = 0
    if AI_DB.exists():
        db = _db()
        answers = db.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        db.close()
    return {"companies": len(sites), "pages_bytes": size, "ai_answers": answers,
            "ai_bytes": AI_DB.stat().st_size if AI_DB.exists() else 0}


def stale(days: float) -> tuple[list[Path], int]:
    """Company folders not refreshed in `days`, and how many AI answers are older than that."""
    cutoff = time.time() - days * 86400
    old = []
    for d in (COMPANIES.glob("*") if COMPANIES.exists() else []):
        try:
            if json.loads((d / "record.json").read_text()).get("shared_at", 0) < cutoff:
                old.append(d)
        except (OSError, ValueError):
            old.append(d)
    n = 0
    if AI_DB.exists():
        db = _db()
        n = db.execute("SELECT COUNT(*) FROM answers WHERE created < ?", (cutoff,)).fetchone()[0]
        db.close()
    return old, n


def delete_stale(days: float) -> tuple[int, int]:
    """Delete what stale() lists. Lists keep their own linked copies, so finished lists aren't affected."""
    old, _ = stale(days)
    for d in old:
        shutil.rmtree(d, ignore_errors=True)
    removed = 0
    if AI_DB.exists():
        db = _db()
        removed = db.execute("DELETE FROM answers WHERE created < ?", (time.time() - days * 86400,)).rowcount
        db.commit()
        db.execute("VACUUM")
        db.close()
    return len(old), removed
