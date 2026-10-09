# PuzzleLeadScoring

Checks a lead list against Puzzle's ideal customer profile ([profile.yaml](config/profile.yaml)) and scores the companies that fit. Free Python scraping, pattern matching and free official directories (YC, SEC, IRS) do most of the work; AI is only used for what's left.

```mermaid
flowchart LR
    L["Your list<br/>leads.csv"] --> P1["Phase 1: Is it our ICP?<br/>icp_check.py<br/>must-haves and exclusions"]
    P1 -->|"ICP companies"| P2["Phase 2: Signals + lead score<br/>signals_check.py"]
    P1 --> R["leads_enriched.csv<br/>every row kept + ICP YES/NO<br/>+ score + findings"]
    P2 --> R

    classDef key stroke:#2d55c8,stroke-width:2px
    class P1,P2,R key
```

| Doc | What it covers |
|---|---|
| **README.md** (this file) | Setup, commands, configuration, project files, status and roadmap |
| [PIPELINE.md](docs/PIPELINE.md) | How the pipeline works, step by step with diagrams: downloading, rules, verdict, AI, scoring, outputs, storage |
| [SIGNALS.md](docs/SIGNALS.md) | How each must-have, exclusion and signal is checked |
| [INTEGRATIONS.md](docs/INTEGRATIONS.md) | The API, the MCP server, and exchanging lists with Clay |

---

## Ground rules

These come from config/profile.yaml and shape everything else.

- **"We don't know" is not "no".** A company is never failed, excluded or marked down because something couldn't be found. Only proof counts.
- **Every answer comes with proof:** a short quote and the page it came from.
- **Cheap checks first.** Free scraping and rules run first, then free directories (YC, SEC, IRS); AI and web search only run for companies still in the running and questions still open.
- **Code does the scoring.** The same input always gives the same score, and changing a weight rescores everything in seconds without downloading again.
- **Your list is never trimmed.** Every original row stays in the final file, in order, with your original columns.

---

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Try it on the 14-company test list:

```bash
.venv/bin/python icp_check.py examples/sample_leads.csv
.venv/bin/python signals_check.py sample_leads
```

Your CSV needs a domain or website column. A company name and employee count column are used if present (common names like "Domain", "Website", "Company Name", "Employee Size" are recognised).

---

## Commands

```bash
# PHASE 1: is it our ICP? Must-haves and exclusions, for every company
.venv/bin/python icp_check.py leads.csv                 # same as: python -m enrich icp leads.csv

# AI for phase 1: settles PENDING companies (asks before spending; needs OPENAI_API_KEY)
.venv/bin/python ai_check.py leads --phase icp          # same as: python -m enrich ai leads --phase icp

# PHASE 2: fit, buying and weak signals + lead score, only for companies phase 1 passed
.venv/bin/python signals_check.py leads                 # same as: python -m enrich signals leads
.venv/bin/python signals_check.py leads --only-yes      # skip companies still waiting on an AI check

# AI for phase 2: AI-only signals, industry, company size (asks before spending)
.venv/bin/python ai_check.py leads --phase signals

# phases 1 and 2 without AI, one after the other
.venv/bin/python -m enrich run leads.csv
```

The AI step uses OpenAI (gpt-4o-mini). The key is set once in `.env` (git-ignored, readable only by you):

```bash
.venv/bin/python -m enrich settings --openai-key sk-...
```

AI options:

| Option | What it does |
|---|---|
| `--limit 20` | First 20 companies only |
| `--no-web` | Skip the web search step |
| `--no-search-signals` | Phase 2: skip the 9 signals only web search can answer (recent funding, new investor, QuickBooks complaints...). They are the most expensive part, about $0.10 to $0.30 per ICP company |
| `--redo` | Ask again where an answer is saved |
| `--yes` | Don't ask before spending |

| You want to | Run |
|---|---|
| See all lists: ICP yes / needs check / no, signals done, disk use | `python -m enrich lists` |
| See every answer and its proof for one company | `python -m enrich explain linear.app` |
| Re-check saved pages after changing a rule (no downloading) | `python -m enrich rules leads` (add `--phase icp` or `--phase signals`) |
| Continue a stopped run | Run the same command again; finished companies come from the cache |
| Delete raw pages no longer needed (always asks first) | `python -m enrich clean leads` |
| Measure accuracy against the right answers | `python -m enrich evaluate leads --labels labels.csv` (blank file: `python -m enrich evaluate --template labels.csv`) |
| See every run of a list: when, counts, AI cost | `python -m enrich runs leads` |
| List the AI prompts | `python -m enrich prompts` |
| See the exact AI message for a company | `python -m enrich prompts --show fit/venture_backed_stage --domain resend.com` (add `--step 2` for the web search step) |
| See the free directories, or look up one company in YC, SEC and IRS | `python -m enrich directories` / `python -m enrich directories acme.com --legal-name "Acme, Inc."` |

