# How It Works

This explains what the enrichment tool does, step by step: what starts each step, what each step decides, and what it hands to the next one.

For the full plan, see [PLAN.md](PLAN.md). For how each signal is checked, see [SIGNALS.md](SIGNALS.md). For setup and commands, see [README.md](README.md).

---

## The big picture

The work is split into **two separate phases**, each with its own script. Phase 1 decides whether a company is our ICP. Phase 2 only runs on companies that passed, so no time or AI is spent on signals for companies we'd never contact.

```
 PHASE 1: IS IT OUR ICP?                          icp_check.py  (python -m enrich icp leads.csv)
 ───────────────────────
 leads.csv ──► Read the list
                  │
                  ▼
               Download only the pages must-haves and exclusions need
               (home, about, pricing, careers, privacy, terms, legal, contact,
                security, investor relations, donate, shipping) + open jobs
                  │
                  ▼
               Must-have and exclusion rules only (free)
                  │
                  ▼
               Verdict per company ──► Yes / Yes (some must-haves unconfirmed) / Needs AI check / No / Unknown: site not reached
                  │
                  ▼
               AI prompts for must-haves and exclusions only, where a rule said "Needs check"
                  │
                  ▼
               output/icp_check.csv, icp_evidence.jsonl, icp_ai_queue.jsonl  ──►  offer to clean up (asks first)


 PHASE 2: SIGNALS                                 signals_check.py  (python -m enrich signals leads)
 ────────────────
 Companies phase 1 marked Yes, Yes (some unconfirmed) or Needs AI check
                  │
                  ▼
               Download the extra pages signals need (team, blog, engineering blog,
               integrations, customers, locations, menu), starting from phase 1's saved homepage
                  │
                  ▼
               Fit, buying and weak signal rules (free)
                  │
                  ▼
               AI prompts for signals (business type, industry, AI core... plus anything marked "Needs check")
                  │
                  ▼
               output/signals.csv, signals_evidence.jsonl, signals_ai_queue.jsonl  ──►  offer to clean up (asks first)
```

Each phase works on many companies at the same time (50 by default). `python -m enrich run leads.csv` runs both phases one after the other.

### Why two phases

| | Phase 1: ICP check | Phase 2: signals |
|---|---|---|
| Runs on | Every company in the list | Only companies that passed phase 1 |
| Pages downloaded | 12 page types needed for must-haves and exclusions | 7 more page types, added to phase 1's pages |
| Rules | 4 must-haves, 6 exclusions | 21 fit, 14 buying, 12 weak signals |
| AI prompts | Only where a must-have or exclusion needs a check | About 10 to 20 per company |
| In the 14-company test | 6 prompts in total | About 15 prompts per company |

---

## What starts what

| When this happens | It triggers |
|---|---|
| You run `icp leads.csv` (or `icp_check.py`) | Phase 1 for the whole list |
| You run `signals leads` (or `signals_check.py`) | Phase 2 for companies phase 1 passed |
| You run `run leads.csv` | Phase 1, then phase 2 |
| The list folder doesn't exist yet | A new folder `lists/leads/` and a copy of your CSV |
| A company was already downloaded in an earlier run | Downloading is skipped for it; the saved pages are reused |
| You add `--refresh` | Downloading runs again, even for cached companies |
| The homepage loads | The search for the page types the phase needs |
| Important pages are missing from homepage links | A sitemap check |
| Phase 1 pages are still missing after the sitemap | Common addresses are tried (`/pricing`, `/about`, `/careers`, `/privacy`, `/terms`) |
| A downloaded page links to a page that's still missing | One more level of downloading |
| A page links to Greenhouse, Lever, Ashby or Workable | The job board's free public feed is read (phase 1) |
| The homepage doesn't load | No more downloading for that company; its status is set (see step 2a) |
| Your network's DNS can't find a domain | Public DNS (Google, Cloudflare) is asked before calling it dead |
| A rule finds a clue but can't be sure | "Needs check", and that data point's AI prompt is added |
| Phase 1 verdict is No or Unknown | The company is left out of phase 2 |
| Phase 1 verdict is Needs AI check | Included in phase 2, unless you add `--only-yes` |
| Phase 1's pages for a company were deleted | Phase 2 downloads that company again from scratch |
| Every company in a phase is done | Outputs are written, then the clean-up offer |
| You answer **y** to the clean-up question | Raw pages of companies that aren't ICP are deleted |
| You answer anything else, or there's no terminal | Nothing is deleted |
| You change a rule and run `rules leads` | Rules run again on saved pages, both phases, without downloading |
| You run `clean leads` | The clean-up offer only |

