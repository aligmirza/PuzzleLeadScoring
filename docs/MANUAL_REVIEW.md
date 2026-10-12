# Manual review of the data points

A data point by data point review of the waterfall, run through MCP (Claude Code) in test mode on
**argon-ai.com** from `examples/Lead-Scoring-(Template)-Default-view-export-1791793022697.csv`.
Each data point was run alone, its full waterfall shown, and approved, corrected or changed before moving on.

**Status (2026-10-12): M1 to F20 reviewed; F21 set to rules, DNS and Wappalyzer only and waiting for your review. Next: review F21, then B1 to B14 and W1 to W12.**

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
4. **Through MCP, only the attached Claude model answers AI questions**, with the prompts in `config/prompts`.
   OpenAI is never called on the MCP path.
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

## Open decisions and offers (not built yet)

- **YC safeguards (F11):** require a second agreeing fact for a strong YC match; count AI web answers about YC only
  when the listing's website is the company's domain; note near-name YC companies as "not a match".
- **F12 / B3:** add the job-board link as the source.
- **F16:** a free rule when all job posts say "In person" / on-site.
- **F17, F19-style cost savers:** web search only on a clue for rarely public data points.
- **F18 and others:** re-fetch nearly empty blog pages with the real browser.
- **E5:** treat "Inc." as a clue, decide by the IRS register / SEC Form D.
- **Other tool data points (F2, F4, F9):** rules only, like F19 and F20, if wanted.
- **Prospeo company lookup** is untested live (only the people search is).
- **Prospeo funding totals are unreliable** (counted Argon AI's $5.5M seed three times as $16.5M): drop or mark the column.

## Still to review

F21, then buying signals B1 to B14 and weak signals W1 to W12: 27 of 57 data points left, F21 included.