| Option | What it does |
|---|---|
| `--limit 100` | Phase 1: only the first 100 rows |
| `--list q4_leads` | Phase 1: choose the list folder name (default: the CSV name) |
| `--workers 100` | More companies at the same time (default 50) |
| `--refresh` | Download again, ignoring the cache |
| `--keep useful / none / all` | What the clean-up question offers to delete (see [PIPELINE.md](docs/PIPELINE.md#10-storage-and-clean-up)) |
| `--yes` | Clean-up: delete without asking. AI step: run without the cost question (only for scheduled runs) |
| `--no-directories` | Skip the free directories (YC, SEC, IRS) and old-site snapshots for this run |
| `--no-browser` | Don't use the real browser (your Google Chrome) for sites that need JavaScript or check for bots |

Results land in `lists/leads/output/`. The main file is **`leads_enriched.csv`**; [PIPELINE.md](docs/PIPELINE.md#9-outputs) lists every column.

---

## Integrations

Other tools can use the pipeline too. Details and setup steps: [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

| You want to | Run |
|---|---|
| See the API key and save keys once (OpenAI, Clay webhook) | `python -m enrich settings` |
| Start the API (Clay and other tools call it) | `python -m enrich serve` |
| List Clay tables / run one as a list | `python -m enrich pull` / `python -m enrich pull t_abc123 --list q4` |
| Send a finished list to a Clay table | `python -m enrich push leads` |
| Use it from Claude Code | Approve **puzzle-lead-scoring** once in `/mcp` (registered in `.mcp.json`) |

---

## Tips before a big run

- **DNS problems are handled.** This Mac's DNS sometimes fails on real domains (6 of 10 in one test). The tool now falls back to public DNS (Google, then Cloudflare) automatically, with the usual certificate checks.
- **Pilot first.** Run 500 rows with `--limit 500` to see speed, block rate and the column mapping before the full list.
- **Check the scoring.** "Tax deadline coming up" adds 2 points around the April 15 and October 15 deadlines, only for companies confirmed as C-Corps. Set it to `"none"` in config/scoring.json if you don't want it.
- **Optional: set `SEC_CONTACT_EMAIL`** in `.env`. SEC then serves Form D documents, which add the amount raised (a $100M+ round excludes as Series C or later) and the year of incorporation ("Incorporated in the last 12 months").

---

## Configuration

`config/profile.yaml` is written in sentences. It was turned into three things you can read and change:

| File | What it holds | Change it when |
|---|---|---|
| [enrich/rules.py](enrich/rules.py) | The pattern-matching rules: what counts as proof for each data point | A rule finds wrong answers or misses clear ones |
| [config/prompts/](config/prompts/) | One JSON prompt file per data point (62), plus shared rules, the AI provider and model names in `_shared.json` | AI should judge a data point differently, or you want another model |
| [config/scoring.json](config/scoring.json) | Points per signal (high 3, medium 2, low 1, weak fit -2), Strong fit cut-off (12), size bands | You want different weights or tiers |

After changing rules or scoring, run `python -m enrich rules leads` to update the results without downloading again.

---

## Project files

```
README.md             start here
icp_check.py          runs phase 1
signals_check.py      runs phase 2
ai_check.py           runs the AI step for either phase
.mcp.json             registers the MCP server for Claude Code
.env                  your keys, set once (not in git)
.env.example          blank list of every key, for teammates
cache/                free directory downloads, shared by all lists (not in git)
requirements.txt
config/               everything you edit to change behaviour
  profile.yaml        the ICP definition (never changed by the tool)
  scoring.json        lead score weights, cut-off, size bands
  prompts/            one AI prompt per data point + _shared.json
docs/
  PIPELINE.md         how the pipeline works, with diagrams
  SIGNALS.md          how each data point is checked
  INTEGRATIONS.md     API, MCP server, Clay
examples/
  sample_leads.csv    14-company test list
enrich/
  __main__.py         all commands
  phase1_icp.py       phase 1: ICP check
  phase2_signals.py   phase 2: signals and score
  directories.py      free directories: YC, SEC EDGAR, IRS non-profit list
  history.py          old copies of company sites (Internet Archive)
  evaluate.py         accuracy against labels
  runlog.py           run log per list (runs.jsonl)
  ai.py               AI step: calls OpenAI, step 1 then web search, cost estimate
  ai_apply.py         turns AI answers into signal values with the confidence rules
  api.py              HTTP API (python -m enrich serve)
  mcp_server.py       MCP server (python -m enrich mcp)
  service.py          runs the pipeline from code, for the API and MCP
  clay.py             pull from / push to Clay
  settings.py         keys from .env, set once
  inputs.py           reads the CSV, cleans domains
  crawl.py            finds and downloads pages, job feeds, public DNS check
  rules.py            pattern-matching rules; which AI prompts each company needs
  prompts.py          loads prompt files and builds AI messages (both steps)
  scoring.py          lead score from scoring.json
  final.py            the final list: every original row + findings
  dashboard.py        live terminal view
  store.py, common.py storage and shared helpers
lists/<list>/         created per list: input.csv, raw/, cache.sqlite, runs.jsonl, output/  (not in git)
```

---

## Status and roadmap

### Built and tested (on a 14-company sample and a 10-company accuracy test)

| Part | Status |
|---|---|
| Read any CSV, clean domains, keep every row | Done |
| Phase 1 and phase 2 as separate scripts | Done |
| Downloading pages, job feeds, robots.txt, public DNS check, slim saving | Done |
| Rules for all 4 must-haves, 6 exclusions and 47 signals (21 fit, 14 buying, 12 weak) | Done. Yes / No only when confirmed; guesses stay empty or Needs check |
| Buying signals from free sources: Form D in the last 6 months, new YC batch, LLC-to-Inc. (SEC former names, old footers in the Internet Archive), pricing launched since last year (Internet Archive) | Done; the rest of the buying signals come from web search |
| Real-browser fallback (your Google Chrome) for JavaScript-only sites and short bot checks | Done; Khan Academy went from 209 to 8,682 characters of text. Hard Cloudflare blocks still fail |
| Reasoning column ending with the profile's "Funding stage: <value> (<basis>)" line | Done (built by code, no AI cost) |
| `evaluate` (accuracy against labels) and `runs` (run log with AI cost) | Done; the 10-company test scores 9 of 9 decided, 0 good companies wrongly NO |
| Final list with ICP YES / NO / PENDING, lead score, tier and segment | Done |
| Free directories: YC (by domain), SEC EDGAR (public, Form D funding, Delaware, address), IRS non-profit list, with match-strength rules | Done; on the test list it caught HubSpot as public and confirmed Puzzle's Delaware status and funding |
| 62 AI prompt files with a web search step | Written |
| AI step with OpenAI (gpt-4o-mini): our data first, web search only if needed, cost estimate and confirmation, answers kept when rules re-run, a quote required for every Yes / No | Done; $0.11 for the 10-company test |
| Live dashboards, `explain`, `lists`, `rules`, `clean`, ask before deleting | Done |
| Version control | Done: private GitHub repo `PuzzleLeadScoring` |
| API (one company, whole lists, push and pull) with a key set once | Built and tested locally |
| MCP server for Claude Code / Desktop | Built and tested with an MCP client; needs your one-time approval |
| Clay: pull tables, push to a webhook, per-row HTTP column | Pull tested on your workspace; push tested against a stand-in webhook; per-row needs a public address |

### Next, in order of impact

1. **Labelled list from Puzzle:** about 50 current customers and 50 lost or "not a fit" deals. Run them and `evaluate`, to measure accuracy and set the Strong fit cut-off from data instead of the current 12.
2. **Pilot on 500 real rows** to measure speed, block rate and cost.
3. **Cheaper AI, later:** several data points per call, OpenAI's half-price Batch API, and one web search for several news signals. Each needs an `evaluate` check before use.

### Then

4. **Follow investor and careers subdomains** such as `ir.hubspot.com`.
5. **More directories:** Techstars and other accelerator portfolios.
6. **A server** for the API, so Clay can call it row by row.
7. Optional: Clay (already connected) for headcount and funding on PENDING companies; monthly re-checks of old lists; a shared page for the team to browse results.

### Decisions and open questions

| Question | Status |
|---|---|
| AI provider and models | OpenAI gpt-4o-mini for both steps (`providers.openai` in `config/prompts/_shared.json`). Claude settings are kept there as an alternative but not wired up |
| OpenAI API key | Saved once in `.env` (git-ignored) |
| One AI call per data point, or grouped | One call per data point for now; cheaper options are on the roadmap |
| Large rounds | A confirmed single round of $100M+ or a $1B+ valuation counts as "Series C or later" (`late_stage_proof` in config/scoring.json) |
| Undecided companies | PENDING until the AI check or a retry settles them |
| Weak fit points | -2 each (in config/scoring.json) |
| Strong fit cut-off | 12 for now; to be set after the 100-company check |
| Real lead list | A sample of 50 to 100 rows would help tune the rules |
| Old test folders | `data/`, `out/` and `lists/sample_leads/` are out of date; waiting on your OK to delete them |