---

## Step 1. Read the list

**Started by:** phase 1 (`python -m enrich icp leads.csv`)
**Code:** [enrich/inputs.py](enrich/inputs.py)

Steps 1 to 7 below describe phase 1 in detail. Phase 2 repeats steps 2 to 7 for the companies that passed, with the differences listed in [the big picture](#the-big-picture).

1. Finds the columns by name. Any of these work:
   - domain: Domain, Website, URL, Company Domain
   - name: Company Name, Company, Name
   - size: Employee Size, Employees, Headcount
2. Cleans every domain: `https://www.acme.com/about?x=1` becomes `acme.com`.
3. Drops rows with no valid domain, and drops duplicates (the first one is kept).
4. Creates the list folder and copies your CSV into it:

```
lists/leads/
  input.csv      copy of your list
  raw/           (filled in step 2)
  cache.sqlite   (filled in steps 2 and 3)
  output/        (filled in step 6)
```

**Hands to step 2:** the clean list of companies.

---

## Step 2. Download each website

**Started by:** step 1, once per company, about 50 companies at a time
**Skipped when:** the company was already downloaded in this list (unless `--refresh`)
**Code:** [enrich/crawl.py](enrich/crawl.py) (`crawl` for phase 1, `crawl_more` for phase 2)

### 2a. Open the homepage

Tries three addresses in order and stops at the first that works:

```
https://acme.com  ──fails──►  https://www.acme.com  ──fails──►  http://acme.com
```

If none work, the company gets a status and step 2 stops for it:

| What happened | Status | What it means later |
|---|---|---|
| Domain doesn't exist (confirmed by public DNS), or page not found (404, 410) | **Dead** | Fails the "live website" must-have |
| Bot protection (Cloudflare page, 403, 429 and similar) | **Blocked** | Unknown. Retry later with a real browser. |
| Timeout, other error, or your network's DNS fails while public DNS finds the domain | **Unreachable** | Unknown. Retry later. |

Blocked and unreachable companies are never failed. Their result is "Site not reached (retry)".

### 2b. Find the important pages

Looks for these pages, in this order of sources:

```
links on the homepage  ──missing?──►  sitemap.xml  ──still missing?──►  common addresses
```

| Page | Used for |
|---|---|
| About, Team | Funding, investors, team members, location |
| Pricing | Paid pricing, currencies, checkout |
| Careers | Hiring, job board link, remote work |
| Privacy, Terms, Legal, Security | Legal name, state of incorporation, tools used (vendor lists) |
| Contact | Address and location |
| Blog, Engineering blog, Customers | Growth numbers, early adopter signs |
| Integrations | Tools the product connects to (kept apart from tools the company uses) |
| Investor relations | Possible public company |
| Locations, Menu, Shipping, Donate | Local business, restaurant, physical stock, non-profit clues |

Pages that are really the homepage again, or "not found" pages, are thrown away.

### 2c. Go one level deeper

If the team, careers, privacy, terms, legal or security page is still missing, it reads the links on the pages it already downloaded and fetches them.

### 2d. Read open jobs

If any page links to a known job board, its free public feed is read:

| Job board | What we get |
|---|---|
| Greenhouse, Lever, Ashby, Workable | Every open role: title, location, remote or not, full description |
| Rippling | Detected only (this also proves they use Rippling) |

### 2e. Save a slim copy

Before saving, each page is slimmed: styles, inline scripts, icons and unused code are removed. Text, links, script addresses and structured data are kept. This makes pages about 85% smaller with the same rule results.

```
lists/leads/raw/acme.com/
  home.html.gz
  pricing.html.gz
  privacy.html.gz
  ...
  jobs.json.gz
```

**Politeness:** follows each site's `robots.txt`, skips pages over 3 MB, and gives each company at most 90 seconds. A typical site needs 11 to 16 requests.

**Hands to step 3:** the saved pages and jobs.

---

## Step 3. Apply the rules

**Started by:** step 2 finishing a company (or the `rules` command)
**Costs:** nothing, it's pattern matching on saved pages
**Code:** [enrich/rules.py](enrich/rules.py)

Each check gives one answer:

| Answer | Meaning | Effect |
|---|---|---|
| **Yes** | Proof found | Counts |
| **No** | Proof of the opposite found | Counts |
| **Needs check** | A clue, but not certain | Sent to the AI step to confirm |
| **Unknown** | Nothing found | Never counts against the company |

Every Yes and No is saved with a short quote and the page it came from.

The checks run in this order, because later checks use earlier answers:

```
1. Website check          is it live, parked, "coming soon", or needs JavaScript?
2. Location and entity    US address, governing law, legal name, entity type
3. Pricing and sales      prices, checkout, app store, waitlist
4. Tools                  Stripe, Gusto, Rippling, Deel, Mercury, Brex, Ramp, Carta, Pulley, Chase, Wise
                          and competitors (QuickBooks, Xero, NetSuite...)
5. Jobs                   number of roles, finance / ops / bookkeeper roles, remote
6. Funding                stage on own site, investors, stock ticker, YC / Techstars
7. Non-profit             501(c)(3), donations, government domain
8. Physical stock         store platform, stock labels, shipping, print-on-demand
9. Business type          accounting firm clues, local business, opening hours
10. Other fit signals     growth numbers, early adopter, holding company, tax deadline
11. Must-haves            combines the answers above into the four must-haves
12. Weak signals          e.g. LLC with no funding found
```

### How the code avoids false matches

| Situation | What the rule does |
|---|---|
| "Connect your Stripe account" | Integration wording, so **Needs check**, not Yes |
| "The payment processors we work with are: Stripe" | Real usage, so **Yes** |
| "VibeGrade (YC X25)" in a customer story | Not the company's own backing, so ignored |
| A YC partner listed as an angel investor | **Needs check**, not Yes |
| "QuickBooks" in a job post | The company uses it, so **Yes** |
| "QuickBooks" on the integrations page | Probably an integration, so only noted as "mentioned" |
| "Series A, B and C" in one sentence | Not a confirmed early stage, so **Needs check** |
| "Inc." in the footer but no Delaware law | **Needs check** (could be incorporated elsewhere) |

**Hands to step 4:** all answers with their proof.

---

## Step 4. Decide the verdict

**Started by:** step 3 finishing a company (phase 1)

The verdict is decided in this order. The first match wins:

```
Site blocked or unreachable?         ──yes──►  Unknown: site not reached
        │ no
        ▼
Any exclusion confirmed?             ──yes──►  No (excluded)
        │ no
        ▼
Any must-have proven false?          ──yes──►  No (failed a must-have)
        │ no
        ▼
Any must-have or exclusion           ──yes──►  Needs AI check
marked "Needs check"?
        │ no
        ▼
All 4 must-haves confirmed?          ──yes──►  Yes
        │ no
        ▼
                                               Yes (some must-haves unconfirmed)
```

"Needs check" and "Unknown" never make a company a No. Only proof does. "Yes (some must-haves unconfirmed)" is still a Yes, as the profile requires.

**Hands to step 5:** the verdict.

---

## Step 5. Prepare the AI step

**Started by:** step 4, for companies that aren't a No and whose site was reached
**Skipped for:** No (excluded, failed a must-have, dead) and Unknown: site not reached
**Code:** [enrich/prompts.py](enrich/prompts.py) and the `prompts/` folder

### One prompt file per data point

Every AI question lives in its own JSON file. There is one file per data point (62 in total), grouped by folder:

```
prompts/
  _shared.json       rules added to every prompt (role, ground rules, models, message layout)
  index.json         list of all prompts
  must_haves/        4 files, e.g. based_in_us.json
  exclusions/        6 files, e.g. nonprofit_or_government.json
  fit/               21 files, e.g. uses_stripe_billing.json
  buying/            13 files, e.g. raised_last_6_months.json
  weak/              12 files, e.g. pre_revenue.json
  fields/            4 files: vertical, segment, legal entity type, competitors used
  output/            2 files: reasoning (with the funding stage line), fit message
```

Every file has the same sections:

| Section | What it holds |
|---|---|
| `data_point`, `group`, `weight` | What is being decided and how much it counts |
| `stage` | What it reads: website, job posts, web search results, or final output |
| `model` | `small` (Haiku) or `large` (Sonnet) for step 1, set in `_shared.json` |
| `web_search` | Step 2: when it runs (fallback, primary or off), search queries with `{company}`, `{domain}` variables, and good sources |
| `run_when` | The condition that triggers this prompt |
| `input` | Which saved pages it needs |
| `prompt` | `task`, `definition`, `yes_when`, `no_when`, `unknown_when`, `watch_out_for`, `where_to_look` |
| `output` | The exact JSON the AI must return (answer, confidence, quote, source, reason) |
| `examples` | Sample text and the correct answer |

To change how a data point is judged, edit its file. To change a rule for all of them, edit `_shared.json`.

### Which prompts a company gets

Phase 1 only uses prompts in `must_haves/` and `exclusions/`. Phase 2 uses all the others.

| Trigger | Prompts added |
|---|---|
| Every company in phase 2 | Startup selling nationally, B2B SaaS, Local professional services, Billed labour, Vertical, AI core, Manufacturing, Heavy invoicing |
| A rule returned **Needs check** | That signal's prompt (e.g. Accounting firm, Non-profit, Uses Stripe) |
| Location unclear | Based in the US |
| Funding unknown | Venture-backed stage |
| Entity type unknown | Legal entity type |
| Employee count missing from your list | Segment (company size) |
| Team page found | No finance person visible, Finance lead present, Segment |
| Competitor tools mentioned | Competitor tools used |

Prompts that need web search or old site snapshots wait for later steps. The reasoning and fit message prompts run last, after scoring.

### Two steps per data point: our data first, then the internet

Every AI data point is answered in up to two steps:

```
STEP 1  Search OUR data                        cheap, small model (Haiku)
        the saved pages and job posts the prompt needs
            │
            ├── answer found with medium or high confidence ──►  done
            │
            ▼  answer is "unknown", or confidence is low
STEP 2  Search the INTERNET                    Claude with its built-in web search tool (Sonnet)
        company details become variables in the prompt's search queries:
          {company}     Resend
          {domain}      resend.com
          {legal_name}  Resend, Inc. (if step 1 or phase 1 found it)
          {year}        2026
        e.g.  "Resend" raises seed OR "Series A" OR "Series B"
              "Resend" resend.com funding investors
            │
            ▼
        answer + confidence + entity match + sources (URL, title, date)
```

Each prompt file has a `web_search` section that says when step 2 runs:

| Mode | When step 2 runs | Count | Examples |
|---|---|---|---|
| `fallback` | Only if step 1 found nothing or only low confidence | 47 | Delaware C-Corp, venture-backed, accounting firm, non-profit, headcount |
| `primary` | Always, step 1 is skipped (the answer normally only exists on the web) | 10 | Raised in the last 6 months, new board member, QuickBooks complaints |
| `off` | Never (the web can't prove it) | 5 | Live website, no finance person visible, no fintech tools, reasoning, fit message |

**Is it the same company?** Many companies share names, so step 2 must report an **entity match**:

| Entity match | Meaning |
|---|---|
| `domain` | A source mentions or links the company's domain |
| `name_and_location` | Same name plus matching location, founder or product |
| `name_only` | Same name only (unreliable for common names) |
| `none` | Couldn't confirm |

**Confidence** decides whether a web answer counts:

| Confidence | Means | Counts? |
|---|---|---|
| High | Official record (SEC, state registry, IRS), the company's own page, or 2+ independent reputable sources, with entity match by domain | Yes, including exclusions and failed must-haves |
| Medium | One reputable source (major press, Crunchbase, LinkedIn company page, YC directory, job board) with entity match by domain or name and location | Yes, except exclusions and failed must-haves |
| Low | Name-only match, SEO or aggregator page, old information, or sources disagree | No. Kept in the proof file as a hint; the answer stays Unknown |

So a company is only ever made a NO from the web with high confidence and a domain match.

Step 2 only runs for companies still in the running (never for companies already a NO), and each call is capped at 3 searches. All of these rules live in `prompts/_shared.json` (`two_steps`, `web_search_step`), and each data point's queries and good sources are in its own prompt file.

See both steps for a real company:

```bash
.venv/bin/python -m enrich prompts --show fit/venture_backed_stage --domain resend.com            # step 1
.venv/bin/python -m enrich prompts --show fit/venture_backed_stage --domain resend.com --step 2   # step 2
```

### How a message is built

```
system        shared role, ICP summary, ground rules          (same for every call)
context       company, today, facts code already confirmed,   (same for every call of this company,
              the page text these prompts need                  so it is cached after the first call)
instruction   one data point's sections, examples, schema     (changes per call)
```

Facts code already confirmed are passed in, so the AI doesn't contradict them.

See any prompt, or the exact message for a real company:

```bash
.venv/bin/python -m enrich prompts
.venv/bin/python -m enrich prompts --show exclusions/accounting_firm --domain pilot.com
```

**Hands to step 6:** the list of prompts per company and the shared page text.

> The AI call itself isn't built yet. For now the prompts and text are written to `icp_ai_queue.jsonl` (phase 1) and `signals_ai_queue.jsonl` (phase 2), ready for it.

---

## Step 6. Write the outputs

**Started by:** every company in the phase finishing

```
lists/leads/output/
  leads_enriched.csv       THE MAIN FILE: every original row and column + ICP, lead score and findings
  icp_check.csv            phase 1: one row per company, the ICP verdict and why
  icp_evidence.jsonl       phase 1: every must-have and exclusion answer with its quote and page link
  icp_ai_queue.jsonl       phase 1: must-have and exclusion prompts still to run
  signals.csv              phase 2: one row per ICP company, its signals
  signals_evidence.jsonl   phase 2: every signal answer with its proof
  signals_ai_queue.jsonl   phase 2: signal prompts still to run
```

### The main file: `leads_enriched.csv`

Your list exactly as you gave it: same rows, same order, same columns. The findings are added as new columns on the right. It's rewritten after each phase, so it always shows the latest state.

| Added column | Example |
|---|---|
| ICP | YES, NO or PENDING |
| ICP status | All 4 must-haves confirmed, no exclusion found |
| Why | Mainly operates outside the US: foreign legal entity: API Hero Ltd. |
| Lead score | 21 (after phase 2) |
| Lead tier | Strong fit, Weak fit, Not ICP, or "Not scored: website not reached (retry)" |
| Score breakdown | Uses Stripe for billing +3; Delaware C-Corp +3; Several open roles +2; ... |
| Segment | SMB, MM or ENT, and what it was based on |
| Fit / Buying / Weak fit signals found | Plain lists of the signals answered Yes |
| Website, legal entity, funding, partners, competitors, open roles, prices | The facts found |
| One column per data point | "Fit: Uses Stripe for billing" = Yes |

| Special row | What you'll see |
|---|---|
| Duplicate domain | Same findings as the first row, plus "Duplicate of row N" |
| No usable domain | ICP = NO, "Domain is empty or not a valid domain" |
| Website not reached | ICP = PENDING, retry later |
| Possible exclusion waiting for AI | ICP = PENDING, scored with "(pending AI check)" |

### Lead scoring (after phase 2)

Each signal answered Yes adds points by its weight in profile.yaml: **high 3, medium 2, low 1**. Each weak fit signal subtracts 2. Needs check and Unknown add nothing. The Strong fit cut-off is 12 for now. All of this is in [scoring.json](scoring.json) and can be changed without touching code.

Main columns in `icp_check.csv` (sorted Yes first, No last):

| Column | Example |
|---|---|
| Is it ICP? | Needs AI check |
| Why | To confirm: Accounting or bookkeeping firm |
| Must-haves confirmed (of 4) | 4 |
| Legal entity type | Delaware C-Corp |
| One column per must-have and exclusion | "Exclusion: Mainly operates outside the US" = No |
| Data points for AI | Accounting firm, bookkeeping practice or fractional CFO firm |

Main columns in `signals.csv` (sorted by most signals found):

| Column | Example |
|---|---|
| Is it ICP? (phase 1) | Yes |
| Fit signals found | Delaware C-Corp; Uses Stripe for billing; Several open roles |
| Buying signals found | Hiring for several roles |
| Weak fit signals found | Serves one local area |
| Funding stage, Partners found, Competitor tools used, Open roles | Seed (confirmed); Stripe, Y Combinator; QuickBooks Online; 31 |
| One column per signal | "Fit: Uses Stripe for billing" = Yes |
| Data points for AI, AI calls | Vertical (industry); ... ; 14 |

The terminal then prints a summary table and the disk use for the list.

---

## Step 7. Offer to clean up

**Started by:** the end of a run, or `python -m enrich clean leads`

1. Finds raw pages that are no longer needed:

   | Option | What's offered for deletion |
   |---|---|
   | `--keep useful` (default) | Companies phase 1 marked No (excluded, failed or dead) |
   | `--keep none` | All raw pages in the list |
   | `--keep all` | Nothing, no question asked |

2. Shows how many companies and how much space.
3. **Asks you.** The default answer is no.

```
Raw pages that are no longer needed
Reason     Companies    Size
not ICP: excluded  2  233 KB
Delete raw pages for 2 companies (233 KB)? [y/n] (n):
```

| Your answer | What happens |
|---|---|
| **y** | Raw pages deleted. Results and proof stay in the database. |
| **n**, Enter, Ctrl+C | Nothing deleted |
| No terminal (scheduled run) | Nothing deleted; the `clean` command is printed instead |
| Run with `--yes` | Deleted without asking (only for scheduled runs) |

After deletion, `rules` keeps that company's earlier result. To check it again, download it with `--refresh`. If phase 2 needs a company whose pages were deleted, it downloads it again automatically.

---

## Re-running

| You want to | Run | What happens |
|---|---|---|
| Check which companies are ICP | `icp leads.csv` | Phase 1 only |
| Check signals for the ICP companies | `signals leads` | Phase 2 only, on companies phase 1 passed |
| Skip companies still waiting on an AI check | `signals leads --only-yes` | Phase 2 on Yes companies only |
| Do both | `run leads.csv` | Phase 1, then phase 2 |
| Continue a stopped run | the same command again | Finished companies come from the cache |
| Check again after changing a rule | `rules leads` (add `--phase icp` or `--phase signals`) | Rules on saved pages, no downloading, takes seconds |
| Download everything again | add `--refresh` | That phase downloads again for all its companies |
| Look at one company | `explain acme.com` | Phase 1 and phase 2 answers with proof |
| See all lists | `lists` | ICP yes / needs check / no / not reached, signals done, disk use |

---

## What the terminal shows during a run

Phase 1:

```
┌ Phase 1, is it ICP?  leads ─────────────────────────────────────────┐
│ Download pages   ████████████░░░░  812/1,200   12.4 sites/s         │
│ ICP rules        ███████████░░░░░  790/1,200   AI needed 140        │
└─────────────────────────────────────────────────────────────────────┘
┌ Running totals ─────────────────────────────────────────────────────┐
│ Sites live 701   Dead 48   Blocked / unreachable 39 / 24            │
│ Is it ICP?  Yes 210  Yes (some unconfirmed) 251  Needs AI check 140 │
│             No 151  Unknown: site not reached 63                    │
│ Must-haves  US ✓540  Live ✓701  Fintech ✓388  Sales ✓602             │
│ Exclusions  Outside US 61/40  Non-profit 12/30  Holds stock 18/22    │
└─────────────────────────────────────────────────────────────────────┘
┌ Latest companies ───────────────────────────────────────────────────┐
│ linear.app   Yes             ✓ ✓ ✓ ✓   Delaware C-Corp   31         │
│ puzzle.io    Needs AI check  ✓ ✓ ✓ ✓   -   Accounting firm?         │
│ trigger.dev  No              ✗ ✓ · ✓   Non-US entity   Outside US   │
└─────────────────────────────────────────────────────────────────────┘
```

Phase 2:

```
┌ Phase 2, signals  leads ────────────────────────────────────────────┐
│ Download extra pages  ██████████░░  480/601                          │
│ Signal rules          █████████░░░  470/601   AI needed 470          │
└─────────────────────────────────────────────────────────────────────┘
┌ Running totals ─────────────────────────────────────────────────────┐
│ Fit       Publishes real paid pricing 301, Uses Stripe 240, ...      │
│ Buying    Hiring for several roles 120, Hiring a finance lead 14 ... │
│ Weak fit  Serves one local area 33, LLC with no funding 28 ...       │
│ Partners  Stripe 240, Y Combinator 88, Gusto 40 ...                  │
└─────────────────────────────────────────────────────────────────────┘
┌ Latest companies ───────────────────────────────────────────────────┐
│ linear.app   Fit 7  Buying 2  Weak 0   Stripe   31 jobs   14 prompts │
└─────────────────────────────────────────────────────────────────────┘
```

(The numbers above are an illustration, not a real run.)

---

## Coming next

Scoring is already built (it runs at the end of phase 2). These steps will slot into each phase after step 5. In phase 1 they only cover must-haves and exclusions; in phase 2 they cover signals:

```
5. Prepare the AI step                 built: prompts chosen per company, written to the AI queue
      │
      ▼
5b. Free directories     planned       SEC Form D (funding, date, Delaware), SEC public-company list,
                                       YC directory, IRS non-profit list
      │
      ▼
5c. AI step              planned       step 1 on our data, then step 2 web search where needed;
                                       several data points grouped per call
                                       to keep cost down; settles every PENDING verdict
      │
      ▼
5d. Web search           planned       phase 2 only, for companies still missing recent funding
                                       and buying signals
      │
      ▼
6. Write the outputs     built         final list with ICP YES / NO and lead score;
                                       later also vertical, reasoning and outreach message
```

What changes for you when each step lands:

| Step | Effect on the final list |
|---|---|
| Free directories | Fewer PENDING rows; public and late-stage companies caught (e.g. HubSpot); funding stage and Delaware status confirmed |
| AI step | PENDING becomes YES or NO; the highest-weighted signals (startup selling nationally, B2B SaaS) start counting, so scores rise for real startups; industry filled in |
| Web search | Buying signals like recent funding start counting |
| Reasoning and outreach message | Two new text columns per ICP company |

For the full prioritised roadmap, see [PLAN.md](PLAN.md#8-status-and-roadmap).
