---
name: bulk-enrich
description: Enrich big lead lists (over ~100 up to thousands of companies) with puzzle-lead-scoring - free steps in the background, then the OpenAI AI steps by default inside the standing cost cap, optional LeadMagic (or Prospeo), costs, results and Clay push. Use when the user wants to enrich a large CSV or Clay table, check costs, or push results.
---

# Big lists (over ~100 companies)

Pipeline: **services** is the default ICP. Use `icp="product"` on every call only when the user asks for the product (platform) ICP, and keep the same `icp` for all calls on a list.

Up to ~100 companies, use `/enrich-chat` (Claude answers in chat, $0 API). Above that, Claude can't answer tens of
thousands of questions in a chat, so the AI steps run on the OpenAI engine. They run by default (production), inside
the standing cap (`API_AI_MAX_COST_USD`, else $5, and `AI_MONTHLY_BUDGET_USD`), without asking.

## Workflow

1. **Free steps and AI:** `enrich_list(csv_path, list_name)` (or `pull_clay_table(table_id)` for a Clay table). Over 100
   companies it runs phase 1, AI, phase 2, AI in the background on its own. Poll `list_status(list_name)` every ~60 s
   until `done`. Report rows, ICP YES / NO / PENDING and the AI cost (`ai_phase1`, `ai_phase2`).
2. **If an AI step was skipped** (`ai_phase1` / `ai_phase2` say `skipped`: above the cap, monthly budget, or no OpenAI
   key): say why, show `list_ai(list_name, phase, estimate_only=true)`, and offer cheap mode (`mode="cheap"`) or a
   higher cap. Run `list_ai(list_name, phase, max_cost_usd=<the user's cap>)` only with a cap the user gave. Never
   raise a cap on your own.
3. **A list already saved** (e.g. enriched with `use_openai=false`): `list_ai(list_name, "icp")` runs inside the
   standing cap; when `list_status` shows it finished, `list_ai(list_name, "signals")`.
4. **Optional step 5:** provider finance check (LeadMagic, else Prospeo), one search per company still open. Ask first; then re-run
   `enrich_list(..., use_provider=true)` on the same CSV and list name (pages and answers are cached).
5. **Results:** `list_results(list_name)` (ICP YES by default), `questions_overview(list_name)` for what's open.
   `push_list_to_clay(list_name)` only if the user asks.
6. **Learn from the run:** the **improve-pipeline** skill (`improvement_report`): report what was fixed automatically,
   fix crashes, and show the other proposals for the user's OK.

## Costs and checks (terminal, from the project folder)

```bash
.venv/bin/python -m enrich costs <list>
.venv/bin/python -m enrich usage
.venv/bin/python -m enrich runs <list>
.venv/bin/python -m enrich lists
```

- Rough OpenAI cost: about $0.30-0.50 per ICP-YES company (accurate), about half in cheap mode, near $0 for companies
  ruled out in phase 1. The monthly cap is `AI_MONTHLY_BUDGET_USD` in `.env`; LeadMagic's is `LEADMAGIC_MONTHLY_CREDITS` (Prospeo's `PROVIDER_MONTHLY_CREDITS`).
- Answers other lists already paid for are reused from the shared cache (no cost).

## For speed and cost

- Split lists over ~5,000 rows into a few lists; run them one after another.
- Run phase 1 AI before phase 2: companies it rules out never get phase 2 questions.
- Don't re-run `list_ai` while one is running for the same list and phase (it refuses).
- Re-running a list is cheap: pages, directory lookups, DNS and AI answers are cached.
