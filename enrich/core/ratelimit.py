"""Rate limits shared by every process on this machine (terminal, API, MCP running at the same time).

Each limit is a row in cache/ratelimits.sqlite holding the next free time slot; taking a slot is one short
transaction, so separate processes and threads never exceed the limit together.
"""
import asyncio
import sqlite3
import time

from .paths import CACHE

DB = CACHE / "ratelimits.sqlite"


def _reserve(name: str, per_second: float) -> float:
    """Book the next slot for `name`; returns how long to wait before using it."""
    CACHE.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS slots (name TEXT PRIMARY KEY, next_at REAL)")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT next_at FROM slots WHERE name = ?", (name,)).fetchone()
        now = time.time()
        start = max(now, row[0] if row else 0.0)
        db.execute("INSERT OR REPLACE INTO slots VALUES (?, ?)", (name, start + 1 / per_second))
        db.execute("COMMIT")
        return start - now
    finally:
        db.close()


def wait(name: str, per_second: float) -> None:
    delay = _reserve(name, per_second)
    if delay > 0:
        time.sleep(delay)


async def wait_async(name: str, per_second: float) -> None:
    delay = await asyncio.to_thread(_reserve, name, per_second)
    if delay > 0:
        await asyncio.sleep(delay)
