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
git clone https://github.com/aligmirza/PuzzleLeadScoring.git
cd PuzzleLeadScoring
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m enrich settings --set LEADMAGIC_API_KEY=...   # keys live in .env, never committed

# first test run on the 14-company sample
.venv/bin/python scripts/icp_check.py examples/sample_leads.csv
.venv/bin/python scripts/signals_check.py sample_leads
.venv/bin/python -m unittest discover -s tests
```

Every key is optional: without one, that step is skipped. Keys can also be added without a terminal, in the Claude Desktop extension's Configure screen or on the hosted connector's `/settings` page (below).

Works on macOS, Linux and Windows (Python 3.10 or later; tested on 3.14). On Windows, use `py -m venv .venv` and `.venv\Scripts\python` in place of `.venv/bin/python` (PowerShell or Command Prompt); everything else is the same.

Results land in `lists/<list>/output/`; the main file is `<list>_enriched.csv`.

## Claude Desktop (one click)

1. **Get a copy and set it up once:** Quick setup above, up to `pip install`. A ZIP from GitHub (Code > Download ZIP) works too; unzip it to a folder without spaces in its name.
2. **Install the extension:** double-click `connectors/puzzle-lead-scoring.mcpb` and pick the project folder.
3. **Add keys (optional):** Settings > Extensions > Puzzle Lead Scoring > **Configure**. Each box is optional and shows an example; keys are kept in the computer's keychain. Keys already in the folder's `.env` work too.
4. **Restart Claude Desktop** (quit fully, Cmd+Q on Mac), then ask "show provider status".

**Moving to a new copy** (a fresh clone or download): set it up (step 1), change **PuzzleLeadScoring folder** under Configure to the new folder, and restart Claude Desktop. Keys typed into Configure carry over; keys only in the old copy's `.env` don't, so copy that file across or paste them into Configure. Saved lists stay in the old copy's `lists/` and `cache/`: copy those folders across, or set `DATA_DIR` in `.env` so every copy shares one data folder.

**Updating:** `git pull` in the folder, `.venv/bin/pip install -r requirements.txt` if `requirements.txt` changed, double-click the `.mcpb` again if it changed, then restart Claude Desktop.

**If it doesn't start:** Claude Desktop's log for the extension says why. "No executable file at …/.venv/bin/python" means the folder wasn't set up (step 1) or Configure points at the wrong folder.

**Hosted connector for the whole team** (claude.ai, nothing to install): `python -m enrich mcp --http --host 0.0.0.0 --public-url https://<address>`; keys at `https://<address>/settings` with the admin password (`MCP_ADMIN_PASSWORD` in `.env`). Details in [docs/HANDOFF.md](docs/HANDOFF.md).

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
| [9. Decisions and reviews](docs/DOCUMENTATION.md#9-decisions-and-reviews) | Manual review, decisions per data point, build plan, the 26-deal test |
| [Hand-off guide](docs/HANDOFF.md) | For Puzzle: a summary for leadership, how to use it with Claude, then setup, everyday use, costs and changing the model |
