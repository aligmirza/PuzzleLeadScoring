"""The five layers of the waterfall, in order. A layer is a source of answers with a cost and a reach.

Adding a layer or a provider: add it here (and its module); the engine, the waterfall view and the review use this list.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Layer:
    key: str        # its settings block in config/datapoints/*.json and its answers' "method" tag
    name: str       # shown in the waterfall and review
    short: str      # "decided by ..."
    cost: str
    paid: bool


RULES = Layer("rules", "Rules on the scraped pages", "rules", "free", False)
DIRECTORIES = Layer("directories", "Free directories", "directories", "free", False)
AI_PAGES = Layer("ai_pages", "AI reading our pages", "AI on pages", "about $0.0008 a question", True)
AI_WEB = Layer("ai_web", "AI web search", "AI web search", "about $0.011 a search", True)
PROVIDER = Layer("provider", "Provider (Prospeo)", "Prospeo", "1 credit per company", True)
ORDER = [RULES, DIRECTORIES, AI_PAGES, AI_WEB, PROVIDER]

# which data points each layer can answer at all (code support); settings outside this have no effect
DIRECTORY_COVERS = {"M1", "M3", "E1", "E2", "E4", "E5", "F1", "F2", "F3", "F4", "F9", "F11", "F19", "F20", "F21",
                    "B1", "B2", "B6", "B7", "B12", "W4"}  # M3, F2, F4, F9, F19-F21 from the DNS check
PROVIDER_COVERS = {"M1", "M3", "M4", "E1", "E4", "E5", "F1", "F2", "F4", "F9", "F12", "F13", "F14", "F19", "F20",
                   "F21", "B1", "B3", "W10"}


def covers(layer: Layer, sid: str) -> bool:
    if layer is RULES:
        return True
    if layer is DIRECTORIES:
        return sid in DIRECTORY_COVERS
    if layer is PROVIDER:
        return sid in PROVIDER_COVERS
    from ..checks import datapoints
    steps = datapoints.load().get(sid, {}).get("steps", {})
    if layer is AI_PAGES:
        return bool(steps.get("ai_pages", {}).get("prompt"))
    return bool(steps.get("ai_pages", {}).get("prompt")) and steps.get("ai_web", {}).get("run") != "off"


def decided_by(answer: dict) -> Layer | None:
    """Which layer gave a final answer (None while the data point is still open)."""
    if answer["value"] in ("unknown", "check"):
        return None
    method = answer.get("method", "code")
    if method in ("code", "derived", "skipped"):  # derived / skipped: free rules from other data points (engine/resolve.py)
        return RULES
    if method == "directory":
        return DIRECTORIES
    if method == "provider":
        return PROVIDER
    return AI_WEB if "web search" in (answer.get("note") or "") else AI_PAGES
