"""Which ICP pipeline is running, and what it says about each data point.

The data points find facts (config/datapoints/: how each one is answered). A pipeline decides how each fact counts:

    config/icps/<pipeline>/profile.yaml   who the ICP is
    config/icps/<pipeline>/roles.json     each data point's group (must, exclusion, fit, buying, weak),
                                          its conditional runs (run_if) and the opposites
    config/icps/<pipeline>/scoring.json   weights, segments, the Strong-fit cut-off

    with icp.use("services"): ...         run something with a pipeline (follows threads and tasks)
    icp.name()                            the pipeline running now (ICP_PIPELINE in .env, else the default)
"""
from __future__ import annotations

import contextvars
import json
import os
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from .paths import CONFIG

DIR = CONFIG / "icps"
DEFAULT = "services"  # the core targeting since Oct 22, 2026; product runs only when chosen
_CURRENT: contextvars.ContextVar[str | None] = contextvars.ContextVar("icp_pipeline", default=None)


def available() -> list[str]:
    return sorted(p.name for p in DIR.iterdir() if (p / "roles.json").exists()) if DIR.exists() else []


def name() -> str:
    return _CURRENT.get() or os.environ.get("ICP_PIPELINE", "").strip() or DEFAULT


def check(pipeline: str | None) -> str:
    """The pipeline asked for, or the current one; an unknown name is an error, never a silent fallback."""
    p = (pipeline or name()).strip().lower()
    if p not in available():
        raise ValueError(f"Unknown ICP pipeline '{p}'. Available: {', '.join(available())}")
    return p


@contextmanager
def use(pipeline: str | None):
    """Run the block with this pipeline (None: keep the current one)."""
    token = _CURRENT.set(check(pipeline))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def label() -> str:
    """Added to output file names: '' for the default pipeline, '_product' for another, so results never mix."""
    return "" if name() == DEFAULT else f"_{name()}"


def enriched_csv(list_folder: Path) -> Path:
    """The list's final file for the running pipeline: <list>_enriched.csv, or <list>_product_enriched.csv."""
    return list_folder / "output" / f"{list_folder.name}{label()}_enriched.csv"


def folder(pipeline: str | None = None) -> Path:
    return DIR / (pipeline or name())


@lru_cache(maxsize=None)
def _roles(pipeline: str) -> dict:
    return json.loads((DIR / pipeline / "roles.json").read_text(encoding="utf-8"))


def roles() -> dict:
    return _roles(name())


def group(sid: str) -> str:
    return roles()["groups"][sid]


def phase_of(sid: str) -> str:
    """Phase 1 ("icp") checks this pipeline's must-haves and exclusions; phase 2 ("signals") scores the rest."""
    return "icp" if group(sid) in ("must", "exclusion") else "signals"


def run_if(sid: str) -> dict[str, list[str]]:
    return (roles().get("run_if") or {}).get(sid) or {}


def opposites() -> dict[str, str]:
    return roles().get("opposites") or {}


def reason_label(sid: str) -> str | None:
    """The one rule several hard stops share in this pipeline (e.g. "Too big for services"), or None."""
    return next((label for label, sids in (roles().get("reason_groups") or {}).items() if sid in sids), None)


@lru_cache(maxsize=None)
def _scoring(pipeline: str) -> dict:
    return json.loads((DIR / pipeline / "scoring.json").read_text(encoding="utf-8"))


def scoring() -> dict:
    return _scoring(name())


def files(pipeline: str | None = None) -> list[Path]:
    """The pipeline's own config files (for the rules version stamp)."""
    f = folder(pipeline)
    return [f / "roles.json", f / "scoring.json", f / "profile.yaml"]
