# ICP Enrichment

Checks a lead list against `profile.yaml` in two separate phases, using free Python scraping and pattern matching first and AI only for what's left:

```
PHASE 1  icp_check.py leads.csv      is it our ICP?  (must-haves and exclusions only)
PHASE 2  signals_check.py leads      fit, buying and weak signals + lead score  (only for ICP companies)
RESULT   lists/leads/output/leads_enriched.csv   your list, every row kept, + ICP YES/NO + score + findings
```

| Doc | What it covers |
|---|---|
| [PIPELINE.md](PIPELINE.md) | The pipeline as diagrams: both phases, the verdict, two-step AI, scoring, storage |
| [HOW_IT_WORKS.md](HOW_IT_WORKS.md) | Step by step: what triggers what, and the workflow of both phases |
| [SIGNALS.md](SIGNALS.md) | How each must-have, exclusion and signal is checked |
| [PLAN.md](PLAN.md) | The approach, build status and roadmap |

**Status:** downloading, rules, both phases, the final list and lead scoring are built and tested on a 14-company sample. The AI step, free directories (SEC, YC, IRS) and web search are next; until the AI step exists, some verdicts stay PENDING and scores are lower than they will be. See [PLAN.md](PLAN.md#8-status-and-roadmap).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Commands

The work runs in two separate phases. See [HOW_IT_WORKS.md](HOW_IT_WORKS.md) for the full flow.

```bash
# PHASE 1: is it our ICP? Must-haves and exclusions only, for every company
.venv/bin/python icp_check.py leads.csv                 # same as: python -m enrich icp leads.csv

# PHASE 2: fit, buying and weak signals, only for companies phase 1 passed
.venv/bin/python signals_check.py leads                 # same as: python -m enrich signals leads
.venv/bin/python signals_check.py leads --only-yes      # skip companies still waiting on an AI check

# both phases, one after the other
.venv/bin/python -m enrich run leads.csv

# useful options (both phases)
--limit 100        # phase 1: first 100 rows only
--workers 100      # more companies at the same time
--refresh          # download again, ignore the cache
--list q4_leads    # phase 1: choose the list folder name

# see all lists: ICP yes / needs check / no, signals done, disk use
.venv/bin/python -m enrich lists

# changed a rule? re-apply rules to saved pages, no downloading
.venv/bin/python -m enrich rules leads                  # both phases
.venv/bin/python -m enrich rules leads --phase icp      # phase 1 only

# see every answer and its proof for one company (both phases)
.venv/bin/python -m enrich explain linear.app

# delete raw pages that are no longer needed (always asks first)
.venv/bin/python -m enrich clean leads

# list the AI prompts, or show the full message for one
.venv/bin/python -m enrich prompts
```

## Where everything is saved

Every list gets its own folder under `lists/`, named after the CSV file:

```
lists/
  leads/                    from leads.csv
    input.csv               copy of the list you gave
    raw/                    scraped data for this list only
      linear.app/
        home.html.gz        one file per page (slimmed source code)
        pricing.html.gz
        ...
        jobs.json.gz        open jobs from the job board
    cache.sqlite            crawl records and results for this list
    output/
      icp_check.csv         phase 1: is it ICP, and why
      icp_evidence.jsonl    phase 1: proof for every must-have and exclusion
      icp_ai_queue.jsonl    phase 1: must-have and exclusion prompts still to run
      signals.csv           phase 2: signals for ICP companies
      signals_evidence.jsonl
      signals_ai_queue.jsonl
  q4_leads/                 another list, fully separate
    ...
```

Running the same CSV again reuses its folder and skips companies already downloaded. A different CSV gets a new folder. If the same company is in two lists, each list keeps its own copy, so deleting one list never affects another.

The CSV needs a domain or website column. A company name and employee size column are used if present. Domains are cleaned (`https://www.acme.com/about` becomes `acme.com`) and duplicates are removed.

## What gets downloaded

For each domain:

1. The homepage (tries `https://`, then `https://www.`, then `http://`)
2. Important pages found through homepage links, then the sitemap, then common paths like `/pricing`:
   about, team, pricing, careers, privacy, terms, legal, contact, security / subprocessors, blog, engineering blog, integrations, customers, investor relations, locations, shipping, donate, menu
3. One more level of links for pages still missing (for example the team page linked from the about page)
4. Open jobs from the company's job board through free public feeds (Greenhouse, Lever, Ashby, Workable)

The tool respects `robots.txt`, skips files over 3 MB and gives each site up to 90 seconds. A typical site takes 11 to 16 requests.

A finished site is never downloaded twice unless you pass `--refresh`, so a stopped run picks up where it left off.

## Saving disk space

Three things keep storage small:

1. **Pages are slimmed before saving.** Styles, inline scripts, icons, comments, embedded images and unused HTML attributes are removed. Text, links, script sources, structured data and page layout (header, footer, headings) are kept, so rules can still be re-run. In testing this made pages 85% smaller with identical rule results.
2. **Deleting is offered, never automatic.** At the end of a run, the tool shows which raw pages are no longer needed and how much space they use, then asks. The answer defaults to **no**: pressing Enter, Ctrl+C or running without a terminal keeps everything.

   ```
   Raw pages that are no longer needed
   Reason     Companies    Size
   excluded           1  121 KB
   Delete raw pages for 1 companies (121 KB)? [y/n] (n):
   ```

   | Option | What it offers to delete |
   |---|---|
   | `--keep useful` (default) | Raw pages of finished companies: dead, excluded or failed a must-have |
   | `--keep none` | All raw pages in the list. Results, proof and AI context stay in the database. |
   | `--keep all` | Doesn't offer anything |
   | `--yes` | Deletes without asking (only for scheduled runs) |

3. **Results are compressed** in the database (about 4 KB per company).

You can also clean up later. It asks the same way:

```bash
.venv/bin/python -m enrich clean leads               # finished companies only
.venv/bin/python -m enrich clean leads --keep none   # all raw pages in this list
```

Once a company's raw pages are deleted, `rules` keeps its earlier result. Use `run --refresh` to download it again.

Rough sizes for 50,000 rows:

| | Full pages | Slim pages | After `clean --keep none` |
|---|---|---|---|
| Saved pages | about 28 GB | about 4 GB | 0 |
| Database | about 200 MB | about 200 MB | about 200 MB |

## Tips before a big run

- **Use public DNS.** Some home and office routers can't look up certain real domains. In testing, the router failed on 4 of 14. The tool double-checks with public DNS and marks those "not reached, retry" rather than failing them, but they still won't be checked. Setting this Mac's DNS to 1.1.1.1 or 8.8.8.8 avoids it.
- **Pilot first.** Run 500 rows with `--limit 500` to see speed, block rate and the column mapping before the full list.
- **Check the scoring.** "Tax deadline coming up" adds 2 points to almost every company around April and October deadlines. Set it to `"none"` in scoring.json if you don't want that.

## Site status

| Status | Meaning | Effect |
|---|---|---|
| Live | Homepage loaded | Rules run |
| Dead | Domain doesn't exist (confirmed by public DNS), or page not found (404 / 410) | ICP = NO (no live website) |
| Blocked | Bot protection (Cloudflare and similar) | ICP = PENDING, retry with a real browser |
| Unreachable | Timeout, other error, or your network's DNS can't find a domain that public DNS can | ICP = PENDING, retry later |

Blocked and unreachable companies are never failed.

## What the rules answer

Every answer is one of:

- **Yes**: proof found (saved with the quote and page link)
- **No**: proof of the opposite found
- **Needs check**: a clue was found, but AI or a person must confirm it
- **Unknown**: nothing found, and it never counts against the company

What the code answers on its own (full detail per signal in [SIGNALS.md](SIGNALS.md)):

| Phase | Data point | How |
|---|---|---|
| 1 | Based in the US / mainly outside the US | US address with ZIP code, structured address data, governing law, "a Delaware corporation", foreign legal endings, country domains |
| 1 | Live website | Loads, not parked, not "coming soon" |
| 1 and 2 | Fintech tools (Stripe, Gusto, Rippling, Deel, Mercury, Brex, Ramp, Carta, Pulley) | Stripe scripts in the page code; tool names in privacy, terms and subprocessor pages and in job posts. Integration wording ("connect your Stripe account") becomes **Needs check**, not Yes. |
| 1 | Real transaction activity | Prices, checkout scripts, app store links, open jobs |
| 1 | Series C or later / public | Funding sentences on the company's own pages, stock tickers, investor relations page |
| 1 | Accounting firm, non-profit, holds physical stock | Clues from titles, structured data, 501(c)(3) text, store platforms and stock labels. Most become **Needs check** for AI. |
| 2 | Delaware C-Corp and entity type | Legal name in the footer + governing law in the terms |
| 2 | Venture-backed | Funding sentences, investor mentions |
| 2 | QuickBooks, NetSuite and other competitors | Job post mentions (usage) vs site mentions (often integrations) |
| 2 | Paid pricing | Prices like "$49 / month" on the pricing page |
| 2 | YC or Techstars | "Backed by YC", batch codes next to the company name, YC directory link |
| 2 | Hiring signals | Job board feed: role count, finance, ops, bookkeeper, audit and manufacturing roles |
| 2 | Growth numbers, remote-first, early adopter | Text patterns, Product Hunt links, engineering blog |
| 2 | Local or physical business | Structured business type, opening hours, menu or locations pages |
| 2 | Pre-revenue, multi-currency, Shopify without stock | Waitlist wording, currency mix, print-on-demand or digital goods |
| 2 | Tax deadline coming up | Calendar (45 days before Apr 15 and Oct 15) |

## What's left for AI

Questions that need judgment are answered by AI, using prompt files in `prompts/`. There is **one JSON file per data point** (62 files), each with the same sections: task, definition, yes / no / unknown rules, watch-outs, where to look, output schema and examples. Shared rules for all prompts are in `prompts/_shared.json`.

The rules decide which prompts each company needs. Every company still in the running gets a core set (business type, industry, AI core and similar). A prompt is also added whenever a rule returns "Needs check". Companies that are dead, excluded or failed get no prompts.

```bash
.venv/bin/python -m enrich prompts                                   # list every prompt
.venv/bin/python -m enrich prompts --show fit/uses_stripe_billing    # show one prompt in full
.venv/bin/python -m enrich prompts --show fit/uses_stripe_billing --domain resend.com   # filled with real page text
```

See [HOW_IT_WORKS.md](HOW_IT_WORKS.md#step-5-prepare-the-ai-step) for which trigger adds which prompt.

## Outputs

All in `lists/<list>/output/`:

| File | What's in it |
|---|---|
| **`<list>_enriched.csv`** | **The main file.** Every row of your original list, in the same order, with your original columns, plus: ICP (YES / NO / PENDING), ICP status, Why, Lead score, Lead tier, Score breakdown, Segment, signals found and a column per data point. No row is ever removed: duplicates are marked "Duplicate of row N" and rows without a usable domain are kept as NO with the reason. Updated after each phase. |
| `icp_check.csv` | Phase 1: one row per company. "Is it ICP?" (Yes, Yes with some must-haves unconfirmed, Needs AI check, No, Unknown: site not reached), why, and a column per must-have and exclusion. Sorted Yes first. |
| `signals.csv` | Phase 2: one row per ICP company. Fit, buying and weak signals found, funding stage, partners, competitors, open roles, and a column per signal. Sorted by most signals. |
| `*_evidence.jsonl` | Every answer with its proof quote and page link |
| `*_ai_queue.jsonl` | The prompts and trimmed page text for the AI step |

## Lead scoring

After phase 2, each company gets a score from the signals answered **Yes**:

| Weight in profile.yaml | Points |
|---|---|
| High | 3 |
| Medium | 2 |
| Low | 1 |
| Weak fit signal | -2 |

"Needs check" and "Unknown" count as 0. Weights, the weak-signal penalty and the Strong fit cut-off (12 for now) are in [scoring.json](scoring.json), listed by signal name, so you can change them without touching code. Profile rules are built in:
- "No finance person visible" counts medium for SMB, low for MM and not at all for ENT.
- "Has a finance lead" counts only for MM and ENT.
- A local professional services firm isn't also penalised for being local.

Segment comes from the employee count in your list (1 to 5 SMB, 6 to 25 MM, 26+ ENT, lower band on a boundary, SMB if missing).

## Code

```
icp_check.py      runs phase 1
signals_check.py  runs phase 2
enrich/
  __main__.py   commands: icp, signals, run, lists, rules, explain, clean, prompts
  phase1_icp.py     phase 1: ICP check (must-haves and exclusions)
  phase2_signals.py phase 2: signals for companies that passed
  common.py     helpers shared by both phases
  final.py      the final list: all original rows + findings
  scoring.py    lead score from scoring.json
  inputs.py     reads the CSV, cleans domains, removes duplicates
  crawl.py      finds and downloads pages, robots.txt, sitemap, job board feeds
  rules.py      all pattern-matching rules, and which AI prompts each company needs
  prompts.py    loads prompts/*.json and builds the AI messages
  dashboard.py  live terminal view
  store.py      one list's raw pages and SQLite cache
```
