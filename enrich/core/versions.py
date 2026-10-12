"""Version stamps: which rules and which prompt produced an answer.

rules()       changes when the rules, scoring, directory matching, scoring.json or profile.yaml change
prompt(pid)   changes when that data point's prompt file, the shared prompt rules or the model change

Saved on every result and AI answer. The shared cache only reuses an AI answer made with the same prompt version.
"""
import hashlib
import json
from functools import lru_cache

from .paths import CONFIG, ROOT


def _hash(*parts: bytes) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p)
    return h.hexdigest()[:10]


@lru_cache(maxsize=1)
def rules() -> str:
    files = [ROOT / "enrich" / "checks" / "rules.py", ROOT / "enrich" / "checks" / "scoring.py",
             ROOT / "enrich" / "sources" / "directories.py", CONFIG / "scoring.json", CONFIG / "profile.yaml"]
    return _hash(*(f.read_bytes() for f in files if f.exists()))


@lru_cache(maxsize=None)
def prompt(prompt_id: str) -> str:
    from ..ai import prompts as P
    shared, prompts = P.load()
    common = {k: shared.get(k) for k in ("system", "two_steps", "web_search_step", "provider")}
    common["models"] = P.provider().get("small"), P.provider().get("web")
    return _hash(json.dumps(prompts.get(prompt_id, {}), sort_keys=True).encode(), json.dumps(common, sort_keys=True).encode())
