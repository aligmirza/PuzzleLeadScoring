"""Where things live. Code and config stay in the project; data (lists, caches) goes to DATA_DIR.

DATA_DIR in .env moves all data (for example to another disk or, later, a container volume); default: the project folder.
"""
import os
from pathlib import Path

from . import settings

settings.load_env()  # .env first, so DATA_DIR from it is seen
ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
DATA_DIR = Path(os.environ.get("DATA_DIR") or ROOT).expanduser()
LISTS = DATA_DIR / "lists"
CACHE = DATA_DIR / "cache"
