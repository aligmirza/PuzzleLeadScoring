# Lead Enrichment Plan

## What we're building

You give us a list of companies. It can be a CSV with anything from 1 to 50,000 rows. Each row has at least a company name and a website, and sometimes employee count or other details.

For every company, the tool will:

1. Collect information about it from its website, free public directories, web searches and (optionally) paid data services.
2. Check it against `profile.yaml` to decide if it's our ideal customer (ICP). That means it meets all the must-haves and none of the exclusions.
3. Give it a score and put it in a tier: **Strong fit**, **Weak fit** or **Not ICP**.
4. Explain the decision in plain English and write a short outreach message.

It processes many companies at the same time and shows progress live in the terminal.

**Where things stand (Oct 2026):** the free layer is built and tested on a 14-company sample: downloading pages, pattern-matching rules, the two phases, the final list with ICP YES/NO and lead scoring. The AI step, free directories and web search are next. See [8. Status and roadmap](#8-status-and-roadmap).

| Doc | What it covers |
|---|---|
| [PLAN.md](PLAN.md) | This file: the approach, status and roadmap |
| [PIPELINE.md](PIPELINE.md) | The pipeline as diagrams |
| [HOW_IT_WORKS.md](HOW_IT_WORKS.md) | Step by step: what triggers what, and the workflow of both phases |
| [SIGNALS.md](SIGNALS.md) | How each must-have, exclusion and signal is checked |
| [README.md](README.md) | Setup, commands and files |

---

## 1. Ground rules

These come straight from `profile.yaml`, and they shape everything else.

**"We don't know" is not the same as "no".**
Every check has three possible answers: yes, no or unknown. If we can't find something, the company is never failed, excluded or marked down for it. The profile says this many times, so the tool treats it as a hard rule.

**Every answer comes with proof.**
For each fact we store where we found it (the web page), how we found it (pattern match, directory, AI or paid service) and a short quote. That way you can check any result and see why a company scored the way it did.

**Cheap checks first, expensive ones last.**
We start with free methods. A company only moves on to paid steps (AI, web search, paid data) if it's still a possible fit and we're still missing something important. With 50,000 rows, this is what keeps costs low.

**Code does the scoring. AI writes the explanation.**
The score is calculated by simple rules, so the same input always gives the same result. If you change a weight later, you can rescore everything in seconds without collecting the data again.

---

## 2. Turning the profile into checks

`profile.yaml` is written in sentences, which is great for people and AI but hard for code to use directly. So it was turned into three things you can read and change:

| Where | What it holds | Change it when |
|---|---|---|
| [enrich/rules.py](enrich/rules.py) | The pattern-matching rules: what counts as proof for each signal | A rule finds wrong answers or misses clear ones |
| [prompts/](prompts/) | One JSON prompt file per data point (62 files) for the AI step, with shared rules in `_shared.json` | AI should judge a data point differently |
| [scoring.json](scoring.json) | Points per signal (high 3, medium 2, low 1, weak fit -2), the Strong fit cut-off and size bands | You want different weights or tiers |

The scoring follows the special rules written in the profile:

- **Some signals depend on company size.** "No finance person" helps small companies. "Has a finance team" only helps mid-size and larger ones.
- **Two signals can't both apply.** A company is either a startup selling nationally or a local service business, never both.
- **No double penalty.** If a local service business already got its smaller positive score, it isn't also marked down for being local.
- **Company size bands:** 1 to 5 people is SMB, 6 to 25 is MM, 26 or more is ENT. If a company sits exactly on the line, it goes in the smaller band. If we can't tell, it goes in SMB.
- **One of each:** every company gets exactly one size band and one industry.
- **Exclusions need real proof.** A guess or estimate never excludes a company.

---

## 3. How we find each piece of information

There are five ways to collect data. They're listed from cheapest to most expensive.

### A. Reading the company's website with code (free and fast) *(built)*

We visit each company's website once. We look at the home, about, team, pricing, careers, privacy policy, terms, legal, blog and contact pages. We find them by following links on the site and reading its sitemap.

We save two versions of each page:

- **the source code**, to spot tools hidden in scripts and legal names in the footer
- **the readable text**, which the AI reads later

Then simple pattern matching picks out a lot of information for free:

| What we want to know | How code finds it |
|---|---|
| Is the website live and real? | The site loads, isn't a parked domain and has real pages |
| Do they use Stripe, Gusto, Rippling, Deel, Mercury, Brex, Ramp, Carta, Pulley or Chase? | Script links in the page code (for example `js.stripe.com`), the list of vendors in the privacy policy, keyword lists |
| Are they a Delaware C-Corp? | Footer text like "© 2025 Acme, Inc." plus terms of service saying "governed by the laws of the State of Delaware" |
| Do they charge money? | Prices like "$49/month" on the pricing page, checkout links, app store links |
| Are they hiring? How much? | We detect which job board they use (Greenhouse, Lever, Ashby or Workable) and pull their open jobs from its free public feed |
| Are they hiring for finance or ops? | Job titles like Controller, Head of Finance, Operations or Chief of Staff |
| Which accounting software do they use? | Mentions of QuickBooks, Xero, NetSuite and others in job posts or integration pages |
| Are they based in the US? | US address, +1 phone number, prices in dollars, US state names |
| Are they a non-profit? (exclusion) | "501(c)(3)", tax ID shown next to "donate", or a site that is mainly about donating |
| Do they sell physical stock? (exclusion) | Shopify store, "add to cart", "sold out", shipping and returns pages |
| Do they only serve one local area? (weak signal) | "Locations" or "Areas we serve" pages, lists of cities |
| Are they early adopters? | Product Hunt links, an engineering blog |
| Are they remote-first? | "Remote" on the careers page or in job posts |

### B. Asking AI to read the website (cheap) *(prompts written; AI calls not built yet)*

Some questions need judgment that pattern matching can't give. For these, a small, low-cost AI model reads the website text. It answers in a fixed format and includes a quote as proof for each answer.

It answers questions like:

- Is this a startup selling nationally, or a small local business?
- Which industry are they in? (exactly one)
- Is it software sold to other businesses?
- Is AI a core part of their product?
- Are they making money yet, or still "just exploring"?
- Do they actually keep physical stock, or only sell digital or print-on-demand products?
- Are they a non-profit themselves, or a company that sells to non-profits? (Only the first is excluded.)
- Roughly how many people are on the team page?
- Do they make physical hardware?
- Do they need to report in more than one currency?
- What type of legal entity are they, when the footer isn't clear?

### C. Free public directories (free, downloaded once) *(planned, next)*

Some facts are easiest to find in official lists. We download these lists once and look companies up on our own machine. That's fast, free and needs no subscription.

| Directory | What it tells us | How we get it |
|---|---|---|
| **Y Combinator** | Is the company YC-backed, and which batch | The YC company directory on its official site uses a public search index. We download the whole list once a week. |
| **Techstars, 500 Global, a16z speedrun** | Other accelerator backing | We copy their public portfolio pages once a week |
| **SEC EDGAR "Form D" filings** | Funding rounds, dates, amounts, state of incorporation and entity type | Free official download. This is the best free source for funding and Delaware status. |
| **SEC list of public companies** | Is the company publicly traded? (exclusion) | Free official file |
| **IRS list of tax-exempt organizations** | Confirms non-profits | Free official download |
| **Job boards (Greenhouse, Lever, Ashby)** | Open roles and job descriptions | Free public feeds |
| **Apple App Store search** | Developer's legal name, paid plans | Free official API |
| **Product Hunt** | Product launches | Public pages |

We match companies by website first, then by company name. Every match is saved with a confidence level.

### D. Searching the web with AI (more expensive, only when needed) *(planned; designed as step 2 of each AI data point)*

This step only runs for companies that:

- passed the must-haves
- weren't excluded
- still have important unanswered questions

We use a search service (such as Exa, Tavily, Serper or Brave) to look for:

- funding in the last 6 months, including SAFEs, bridge rounds and extensions
- the funding stage and the investors
- a recent switch from LLC to Delaware C-Corp, or a new second company
- public complaints about QuickBooks
- a bookkeeper or part-time CFO leaving
- new board members, investor updates, or preparing for an audit
- published revenue, customer numbers or growth milestones

AI reads the top search results and pulls out the facts, with links as proof.

### E. Paid data services (optional add-ons) *(planned; Clay is already connected)*

| Service | What it gives us |
|---|---|
| Clay, People Data Labs, Apollo | Employee count, LinkedIn page, funding |
| Crunchbase | Funding stage and funding rounds |
| BuiltWith, Wappalyzer | The tools a website uses |
| OpenCorporates | Entity type, date of incorporation, related companies |

Each service lists what it can find and how much each lookup costs. We only call a service when one of its data points is still unknown for that company.

---

## 4. The steps, in order

The work is split into **two separate phases**, each with its own script, so no time or AI is spent on signals for companies that aren't ICP.

```
PHASE 1: IS IT OUR ICP?                                   icp_check.py leads.csv
  1. Clean up the list (fix website addresses; duplicates are checked once)
  2. Download only the pages must-haves and exclusions need, plus open jobs
  3. Must-have and exclusion rules (free)
  4. Verdict: Yes / Yes (some unconfirmed) / Needs AI check / No / Unknown: site not reached
  5. AI prompts only where a must-have or exclusion needs a check        (planned)
  6. Free directories: SEC, YC, IRS lists                                (planned)

PHASE 2: SIGNALS, only for companies phase 1 passed       signals_check.py leads
  1. Download the extra pages signals need, starting from phase 1's pages
  2. Fit, buying and weak signal rules (free)
  3. AI prompts for signals (business type, industry, AI core...)        (planned)
  4. Web search for recent funding and buying signals                    (planned)
  5. Lead score, tier and segment (3 high, 2 medium, 1 low)
  6. AI writes the reasoning and the outreach message                    (planned)

Final list after each phase: every original row + ICP YES / NO + lead score + findings
```

### What you get back

The main file is `lists/<list>/output/<list>_enriched.csv`: **your list exactly as you gave it** (same rows, same order, same columns) with the findings added on the right. No row is ever removed.

| Added column | What it means |
|---|---|
| ICP | YES, NO, or PENDING (website not reached, or a possible exclusion waiting for the AI check) |
| ICP status, Why | The verdict in words, and the proof for a NO |
| Lead score | Points from signals answered Yes (high 3, medium 2, low 1, weak fit -2) |
| Lead tier | Strong fit, Weak fit or Not ICP |
| Score breakdown | Which signals added or took away points |
| Segment | SMB, MM or ENT, and what it was based on |
| Fit / buying / weak fit signals found | Plain lists of the signals answered Yes |
| Legal entity, funding stage, partners, competitors, open roles, prices | The facts found |
| One column per data point | Yes, No, Needs check or Unknown |
| Notes | "Duplicate of row N" where a domain repeats |

Each phase also writes its own detail file (`icp_check.csv`, `signals.csv`), a proof file with the quote and page link for every answer, and an AI queue.

Still to come once the AI step exists: **vertical**, **reasoning** (ending with `Funding stage: <value> (<basis>)`) and **fit message**.

---

## 5. Speed, scale and disk space

Measured on the 14-company test:

| | Result |
|---|---|
| Requests per site | 11 to 16 in phase 1; about 5 more in phase 2 |
| Time | About 30 seconds for 14 sites (slow sites dominate small lists) |
| Disk per site | About 85 KB of slimmed pages (85% smaller than raw) plus about 4 KB of results |
| AI prompts | Phase 1: 6 prompts for the whole list. Phase 2: about 15 per company. |

Built in:

- **Many companies at once** (50 by default) with a cap on requests in flight (200).
- **Polite to websites:** follows robots.txt, skips files over 3 MB, gives each site at most 90 seconds.
- **Saves as it goes.** A stopped run picks up where it left off. Re-checking after a rule change costs nothing.
- **Saves space:** pages are slimmed before saving, and pages of companies that aren't ICP can be deleted afterwards. It always asks first.
- **Doesn't fail good companies on network problems:** blocked or slow sites, and domains your network's DNS can't find but public DNS can, are marked "not reached, retry", never failed.

Rough estimate for 50,000 rows: about 4 GB of slimmed pages (less after clean-up) and about 200 MB of results. Real speed and cost will come from a 500-row pilot.

---

## 6. What you'll see in the terminal

Each phase has its own live dashboard: progress bars, running totals (ICP verdicts and exclusion flags in phase 1; fit, buying and weak signal counts in phase 2) and the latest companies. See [HOW_IT_WORKS.md](HOW_IT_WORKS.md#what-the-terminal-shows-during-a-run) for examples.

| Command | What it does |
|---|---|
| `icp_check.py leads.csv` | Phase 1: is it ICP? |
| `signals_check.py leads` | Phase 2: signals for companies that passed |
| `python -m enrich run leads.csv` | Both phases |
| `python -m enrich lists` | Every list: ICP yes / needs check / no, signals done, disk use |
| `python -m enrich explain acme.com` | Every answer and its proof for one company |
| `python -m enrich rules leads` | Re-check saved pages after a rule change, no downloading |
| `python -m enrich clean leads` | Offer to delete pages no longer needed (asks first) |
| `python -m enrich prompts` | List the AI prompts, or show the exact message for a company |

---

## 7. How the files are organized

```
profile.yaml          your ICP definition (never changed by the tool)
scoring.json          points per signal, tier cut-off, size bands
prompts/              one AI prompt per data point (62 JSON files) + _shared.json
icp_check.py          runs phase 1
signals_check.py      runs phase 2
enrich/
  __main__.py         all commands
  phase1_icp.py       phase 1: ICP check
  phase2_signals.py   phase 2: signals
  crawl.py            finds and downloads pages, job board feeds, public DNS double-check
  rules.py            pattern-matching rules and which AI prompts each company needs
  prompts.py          loads prompt files and builds AI messages
  scoring.py          lead score from scoring.json
  final.py            the final list: every original row + findings
  dashboard.py        live terminal view
  store.py, common.py, inputs.py
lists/<list>/         one folder per list: input.csv, raw/, cache.sqlite, output/
```

---

## 8. Status and roadmap

### Built and tested

| Part | Status |
|---|---|
| Read any CSV, clean domains, keep every row | Done |
| Phase 1 and phase 2 as separate scripts | Done |
| Downloading pages, job board feeds, robots.txt, slim saving | Done |
| Pattern-matching rules for all 4 must-haves, 6 exclusions and 37 signals | Done (accuracy checked on 14 companies only) |
| Final list with ICP YES / NO / PENDING, lead score, tier and segment | Done |
| 62 AI prompt files, one per data point | Written, not yet run |
| Step 2 web search plan per data point (queries with company variables, good sources, confidence and entity-match rules) | Written, not yet run |
| Live terminal dashboards, `explain`, `lists`, `rules`, `clean` | Done |
| Ask before deleting anything | Done |

### Next, in order of impact

1. **Put the project in git.** It isn't under version control yet, so a bad edit to rules or prompts can't be undone.
2. **Build the AI step, in two steps per data point:** step 1 reads our saved pages; step 2 searches the web with the company name and domain only if step 1 found nothing (query templates, sources and confidence rules are already in every prompt file). The two highest-scoring signals ("Startup selling nationally", "B2B SaaS"), the industry and every PENDING verdict wait on it. Until then, scores are lower than they will be. Needs an Anthropic API key.
3. **Group prompts at run time.** Keep one file per data point, but send 4 to 5 data points per AI call. This cuts AI cost about 3 to 4 times.
4. **Add free official directories:**
   - SEC Form D: funding date, amount, state of incorporation
   - SEC public-company list: would have caught HubSpot, which wrongly passed as venture-backed
   - YC directory
   - IRS non-profit list
5. **Hand-label about 100 companies** (ICP yes/no, Strong/Weak). This measures how accurate each rule and prompt is, and sets the Strong fit cut-off from data instead of the current guess of 12.

### Then (accuracy)

6. **Real-browser fallback** (Playwright) for sites that block bots or need JavaScript (1 in 14 in the test).
7. **Fix this Mac's DNS** (use 1.1.1.1 or 8.8.8.8) before big runs: the router failed on 4 of 14 real domains.
8. **Follow investor and careers subdomains** such as `ir.hubspot.com` and `careers.acme.com`.
9. **Set "Tax deadline coming up" to 0 points** in scoring.json. It adds 2 to almost every company around deadlines.

### Before running 50,000 rows

10. **Pilot on 500 real rows** to measure speed, block rate and cost, and check the column mapping.
11. **Run log** per list: date, settings, counts and cost of each run.
12. **Web search** for recent funding and buying signals, only for companies still missing them.

### Optional

13. **Clay** (already connected) for headcount and funding on PENDING companies only.
14. **Monthly re-checks** of old lists to catch new funding or new finance hires.
15. **A shared page** for the team to browse the enriched list with filters, instead of passing CSVs around.

---

## 9. Decisions and open questions

| Question | Status |
|---|---|
| Language | Python (decided) |
| AI models | Claude: Haiku 4.5 for reading websites, Sonnet 5.5 for reasoning and messages (set in `prompts/_shared.json`) |
| Anthropic API key | **Needed** for the AI step |
| One AI call per data point, or grouped | One prompt file per data point (decided); grouping several per call at run time is suggested, waiting on your OK |
| ICP for undecided companies | PENDING until the AI check or a retry resolves it |
| Weak fit points | -2 each (chosen; change in scoring.json) |
| Strong fit cut-off | 12 for now; set properly after the 100-company check |
| Search and data services | Not chosen yet (Exa, Tavily or Serper for search; Clay is already connected) |
| Real lead list | A sample of 50 to 100 rows would help tune the rules |
| git | **Waiting on your OK** to run `git init` |
| Old test folders | `data/`, `out/` and `lists/sample_leads/` are out of date; **waiting on your OK** to delete them |
