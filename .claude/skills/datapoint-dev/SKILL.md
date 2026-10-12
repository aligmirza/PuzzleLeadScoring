---
name: datapoint-dev
description: Change how a puzzle-lead-scoring data point is answered in the code - its waterfall steps, rules, prompts, directories, DNS / Wappalyzer, Prospeo, size skipping - keeping config, code defaults, tests and docs in sync. Use for any edit under enrich/ or config/ that affects a data point (M1-W12).
---

# Changing a data point

## Where things live

| What | File |
|---|---|
| A data point's steps (rules, directories, ai_pages, ai_web, provider) | `config/datapoints/<group>/<name>.json` |
| Code defaults that must match those files | `enrich/checks/datapoints.py` (`defaults()`, `PROVIDER_ON`, `RULES_ONLY`, `_NEEDS_TEAM_PAGE`) |
| Which layers can answer which data point | `enrich/engine/layers.py` (`DIRECTORY_COVERS`, `PROVIDER_COVERS`) |
| Step 1 rules (patterns, page and job-post checks, size) | `enrich/checks/rules.py` |
| Step 2: YC, SEC, IRS, Wayback, DNS check | `enrich/sources/directories.py`, `dns_check.py`, `history.py` |
| Code checks at download time | `enrich/sources/crawl.py` (`payment_scan`), `payments.py`, `wappalyzer.py` |
| AI prompts (used by OpenAI and by Claude through MCP) | `config/prompts/<group>/<name>.json`, `_shared.json` |
| AI answer quality checks | `enrich/ai/apply.py` |
| Prospeo (step 5) | `enrich/providers/` (`run.py`, `apply.py`, `prospeo.py`) |
| Merge rules for directory / provider answers | `enrich/engine/merge.py` (`settle`) |
| Scoring and segments | `config/scoring.json`, `enrich/checks/scoring.py` |

## Checklist for every change

1. Change the data point file **and** the code default together. Check they match:
   ```bash
   .venv/bin/python -W ignore -c "from enrich.checks import datapoints as d; a, b = d.load(), d.defaults(); print([s for s in a if a[s]['steps'] != b[s]['steps']])"
   ```
   It must print `[]`.
2. A new rule: proof must be stated (quote + page link via `sig(value, evidence, url, note)`); ambiguous = `CHECK`,
   never a guessed Yes/No. Exclusions and must-have No need strong proof.
3. Add an offline test (no network): `tests/test_tech_checks.py` (rules, DNS, Wappalyzer, Prospeo with mocks),
   `tests/test_dp_test.py`, `tests/test_client_ai.py`.
4. Run lint and all tests:
   ```bash
   .venv/bin/python -m pyflakes enrich tests && .venv/bin/python -W ignore -m unittest discover -s tests
   ```
5. Show the effect on a real company without spending: `dp_test_run` / `dp_test_record(action="retry")` on
   `dp_test_lead_scoring`, or `.venv/bin/python -m enrich waterfall <domain> --group <group>`.
6. Update the docs: the data point's row in the "Waterfall per data point" table in `docs/SIGNALS.md` (second table
   with that ID), and `docs/MANUAL_REVIEW.md` if it came from the review. INTEGRATIONS.md for MCP changes.

## Things to know

- Rules, prompt and settings changes bump version stamps: saved results re-run the free rules automatically; saved
  AI answers whose prompt changed are re-asked on the next AI run (costs money through OpenAI, $0 through MCP).
- Never call paid services in tests or demos without the user's OK (OpenAI, Prospeo credits).
- The Mac's DNS sometimes fails: network code uses `PublicDNSFallback` (crawl.py).
- After changing MCP tools, the user reconnects with `/mcp` (or a new chat) to load them.
