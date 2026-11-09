# Puzzle Lead Scoring

Checks a list of companies against Puzzle's ideal customer profile, scores the ones that fit, and tells sales how to approach each one. Free Python scraping, pattern matching and free official directories (YC, SEC, IRS) do most of the work; AI is only used for what's left. Every answer comes with a short quote and the page it came from.

All documentation is in **[docs/DOCUMENTATION.md](docs/DOCUMENTATION.md)**.

## Ground rules

- **"We don't know" is not "no".** A company is never failed or marked down because something couldn't be found. Only proof counts.
- **Every answer comes with proof:** a quote and its page.
- **Cheap checks first.** Free scraping, rules and directories run first; AI and web search only for what is still open.
- **Code does the scoring.** The same input always gives the same score.
- **Your list is never trimmed.** Every original row stays in the final file, in order.

## Two pipelines

- **services** (default): operators (founders up to Series C, SMB owners) who want the bookkeeping done for them.
- **product** (`--icp product`): early-stage startups (pre-seed to Series B) for the platform.

## Quick setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m enrich settings --openai-key sk-...   # keys live in .env, never committed

# first test run on the 14-company sample
.venv/bin/python icp_check.py examples/sample_leads.csv
.venv/bin/python signals_check.py sample_leads
.venv/bin/python -m unittest discover -s tests
```

Results land in `lists/<list>/output/`; the main file is `<list>_enriched.csv`.

## Three ways to use it

| Who | How | Start here |
| --- | --- | --- |
| Sales and GTM team | Scores appear as columns in Clay tables | [Clay](docs/DOCUMENTATION.md#clay) |
| Ops or GTM lead | Ask Claude Code or Claude Desktop in plain English: "check acme.com", "score this list" | [MCP server](docs/DOCUMENTATION.md#mcp-server-claude-code-claude-desktop) |
| Engineer | Command line or API for big lists, schedules, or the CRM | [Commands](docs/DOCUMENTATION.md#3-commands), [API](docs/DOCUMENTATION.md#api) |

## Documentation

| Section | What it covers |
|---|---|
| [1. Overview](docs/DOCUMENTATION.md#1-overview) | What it does, ground rules, the two ICP pipelines, the three ways to use it |
| [2. Setup](docs/DOCUMENTATION.md#2-setup) | Install, keys and settings, configuration files, project files |
| [3. Commands](docs/DOCUMENTATION.md#3-commands) | Every command and option, tips before a big run |
| [4. How the pipeline works](docs/DOCUMENTATION.md#4-how-the-pipeline-works) | Downloading, rules, verdict, AI, scoring, outputs, storage, architecture |
| [5. The ICP models](docs/DOCUMENTATION.md#5-the-icp-models) | Roles and points, services signals, routes, changing the model, the services decisions |
| [6. Data point reference](docs/DOCUMENTATION.md#6-data-point-reference) | All 69 data points (57 + services facts S1–S12) and the waterfall per data point |
| [7. Integrations](docs/DOCUMENTATION.md#7-integrations) | API, Clay, MCP server, Claude Code skills, list viewer |
| [8. Testing](docs/DOCUMENTATION.md#8-testing) | Smoke test, trace, waterfall, manual review, accuracy, regression checklist |
| [9. Hand-off to Puzzle](docs/DOCUMENTATION.md#9-hand-off-to-puzzle) | Who uses what, everyday use, costs and safety |
| [10. Decisions and reviews](docs/DOCUMENTATION.md#10-decisions-and-reviews) | Manual review, decisions per data point, build plan, the 26-deal test |
| [11. Status and open items](docs/DOCUMENTATION.md#11-status-and-open-items) | Status and roadmap, before go-live, open decisions |
| [12. Improvements log](docs/DOCUMENTATION.md#12-improvements-log) | What the pipeline changed about itself |
