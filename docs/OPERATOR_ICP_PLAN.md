# Plan: moving lead scoring to the operator ICP

Status: plan, not built. Source of truth for the ICP: the updated Notion ICP page (Oct 22–23, 2026) and Alexey's thread. This page maps that ICP onto the lead scoring model and lists what has to change.

The change is more than a re-weighting. Today the model rewards a funded early-stage startup. The new ICP rewards an **operator who wants the bookkeeping done for them**, so several signals flip direction.

## 1. What changed

|  | Today (founder ICP) | New (operator ICP) |
| --- | --- | --- |
| Who | Early-stage, venture-backed founders | Founders up to Series C and SMB owners who want the work off their plate |
| What we sell | The platform | Services (Full Service, Catch-Up, Expert Review) plus the platform |
| What decides fit | Funding stage, modern startup profile | **The situation:** books behind or a provider failed them, wants to hand the work off, can pay |
| Funding | High-weight fit signal | Context only, not intent |
| Early-stage companies | Core target | Scored lower, not blocked |
| LLCs and S-Corps | Penalised (W4) | Qualify; C-Corps are still the clearest fit |
| First lanes | SaaS, AI, fintech | **SaaS and professional services** |

Why: early-stage founders mostly want to do their own books and rarely become services customers. Operators who already have real books to deal with want to hand the work off (Alexey's analysis of 500 sales calls: 69% of help-seekers had books behind, against 18% of DIY buyers).

## 2. What still fits as it is

- **The engine:** the waterfall per data point, the proof rules, conditional runs, the MCP and OpenAI paths, the learning loop and the speed work all stay unchanged.
- **Must-haves:**
  - M1 (US-based), M2 (live product) and M4 (real activity) still apply.
  - M3 (reachable source data) still applies; only its tool list changes (section 3.2).
- **Exclusions:** E3 (accounting firm), E4 (outside the US) and E6 (holds stock) are still hard stops.
- **Signals that keep their meaning, with a new weight:** B9 (left a bookkeeper), B10 (tax deadline), B14 (audit), B13 (investor reporting), F9 (payroll), F13 (no finance person), B5 (hiring ops or chief of staff).

## 3. The changes

### 3.1 Hard stops: new exclusions (routed to a partner referral)

| New ICP disqualifier | Today | Change |
| --- | --- | --- |
| Sole proprietorship or partnership | Part of W4 (weak) | New exclusion. LLCs and S-Corps are no longer penalised |
| Non-USD or multi-currency books | W6 (weak, −2) | Becomes an exclusion |
| More than one entity | **F17 is a fit signal (+2), B8 a buying signal (+2)** | Both flip to exclusions |
| Inventory or physical goods | E6 covers holding stock | Widen to physical goods; decide about W7 (manufacturing) |
| Marketplaces | Not covered | New exclusion |
| Over $500K monthly expenses | Not covered | Not public, so it needs a proxy: about 50+ people, Series D or later, or NetSuite with a finance team |
| Series C or later (E1) | Exclusion | "Up to Series C" needs one answer: is Series C itself in or out? |
| New company, no website, no funding (E2) | Exclusion | Becomes "no activity yet": anti-ICP, scored down, not blocked |

### 3.2 Must-haves: small changes

- M3: add Rho to the tool list (Chase is already there). "Has source data we can reach" stays a must-have.

### 3.3 Score up

| From Alexey's "score up" list | Today | Plan |
| --- | --- | --- |
| Books behind or messy | Nothing | **New data point.** Mostly found in job posts (a "catch-up bookkeeper" role) or public posts asking for help |
| Needs tax filing | B10 (medium, calendar rule) | Raise it, and add stated filing needs |
| Preparing for investors or a raise | B13 and B14 (low) | Raise to high |
| Just left a bookkeeper or firm | B9 (medium) | High: 34% of help-seekers against 7% of DIY buyers |
| Asking for help, willing to hand it over | Nothing | **New data point:** hiring a bookkeeper or accountant (contract or part-time), or posting "looking for a bookkeeper" |
| Budget | Nothing | A proxy: payroll (F9, raised to high), 6–25 people, published revenue (F15) |
| Payroll | F9 (medium) | High: 41% of help-seekers against 22% of DIY buyers |
| Professional services is a first lane | F7 medium; W1 and W2 penalise it | F7 up to high; drop W2, and probably W1 and W3 |

### 3.4 Score down

| From Alexey's "score down" list | Today | Plan |
| --- | --- | --- |
| DIY types | Nothing; **F10 (AI core) and F18 (early adopter) score up** | Flip both. New DIY signal: AI, MCP or API workflows for their own books, a finance-literate founder (CPA), "we do our own books" |
| No activity yet | W5 (weak) | Keep; absorbs E2 |
| Brand-new company | B7 (+2) | Flip to neutral or down: 28% of DIY buyers against 6% of help-seekers. It stays positive only when they want managed finance from day one |
| Owner with an accountant they trust | W11 (weak) | Keep |
| Early-stage | F1 (+3), B1 and B2 (+3) | F1 becomes "operating company up to Series C", any funding or bootstrapped, low weight. B1 and B2 become low |

### 3.5 Lower or change the startup signals

- **Lower:**
  - F2 Stripe: it becomes part of the reachable stack
  - F4 QuickBooks Online: context only
  - F8 paid pricing, F11 YC, F12 open roles, F16 remote
  - F19–F21: kept low, as part of the reachable stack
- **Change:**
  - F3 becomes "C-Corp (any state) high; LLC or S-Corp qualifies"
  - F5 and F6 stay as the SaaS lane
  - B11 (QuickBooks complaints) becomes context
  - B6 and B12 become low or none

### 3.6 The other profile sections

- **Segments:** keep 1–25 employees as a hypothesis. Add the $500K monthly-expense ceiling (through the proxy) and note that $10K–$50K a month is the sweet spot.
- **Verticals:** SaaS and professional services are the first lanes. Other verticals are marked "qualify the workflow first". Real estate, legal and nonprofits are undecided (section 5).
- **Partners:** add Rho. The accelerators matter less now.
- **Competitors:** add Gradient, Minerva and Kick (AI-native firms selling the outcome). Pilot, Bench and freelancers become signals of a provider that failed.
- **Value proposition:** rewrite it around the outcome: current, dependable books without owning the work.

### 3.7 New output columns

- **Route:** Full Service, Catch-Up, Expert Review, Platform only, or Partner referral, taken from the ICP's routing table.
- **Arrival:** reactive (something broke) or proactive (delegating early).
- **Contacts (Prospeo):** search the buyer titles (founder, owner, CEO, COO, chief of staff, operations or finance lead), not only the finance person. Still 1 credit per company.

## 4. How to roll it out

1. Rewrite `config/profile.yaml` from the Notion page, which stays the source of truth.
2. Change the data points:
   - add about 5: books behind, wants help, DIY signal, sole proprietorship or partnership, marketplace
   - flip about 6
   - re-weight the rest in `config/scoring.json`
3. Update the prompts, rules, conditional runs, `docs/SIGNALS.md` and the workflow page.
4. Review only the new and changed data points by hand, the same way as the original 57.
5. **Calibrate on real outcomes:** run the model on Alexey's 152 coded companies (help / DIY / review) and the 17 closed-won services deals. Tune the weights and the Strong-fit cut-off until help-seekers score above DIY buyers. This is the first labelled set we have.
6. Re-run the lead list, compare the tiers before and after, and post the change to the team.

## 5. Two pipelines: services (default) and product

Most data points find **facts** about a company (US-based, has payroll, runs several entities). What differs between the product ICP and the services ICP is **how each fact counts**: "runs several entities" is a fit signal for the product today, and a hard stop for services. So we keep one fact-finding engine and put two scoring pipelines on top. **Only one pipeline runs at a time.**

**Decided:**

- **Services is the default.** The product pipeline runs only when chosen (`--icp product`).
- **Only one pipeline runs at a time.** Never both in the same run, so there is no combined verdict or "best route".
- **Prospeo stays at 1 credit per company.** One search brings both the finance person (product) and the buyer titles (services).

### 5.1 What's shared and what's separate

| Shared (done once per company, reused by either pipeline) | Separate per pipeline |
| --- | --- |
| Download, job boards, code scan, Wappalyzer, DNS | `profile.yaml`: who the ICP is |
| Directories (YC, SEC, IRS, Wayback) | Each fact's role: must-have, exclusion, fit, buying or weak |
| Rules and the 57 data points, plus the new services facts (books behind, wants help, DIY) | Weights and the Strong-fit cut-off (`scoring.json`) |
| AI answers to fact questions (reused if the other pipeline runs on the same list later) | Conditional runs and opposites (for example, F12 after F1 is product only) |
| Segment, vertical, partners, competitors | Value proposition, route column, which contacts to look for |
| Speed work, learning loop, MCP, API, Clay | The AI's ICP summary in the prompt |

### 5.2 How one company flows

```
Download + free checks (cached)
        │
        ├── --icp services (default): hard stops ──► score up / score down ──► score, tier, route
        │
        └── --icp product (when chosen): must-haves / exclusions ──► fit, buying, weak ──► score, tier
        │
AI questions = only what the chosen pipeline still needs
        │
Output: that pipeline's verdict, score, tier and proof
```

- **A company can pass one ICP and fail the other.** A funded seed-stage AI startup can be a Strong product fit and a Weak services fit (likely DIY). A 12-person agency with books behind is the reverse. Run the other pipeline on the same list to see its view.
- **Each pipeline has its own stop-when-decided and conditional runs**, and asks only the questions it needs.
- **Running the other pipeline later on the same list is cheap.** The downloads and free checks are cached. AI answers to fact questions are reused too, as long as those prompts stay pipeline-neutral: they ask for facts, and the ICP summary in the system prompt is not part of the cached answer.

### 5.3 Output columns

| Column | Example |
| --- | --- |
| Pipeline | Services or Product |
| ICP, Score, Tier | YES · 14 · Strong fit |
| Route (services only) | Full Service / Catch-Up / Expert Review / Platform only / Partner referral |
| Why | The proof behind the verdict |

Clay, the CSVs, MCP and the API all get the same columns, with the pipeline named, so services and product results never get mixed up.

### 5.4 How you choose what runs

- **CLI:** `--icp services` (default) or `--icp product`.
- **MCP and API:** the same `icp` option on `check_company`, `check_companies`, `enrich_list` and `/v1/check`.

### 5.5 Cost

- **Free steps:** no extra cost, since they run once.
- **AI:** each run asks only what its pipeline needs. Running the other pipeline later reuses the fact answers already bought.
- **Prospeo:** at most 1 credit per company, whichever pipeline runs, and never a second credit for the same company.

### 5.6 How we'd build it, safely

1. **Separate facts from roles, with no change in behaviour.** Move each data point's group and weight out of the code into `config/icps/product/`. The product pipeline then equals today's model exactly. To prove it, run `python -m enrich bench` before and after; every answer must be identical.
2. **Add** `config/icps/services/` from sections 3 and 4 of this page: its roles, weights, hard stops and conditional runs. Make it the default.
3. **Add the new services facts:** books behind, wants help, DIY signal, sole proprietorship or partnership, marketplace. The product pipeline gives them weight 0.
4. **Output names the pipeline** in the CSV, MCP, API and Clay.
5. **Prospeo:** one search per company returning the finance person and the buyer titles (still 1 credit).
6. **Per-pipeline views:**
   - review: test mode and the waterfall table get an `--icp` option
   - the workflow page: one view per pipeline
   - the learning log: each event tagged with its pipeline
7. **Calibrate the services pipeline** on Alexey's 152 companies and the closed-won deals. The product pipeline keeps today's calibration.

## 6. Questions

1. **Series C:** is it in ("up to and including") or out?
2. **The $500K expense ceiling:** is a proxy acceptable (50+ people, Series D or later, NetSuite with a finance team)?
3. **Nonprofits, real estate, legal:** keep excluding nonprofits, or route them for a closer look?
4. **Early-stage weight:** a fixed penalty, or just no points from funding?
5. **AI-native founders:** score them down as likely DIY, or keep them neutral?
6. **Labelled data:** can we get the domains for the 152 coded companies and the closed-won deals, so we can calibrate?

**Answers** (1 and 2 decided; 3–6 proposed, to confirm with Oz and Alexey):

1. **Decided (2026-11-03): in, up to and including Series C.** Series D and later, and public companies, are excluded (S6). E1 is info only.
2. **Decided (2026-11-03): the proxy as built.** A hard stop only on strong proof (NetSuite with a finance team, Series D or later, or 100+ people); 50–99 people scores down (S8). In the output S6, S7 and W10 are one rule: the exclusion reason reads "Too big for services (<the check>): <proof>" (`reason_groups` in `config/icps/services/roles.json`).
3. **Nonprofits stay excluded** (audit-specific scoping). **Real estate and legal pass, neutral, marked "qualify the workflow first".**
4. **No points from funding, no fixed penalty.** Score down only on real signals: no activity yet, brand-new with no history, DIY signals.
5. **A small score-down only with a DIY signal** (doing their own books with AI, MCP or API). An AI product on its own is neutral.
6. **Ask Alexey for the domains.** Without them the services weights are guesses.

Decided: only one pipeline runs at a time (services by default), so there is no "best route" question.
## 7. Build plan

Five stages. Each one ends with a check that has to pass before the next starts, and each can be pushed on its own.
Rule throughout: the product pipeline must keep giving today's answers.

### Stage 1: separate facts from roles (no change in behaviour)

**Status: done (2026-10-28).** Groups, conditional runs and opposites live in `config/icps/product/roles.json`;
`profile.yaml` and `scoring.json` moved there too. `enrich/core/icp.py` says which pipeline runs; `SIGNALS` takes
its groups from it, results are stored per pipeline, and the AI system prompt no longer carries an ICP description.
Checked: on the 100-company lead list, re-running the rules offline gives exactly the same answers, verdicts and
scores as before (with saved directory findings and without directories), and all tests pass, including new
`tests/test_icp.py`.

Today each data point's group is fixed in the code (`SIGNALS` in `enrich/checks/rules.py`). About 100 places in 19
files read it, most in `rules.py`, then `review.py`, `__main__.py`, `directories.py`, `phase2_signals.py`,
`waterfall.py`, `final.py` and `merge.py`.

1. **`config/icps/product/`** holds today's `profile.yaml` and `scoring.json`, plus a new **roles** list: each data
   point's group (must-have, exclusion, fit, buying, weak), weight, conditional runs and opposites.
   `config/datapoints/` stays shared: it holds how each fact is found, not how it counts.
2. **`enrich/core/icp.py`:** the chosen pipeline and what it says about each data point:
   - `group(sid)`, `weight(sid, segment)`, `phase_of(sid)`
   - `run_if(sid)`, `opposites()`
   - `profile()`, `scoring()`
3. **Replace every hard-coded group** with these calls. `SIGNALS` keeps only the names.
4. **The phases come from the pipeline:** phase 1 is that pipeline's must-haves and exclusions, phase 2 the rest. Today
   F17 (several entities) belongs to phase 2. In services it's a hard stop, so it moves to phase 1 there.
5. **Storage:** results are saved per pipeline in each list. The product pipeline keeps today's tables, so existing
   lists keep working. AI answers stay shared, since they're facts.
6. **Neutral fact questions:** the AI system prompt keeps the GTM-engineer role and the proof rules but drops the ICP
   summary. The answers then mean the same in both pipelines and can be reused. This changes prompt versions once.

**Check:** all tests pass, and `python -m enrich bench` on the 100-company list gives exactly the answers it gave
before the change.

### Stage 2: the services pipeline and how to choose it

**Status: done (2026-10-28), provisional weights.** `config/icps/services/` (profile, roles, weights) is the default
everywhere: CLI `--icp product`, MCP `icp="product"`, API `?icp=product` choose product. Output files and results are
kept per pipeline, with a `Pipeline` column. Checked: product still gives exactly the baseline answers; services runs
on all 100 companies (56 ICP YES, 4 newly excluded by its hard stops: manufacturing 2, non-US operations 1, several
entities 1). Only 1 company reaches the provisional Strong-fit line, because the services situation facts arrive in
stage 3 and the weights and cut-off are set in stage 5. The open items are listed under `provisional` in
`config/icps/services/roles.json`.

1. **`config/icps/services/`:** the profile rewritten from the Notion page, and roles and weights from section 3.
   That means hard stops (F17, B8 and W6 become exclusions), score up and score down, and its own conditional runs.
2. **Services becomes the default** everywhere:
   - **CLI:** `--icp services` (default) or `--icp product`
   - **MCP:** an `icp` option on `check_company`, `check_companies`, `enrich_list`, `list_ai`, `open_questions`,
     `submit_answers` and `explain_company`
   - **API:** the same option on `/v1/check`, `/v1/check/batch` and `/v1/lists`
   - **Clay:** the pipeline goes with the push
3. **The output names its pipeline:** the file is `<list>_enriched.csv` for services and
   `<list>_product_enriched.csv` for product, with a `Pipeline` column.
4. **Per-pipeline views and logs:**
   - stop-when-decided and the cost estimate use the chosen pipeline's weights
   - learning events are tagged with their pipeline
   - `bench` gets an `--icp` option

**Check:** product runs are still identical to stage 1. Services runs give a verdict for every company. The new tests
cover the flips: F17 is a fit signal in product and a hard stop in services; LLCs are scored down in product and
pass in services.

### Stage 3: the new services facts

**Status: built (2026-10-28); the manual review of the new facts is still to do.** S1–S8 are in the rules, with AI
questions for S1–S6 and waterfall files. They are info in product and count in services; E1 is info in services
(S6, Series D or later, replaces it). Rho counts as a fintech tool (M3). No pipeline asks AI questions about data
points worth 0 points in it (services: funding, AI, YC and the like). Checked: on identical data, product gives
exactly the same answers as the code before stage 1; services: 56 ICP YES, with marketplaces (2), 100+ people (1) and
the earlier hard stops excluding, and Series C companies now passing. Tests: `tests/test_services_facts.py`.
Known limit found: E1's existing "publicly traded" proof sometimes comes from a founder's bio or a public customer,
and S6 inherits it (to fix through the learning loop). Next: review S1–S6 one at a time in test mode
(`dp_test` with `icp="services"`), then stage 4.

| New fact | Found by (free first) | Then AI |
|---|---|---|
| Sole proprietorship or partnership | Legal suffix, terms of service, SEC entity type, "sole proprietor" | Pages, then the web |
| Marketplace | "Marketplace", buyers and sellers, payment splits (Stripe Connect in the code) | Pages |
| Books behind or messy | Job posts: catch-up, cleanup, "behind on books", reconciliation backlog | Web, on a clue only |
| Wants help, will hand it over | Job posts for a bookkeeper or accountant (contract, part-time, fractional), "looking for a bookkeeper" | Web: posts, Upwork-style requests |
| DIY signal | AI, MCP or API workflows for their own books, a founder who is a CPA, "we do our own books" | Pages, then the web on a clue |

Also: widen E6 to physical goods, change E1 to Series D or later, add the $500K proxy hard stop (NetSuite with a
finance team, Series D or later, or 100+ people), and add Rho to M3, partners and the payment and DNS checks.

Each new fact gets a waterfall file, a prompt, rules, tests, and a row in SIGNALS.md and on the workflow page.
Then it gets reviewed by hand, one at a time in test mode (`dp_test --icp services`), like the original 57.

**Check:** every new fact approved in the manual review, and all tests pass.

### Stage 4: routing and outreach columns

**Status: done (2026-10-28).** Route, Arrival and Value angle are in the services output; partners and competitors are
filled for every company read (also ICP NO); Gradient and Minerva are competitors (only in an accounting context);
the Prospeo search ranks founders, owners and CEOs, then the finance person, then COO / chief of staff / operations.
On the lead list: 56 to qualify on the call, 23 partner referrals, 11 retries (site not reached), 10 not ICP; the
strong services triggers are rarely public, so most routes need the call. Product answers unchanged (checked against
the committed code; only partners now show for 5 ICP NO companies).

Services facts reviewed on more companies (2026-10-28): hyperguest.com marketplace (Yes), cibc.com public (Yes),
repool.com bookkeeping help (Unknown: its accounting role serves clients). Open decisions found there: B4 counts a
client-facing accounting role as "hiring a finance lead", and a fund administrator like Repool may be an
accounting-services firm (E3).

1. **Route** (services): Full Service, Catch-Up, Expert Review, Platform only, or Partner referral, from the routing
   table. A hard stop means a partner referral, books behind means Catch-Up first, and a DIY signal with no need
   means Platform only.
2. **Arrival:** reactive (something broke: books behind, provider failed, a deadline) or proactive.
3. **Value angle:** the operator value proposition, chosen from the proof found.
4. **Partners and competitors:** add Rho; add Gradient, Minerva and Kick as competitors. Fill both columns for every
   company that was read, ICP NO included.
5. **Prospeo:** one search per company (1 credit) that returns the finance person and the buyer titles (founder,
   owner, CEO, COO, chief of staff, operations or finance lead).

**Check:** on a sample of 20 companies, each route and arrival matches what the ICP doc's routing table would say.

### Stage 5: calibrate, document and launch

**Status: tooling done (2026-10-28); waiting for the labels.** `python -m enrich calibrate` reports how well the
services scores separate help-seekers from DIY buyers, the Yes rate of each fact in each group, the weights those
suggest, the best Strong-fit cut-off and how many closed-won deals reach it (`docs/TESTING.md`). It changes nothing
unless `--apply` is given and the tests pass. Needed from Alexey: the domains of the 152 coded companies with their
buyer type, and the closed-won services deals (template: `examples/calibration_labels_template.csv`).

1. **Calibrate on outcomes:** load Alexey's 152 coded companies (help / DIY / review) and the closed-won services
   deals as labels. Run `python -m enrich evaluate --icp services`, then tune the weights and the Strong-fit cut-off
   until help-seekers score above DIY buyers and the closed-won deals land in Strong fit.
2. **Re-run the lead list** with services, and compare it with the product tiers.
3. **Docs and tools:**
   - README, PIPELINE.md, SIGNALS.md and INTEGRATIONS.md
   - the workflow page, with one view per pipeline
   - the MCP instructions and the Claude Code skills (icp-answering, check-company, bulk-enrich, enrich-chat)
4. **Team:** a short update with the new columns and how to choose the pipeline.

**Check:** labelled help-seekers score higher than DIY buyers by a clear margin, the closed-won deals are Strong fit,
and Oz and Alexey sign off on the sample.

### Risks and how we handle them

| Risk | Handling |
|---|---|
| Stage 1 touches ~100 places and could change product answers | The bench check must show identical answers before anything else is built |
| "Books behind" and "wants help" are rarely public | Search the web only on a clue; Unknown stays Unknown, never a No |
| Weights are guesses until the labels arrive | Stage 5 depends on Alexey's domains; until then services tiers are marked provisional |
| The $500K proxy blocks a lean company by mistake | Hard stop only on strong proof; 50–100 people only scores down |
| Old lists have product results only | They stay readable; running services on them reuses the cached downloads and fact answers |
