"""Where the time goes: seconds per stage, per company (download, job boards, code scan, each directory, rules).

    t = timing.start(domain)        at the start of a company's work (keeps the one already started for it)
    with timing.stage("jobs"): ...  adds the seconds to that company's stage, also from threads and parallel tasks
    timing.summary()                totals, median and slowest per stage, for `python -m enrich bench`

Free: nothing is written unless bench asks for the summary.
"""
from __future__ import annotations

import contextvars
import statistics
import time
from contextlib import contextmanager

_CURRENT: contextvars.ContextVar[dict | None] = contextvars.ContextVar("timings", default=None)
COMPANIES: dict[str, dict[str, float]] = {}


def start(domain: str) -> dict:
    t = COMPANIES.setdefault(domain, {})
    _CURRENT.set(t)
    return t


@contextmanager
def stage(name: str):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        t = _CURRENT.get()
        if t is not None:
            t[name] = t.get(name, 0.0) + time.perf_counter() - t0


def reset() -> None:
    COMPANIES.clear()


def summary() -> dict:
    stages = sorted({s for t in COMPANIES.values() for s in t})
    out = {}
    for s in stages:
        xs = [t[s] for t in COMPANIES.values() if s in t]
        out[s] = {"companies": len(xs), "total_s": round(sum(xs), 1), "median_s": round(statistics.median(xs), 2),
                  "slowest_s": round(max(xs), 1)}
    return out
