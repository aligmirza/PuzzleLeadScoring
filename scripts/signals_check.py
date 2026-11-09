"""Phase 2: fit, buying and weak signals, only for companies that passed phase 1.

    .venv/bin/python scripts/signals_check.py leads [--only-yes]

Writes lists/<list>/output/signals.csv. Same as: python -m enrich signals leads
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere: the project root holds enrich/

from enrich.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main(["signals", *sys.argv[1:]])
