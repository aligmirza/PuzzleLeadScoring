"""Paid data providers: the last layer of the waterfall, used only for what the free layers and AI left open.

Each provider is set up with its own API key in .env; without a key it is simply skipped. Responses are cached
(cache/providers.sqlite), so a company is never paid for twice within PROVIDER_CACHE_DAYS (default 90).
"""
from . import prospeo

PROVIDERS = {"prospeo": prospeo}


def available() -> list[str]:
    return [name for name, mod in PROVIDERS.items() if mod.enabled()]
