"""Phase 1: is it our ICP? Checks only must-haves and exclusions.

    .venv/bin/python scripts/icp_check.py leads.csv [--limit 100] [--workers 50]

Writes lists/<list>/output/icp_check.csv. Same as: python -m enrich icp leads.csv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere: the project root holds enrich/

from enrich.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main(["icp", *sys.argv[1:]])
