"""AI step: answer the queued prompts with OpenAI (gpt-4o-mini by default). Asks before spending.

    export OPENAI_API_KEY=sk-...
    .venv/bin/python ai_check.py leads --phase icp       # after phase 1: settles PENDING verdicts
    .venv/bin/python ai_check.py leads --phase signals   # after phase 2: AI-only signals, vertical, segment

Same as: python -m enrich ai leads --phase icp
"""
import sys

from enrich.__main__ import main

if __name__ == "__main__":
    main(["ai", *sys.argv[1:]])
