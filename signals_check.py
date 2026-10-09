"""Phase 2: fit, buying and weak signals, only for companies that passed phase 1.

    .venv/bin/python signals_check.py leads [--only-yes]

Writes lists/<list>/output/signals.csv. Same as: python -m enrich signals leads
"""
import sys

from enrich.__main__ import main

if __name__ == "__main__":
    main(["signals", *sys.argv[1:]])
