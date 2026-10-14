# Manual review of the data points

A data point by data point review of the waterfall, run through MCP (Claude Code) in test mode on
**argon-ai.com** from `examples/Lead-Scoring-(Template)-Default-view-export-1791793022697.csv`.
Each data point was run alone, its full waterfall shown, and approved, corrected or changed before moving on.

**Status (2026-10-14): complete. All 57 data points reviewed on argon-ai.com: 56 approved, 1 partially correct (E2), 29 retries after fixes.**

| | |
|---|---|
| Test list | `lists/dp_test_lead_scoring` (an isolated copy of `lead_scoring_template`; production is never touched) |
| Review log | `lists/dp_test_lead_scoring/output/dp_test_log.csv` (one row per review) |
| Session state | `lists/dp_test_lead_scoring/output/dp_test_session.json` (current data point, reviews, retries) |
| Contacts found | `lists/dp_test_lead_scoring/output/dp_test_lead_scoring_contacts.csv` |
| Waterfall per data point | [SIGNALS.md, "Waterfall per data point"](SIGNALS.md#waterfall-per-data-point) |

## How to resume

In Claude Code (with the puzzle-lead-scoring MCP server connected), say:

> Resume the manual review in dp_test_lead_scoring: show me F21 for argon-ai.com and wait for my review.

The tools: `dp_test_run` (one data point; `confirm_next=true` only after you say yes), `dp_test_answer` (Claude answers
an AI step, test list only), `dp_test_record` (approve, reject, correct, partially_correct, investigate, retry, stop),
`dp_test_summary` (progress). After each review Claude asks "Ready to move to the next data point?" and waits.

## Standing instructions (apply to every data point)

1. **Only stated proof.** Every part of a definition must be shown by a quote. Something only implied is Unknown and
   the waterfall moves on. Unknown is never a No. Short explanations: proof first, little reasoning.
2. **High confidence = the source clearly states it** (any source type: news, the company, an official record), about
   this company (domain match), not older than about 2 years. Medium = clearly stated but matched by name and
   location. Low = implied, name-only, outdated or conflicting (never counts).
3. **Waterfall rule for all:** if steps 1 and 2 (rules, directories) find nothing, run step 3 (AI on our pages), then
   step 4 (AI web search) if still open, unless the data point's file switches a step off with a reason.
4. **Through MCP, Claude answers AI questions for single companies and lists up to about 100** (in Claude Code:
   `/enrich-chat`, one `company-answerer` subagent per company), with the prompts in `config/prompts`; OpenAI is
   never called on that chat path. **Bigger lists use OpenAI** through the `list_ai` tool only: cost estimate
   first, then only with a cap the user approved.
5. **Prospeo only for the finance-person check (F13, F14), at most 1 credit per company** (one team search; the company
   details that come with it go to the contacts CSV only). Ask before using it anywhere else.
6. **Size first:** work out the segment before the finance checks, and skip data points that can't score for that
   size. YC team size is part of the size waterfall.
7. **Rules over AI where the words are in our data:** when the pages, code and DNS already show (or don't show) a
   tool name, no AI step (F19, F20).
8. **Save contacts** found by Prospeo in a separate CSV with company details.

## Decisions per data point

| ID | Result for Argon AI | Review | What was decided or changed |
|---|---|---|---|
| M1 | Yes (rules: NY address, privacy policy) | Approved | — |
| M2 | Yes (site loads, 6 pages) | Approved | Web search stays off (can't prove a site is live). |
| M3 | Yes (Stripe named in the privacy policy) | Approved | — |
| M4 | Yes (3 open roles, Ashby) | Approved | — |
| E1 | No (company press release: $5.5M seed; SEC: one Form D) | Approved | High confidence redefined as "clearly stated" (instruction 2). |
| E2 | No (rules: live site) | Partially correct | A live site now rules E2 out in the rules (it was decided late, by SEC). |
| E3 | No (AI on pages: AI software, not accounting) | Approved | Waterfall rule for all data points (instruction 3): 45 data point files changed. |
| E4 | No (NY address) | Approved | E4 and M1 proofs now keep their page link. |
| E5 | No (rules: "Argon AI Inc.") | Approved | Noted: an "Inc." suffix is a weak proof (non-profits can be Inc.). Not changed yet. |
| E6 | No (AI on pages: software, no stock) | Approved | — |
| F1 | Yes, Seed (confirmed) | Approved | "Backed by" without a named investor is only a clue; F1 stays open for AI until the stage is confirmed; a confirmed round becomes the proof (Series C+ turns it No). |
| F2 | Yes (Stripe in the privacy policy, not seen in code) | Approved | Payment-code scan added (raw pages, JS bundles, the app's sign-in page; every Stripe method and 12 other gateways); note says "found in code" or "stated on the site". |
| F3 | Yes (SEC: incorporated in Delaware) | Approved | — |
| F4 | Unknown | Approved | Job posts always checked: rules list the posts read; the AI gets every job post. |
| F5 | Yes (homepage states a software product) | Approved | Rule: stated software = sells nationally; services need stated customers beyond their city. |
| F6 | Yes ("AI Native Workspace for Clinical & Commercial Teams") | Approved | — |
| F7 | No (stated scalable product) | Approved | — |
| F8 | No (no pricing page, only "Contact Sales") | Approved | Rules: no pricing page after the full search plus contact-only, or no prices and no pricing words anywhere = No. `/plans` added to the search. |
| F9 | Yes, medium (DNS: rippling-domain-verification) | Approved | DNS check added (free) and Wappalyzer fingerprints (free, local). |
| F10 | Yes (the product is AI assistants) | Approved | — |
| F11 | Yes (YC directory, Winter 2024) | Approved | Discussed name confusion (stratifyd.com vs trystratify.com): our match is by exact domain. Proposed safeguards not built yet. |
| F12 | Yes (3 open roles) | Approved | Source link (job board) still missing on F12; offered, not done. |
| F13 | Yes (Prospeo: 9 people, no finance title) | Approved | Prospeo connected (key in .env). Size first; whole-team search, 1 credit per company; F13/F14 only; contacts CSV. Prospeo people-search filter bug fixed. |
| F14 | No (same team list) | Approved | Size waterfall: list, Prospeo headcount, YC team size, AI estimate, SMB. F14 skipped for SMB, F13 for ENT; ENT uses Prospeo's finance-department search. Argon AI is MM (10 people). |
| F15 | Yes ("7-figure ARR up 5x year-to-date", job post) | Approved | Job post excerpts keep fact sentences; rules read job posts for the company's own growth numbers. |
| F16 | No, medium ("In person", New York City) | Approved | — |
| F17 | Unknown | Approved | — |
| F18 | Unknown | Approved | Gap: blogs loaded by JavaScript aren't read. |
| F19 | Unknown | Approved | Rules, DNS and Wappalyzer only (no AI). |
| F20 | Unknown | Approved | Rules, DNS and Wappalyzer only (no AI). Wappalyzer page-structure rules added and false matches fixed. |
| F21 | Unknown (no Carta or Pulley in text, code, fingerprints or DNS) | **Waiting for review** | Rules, DNS and Wappalyzer only (no AI), like F19 and F20. |

## Decisions from the B group (2026-10-14)

| ID | Result for Argon AI | Review | What was decided or changed |
|---|---|---|---|
| F21 | Unknown | Approved | Rules, DNS and Wappalyzer only (no AI). |
| B1 | No, medium (latest round: $5.5M seed, 2025-07-02) | Approved | — |
| B2 | No, medium (investors from July 2025, YC W24) | Approved | Offer: one shared funding search for B1 and B2. |
| B3 | Yes (3 open roles, Ashby) | Approved | Job-board link still missing as source (same as F12). |
| B4 | No, high (3 open roles listed, none in finance) | Waiting | Job sites added to step 4 for all job-based data points (Built In, ZipRecruiter, LinkedIn Jobs, YC, Wellfound via search). A web search confirmed the same 3 roles and no finance role. LinkedIn automation tools (logged-in bots) were rejected: against LinkedIn's terms. |

| B5 | No, high (no ops or chief-of-staff role among 3 listed) | Approved | — |
| B6 | No (SEC Form D: Delaware corporation since 2023, previous name Incyte Bio, Inc.) | Approved | Step 2 now reads Form D year of incorporation, entity type and previous names (SEC contact email set). |
| B7 | No (SEC Form D: incorporated 2023) | Approved | Decided free by step 2. |
| B8 | Unknown | Approved | — |
| B9 | Unknown | Approved | — |
| B10 | Yes (1 day to the Oct 15 C-Corp deadline) | Approved | Details added to SIGNALS.md. |
| B11 | Unknown | Approved | — |
| B12 | No, medium (blog: latest launch 2025-10-07) | Approved | JavaScript pages rendered in the real browser (`render`): the blog is now read. |
| B13 | Unknown | Approved | LinkedIn-post searches (company or founder) added for B2, B9, B11, B12, B13. |
| B14 | Unknown | Approved | SOC 2 / Trust Center correctly not counted as a financial audit. |
| W1 | No (stated software product) | Approved | W1 prompt gets the F5 rule; W1 = No for free when F5 is Yes (opposites). |
| W2 | No (software, not billed labour) | Approved | — |
| W3 | No (online software; an office address is not a venue) | Approved | — |
| W4 | No ("Argon AI, Inc.", YC-backed) | Approved | Test mode now asks questions a normal run skips (tier locked) and says so. |
| W5 | No ("7-figure ARR", 3 open jobs) | Approved | Prospeo's old "waitlist" description ignored: current pages win. |
| W6 | No ("All payments shall be in US dollars", terms) | Approved | — |
| W7 | No ("Argon builds AI software") | Approved | A different "Argon AI" making chips was ignored. |
| W8 | No, medium (subscription billing in the terms; template-like) | Approved | Long pages now keep decisive sentences past the cut-off (`excerpt`). |
| W9 | No (no store platform in the code; software) | Approved | — |
| W10 | Unknown | Approved | No from F13 (no finance team) for free: opposites, built 2026-10-14. |
| W11 | Unknown | Approved | Web search only on a clue for F17, B8, B9, B11, B13, B14, W11. |
| W12 | No ("All payment data is stored by Stripe"; Rippling in DNS) | Approved | Opposite of M3. |

## Conditional runs (2026-10-14)

- Must-haves run one after another (M1 → M2 → M3 → M4 → exclusions); a proven No stops the rest, and ICP NO makes
  every open phase 1 data point Not applicable.
- F12 runs only when F1 is Yes.
- Skipped data points show **Not applicable** with the reason; free proof already found is kept.
- Also built: B1, B2, B13 after F1; B12 after F8; B11 after F4 (skipped when the parent is proven No).
- Opposites built: W1, W3, F7 from F5; W5 from M4; W4 from F3; W10 from F13; W12 from M3; W9 from E6.
- Left out on purpose: B3 → B4/B5, F5 → W2/W7, F11 → B2 (they would skip or close data points that can still be Yes).

## Open decisions and offers (not built yet)

- ~~Opposites~~ **built 2026-10-14** (see Conditional runs; W2 and W7 left out).
- **W9:** No store platform in the code → W9 No at step 1.
- **B4/B5:** job board read and no finance / ops title → No at step 1.
- **B12:** skip "launched pricing" when the company publishes no pricing.
- **B1/B2:** one shared funding search.

- **YC safeguards (F11):** require a second agreeing fact for a strong YC match; count AI web answers about YC only
  when the listing's website is the company's domain; note near-name YC companies as "not a match".
- ~~Job boards (A, B)~~ **built 2026-10-14:** 13 boards + careers-page roles; 4 → 13 test companies with jobs
  (glavel.com and repool.com now show finance hires for B4). Not built: JazzHR, Jobvite, Stardex.
- **B4:** rules-only No when the job board was read and no title is a finance role.
- **F16:** a free rule when all job posts say "In person" / on-site.
- **F17, F19-style cost savers:** web search only on a clue for rarely public data points.
- ~~F18 and others: re-fetch nearly empty pages with the real browser~~ **built 2026-10-14** (`render`).
- **E5:** treat "Inc." as a clue, decide by the IRS register / SEC Form D.
- **Other tool data points (F2, F4, F9):** rules only, like F19 and F20, if wanted.
- **Prospeo company lookup** is untested live (only the people search is).
- **Prospeo funding totals are unreliable** (counted Argon AI's $5.5M seed three times as $16.5M): drop or mark the column.

## Still to review

Nothing: the review is complete. Next: the open offers above, then a second company to confirm the fixes.

## Before the review was completed

F21, then buying signals B1 to B14 and weak signals W1 to W12: 27 of 57 data points left, F21 included.
