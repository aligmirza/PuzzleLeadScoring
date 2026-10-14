---
name: bulk-enrich
description: Enrich big lead lists (over ~100 up to thousands of companies) with puzzle-lead-scoring - free steps in the background, OpenAI via list_ai only with an approved cost cap, optional Prospeo, costs, results and Clay push. Use when the user wants to enrich a large CSV or Clay table, check costs, or push results.
---

# Big lists (over ~100 companies)

Up to ~100 companies, use `/enrich-chat` (Claude answers in chat, $0 API). Above that, Claude can't answer tens of
thousands of questions in a chat, so the AI steps run on the OpenAI engine, only with the user's approved cap.

## Workflow

1. **Free steps:** `enrich_list(csv_path, list_name)` (or `pull_clay_table(table_id)` for a Clay table). Background;
   poll `list_status(list_name)` every ~60 s until `done`. Report rows and ICP YES / NO / PENDING.
2. **Phase 1 AI estimate:** `list_ai(list_name, "icp")` with no cap returns the estimate only. Show: companies, questions,
   worst case in accurate mode and in cheap mode. Ask for a cap.
3. **Run with the cap:** `list_ai(list_name, "icp", max_cost_usd=<approved cap>)`. If it's refused, say why (above the
   cap or the monthly budget) and offer cheap mode (`mode="cheap"`) or a higher cap. Never raise a cap on your own.
4. When `list_status` shows phase 1 AI finished, repeat 2-3 for `"signals"`.
5. **Optional step 5:** Prospeo finance check, 1 credit per company still open. Ask first; then re-run
   `enrich_list(..., use_prospeo=true)` on the same CSV and list name (pages and answers are cached).
6. **Results:** `list_results(list_name)` (ICP YES by default), `questions_overview(list_name)` for what's open.
   `push_list_to_clay(list_name)` only if the user asks.
7. **Learn from the run:** the **improve-pipeline** skill (`improvement_report`): report what was fixed automatically,
   fix crashes, and show the other proposals for the user's OK.

## Costs and checks (terminal, from the project folder)

```bash
.venv/bin/python -m enrich costs <list>
.venv/bin/python -m enrich usage
.venv/bin/python -m enrich runs <list>
.venv/bin/python -m enrich lists
```

- Rough OpenAI cost: about $0.30-0.50 per ICP-YES company (accurate), about half in cheap mode, near $0 for companies
  ruled out in phase 1. The monthly cap is `AI_MONTHLY_BUDGET_USD` in `.env`; Prospeo's is `PROVIDER_MONTHLY_CREDITS`.
- Answers other lists already paid for are reused from the shared cache (no cost).

## For speed and cost

- Split lists over ~5,000 rows into a few lists; run them one after another.
- Run phase 1 AI before phase 2: companies it rules out never get phase 2 questions.
- Don't re-run `list_ai` while one is running for the same list and phase (it refuses).
- Re-running a list is cheap: pages, directory lookups, DNS and AI answers are cached.
