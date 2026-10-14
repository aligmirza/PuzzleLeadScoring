# Testing and Evaluation

How to check that the tool gives the right answers, step by step: from a quick free run, to one company in depth, to reviewing every data point yourself and measuring accuracy.

- What each data point means and what counts as proof: [SIGNALS.md](SIGNALS.md)
- How the pipeline and the waterfall work: [PIPELINE.md](PIPELINE.md)
- Setup and every command: [README.md](../README.md)

Commands below assume you are in the project folder with the virtual environment active (`source .venv/bin/activate`). Without it, start each command with `.venv/bin/python` instead of `python`.

---

## The levels at a glance

| Level | What you check | Command | Cost |
|---|---|---|---|
| 1. Smoke test | Everything runs; the test list gets its known answers | `python -m enrich run examples/accuracy_test.csv --list accuracy_test` | Free |
| 2. AI run | AI settles the open questions within budget | `python ai_check.py accuracy_test --phase icp` | About $0.05 per 10 companies |
| 3. One company in depth | Every step for one company | `python -m enrich trace linear.app` | Free (`--ai` asks first) |
| 4. One data point in depth | Each data point through all 5 layers | `python -m enrich waterfall linear.app --group all` | Free (`--ai`, `--providers` ask first) |
| 5. Manual review | You mark every data point right or wrong, one data point (`--only M1`) or one company at a time | `python -m enrich review accuracy_test --only M1` | Free |
| 6. Accuracy | Numbers: right, wrong, good companies wrongly NO | `python -m enrich evaluate accuracy_test --labels ...` | Free |
| 7. After any change | Nothing that was right broke | [Regression checklist](#7-after-any-change-regression-checklist) | Free |

---

## Before you start

```bash
python -m enrich settings
python -m enrich directories
python -m enrich cache
python -m enrich usage
```

These show your keys and limits (set or not, values hidden), the YC and IRS downloads, the shared company cache, and AI cost and provider credits used this month.

Set a monthly cap so testing can't overspend: `AI_MONTHLY_BUDGET_USD=10` (and `PROVIDER_MONTHLY_CREDITS=50` if Prospeo is set) in `.env`.

---

## The test set

[examples/accuracy_test.csv](../examples/accuracy_test.csv) has 11 companies, each chosen to test one part of the profile, with the right answers in their own columns:

| Column | Example |
|---|---|
| `Expected ICP` | YES or NO |
| `Expected tier` | Strong fit or Weak fit (companies expected YES only) |
| `Expected: <data point>` | `Expected: Series C or later, or public` = Yes |
| `Expected reason` | Free text, shown next to disagreements |
| `What it tests` | Why the company is in the set |

| Company | Tests |
|---|---|
| Mintlify | Clean early-stage ICP, Strong fit |
| thoughtbot | Services firm: passes, Weak fit |
| Pulley | Shutdown notice on its homepage |
| Ramp | Late-stage private ($44B valuation), no ticker |
| Linear | Late stage; its own site never mentions funding |
| Asana | Public company (SEC) |
| Khan Academy | Non-profit with "Inc." in its name |
| Givebutter | Trap: for-profit software for non-profits (site blocks bots) |
| Kruze Consulting | Accounting firm |
| Pennylane | Non-US company on a .com |
| Brooklinen | Holds physical stock |

**Add a company:** add a row with its domain and the answers you are sure of, then run level 1 again. Every mistake found in testing should become a row here, so it stays fixed.

**The real answer key** comes from Puzzle: about 50 current customers (expected YES, mostly Strong fit) and 50 lost or "not a fit" deals (with the reason). `python -m enrich evaluate --template labels.csv` writes a blank file to fill in.

---

## 1. Smoke test (free)

```bash
python -m enrich run examples/accuracy_test.csv --list accuracy_test --keep all
python -m enrich evaluate accuracy_test --labels examples/accuracy_test.csv
```

**Expected:** ICP 9 or 10 decided right, 0 good companies wrongly marked NO. Givebutter stays PENDING (its site blocks automated visits). Before the AI step some companies are PENDING ("Needs AI check"); that is normal.

**Look at:** the final list `lists/accuracy_test/output/accuracy_test_enriched.csv`: every original row kept, `ICP`, `Why`, `Lead score`, `Lead tier`, `Reasoning` ending in a `Funding stage:` line, one column per data point (empty = not found).

---

## 2. AI run

```bash
python ai_check.py accuracy_test --phase icp
python -m enrich signals accuracy_test --keep all
python ai_check.py accuracy_test --phase signals
python -m enrich evaluate accuracy_test --labels examples/accuracy_test.csv
python -m enrich usage
```

That is: AI for phase 1 (shows the cost and asks), phase 2 again (verdicts may have changed), AI for phase 2, then the accuracy check and the month's cost.

**Expected:** 10 of 10 decided right (Givebutter still PENDING), tiers right, cost well under $1. Answers already paid for by another list are reused for free.

**Options worth testing:** `--mode cheap` (grouped calls, about 3 times cheaper; compare it, see [section 7](#comparing-two-runs)), `--no-search-signals` (skips the web-only buying signals), `--limit 3`.

---

## 3. One company in depth: `trace`

```bash
python -m enrich trace linear.app --company Linear
python -m enrich trace linear.app --company Linear --ai
```

It runs the whole flow for one company and stops after each step (Enter = next, q = stop): input → download → rules → directories → ICP verdict and the AI questions still open (with why each is asked) → AI → phase 2 pages and signals → score. Saved as `lists/trace_linear_app/output/trace_linear_app.md`.

**Check in each step:**

| Step | Right looks like |
|---|---|
| Download | Status live; the pages found really are what their names say (an "engineering" page is an engineering blog, not an integrations listing); open roles from the job board |
| Rules | Each Yes or No has a quote that says it, from the company's own page |
| Directories | Matches are the same company (strong = domain or legal name); weak matches change nothing |
| Verdict | The reason names real proof; open AI questions are only for things the free layers couldn't answer |
| AI | Each counted answer has a quote that proves it; "not mentioned" is never a No |
| Score | Every point in the breakdown comes from a confirmed Yes |

---

## 4. One data point in depth: `waterfall`

```bash
python -m enrich waterfall linear.app
python -m enrich waterfall linear.app --group all
python -m enrich waterfall linear.app --ai --providers
```

Each data point shows the 5 layers in order, cheapest first:

| # | Layer | Cost |
|---|---|---|
| 1 | Rules on the scraped pages | free |
| 2 | Free directories (YC, SEC, IRS, Internet Archive) | free |
| 3 | AI reading our pages | about $0.0003 a question |
| 4 | AI web search | about $0.011 a search |
| 5 | Provider: Prospeo | 1 credit per company |

**Status of each layer:**

| Status | Meaning |
|---|---|
| DECIDED | This layer gave the final answer |
| not needed | An earlier layer already decided |
| clue only, passed on | It found a hint, not proof (Needs check), so the next layer was asked |
| nothing found, passed on | It looked and found nothing |
| not counted, passed on | It answered, but the quality checks rejected it (no quote, low confidence, not the same company...) |
| doesn't cover this | This layer can't answer this data point |
| not run (--ai) / not run (--providers) | That layer wasn't run for this company |

Each panel also shows that data point's settings and its file (`config/datapoints/...`). To change how a data point is answered, edit its file, then re-run that company: `python -m enrich rules <list>` is free; new AI questions need `ai_check.py` (asks first). A layer you switched off shows as "off in its file".

**A good waterfall:** the deciding layer is the cheapest that could know; later layers say "not needed"; a data point still empty after every layer is genuinely not findable.

---

## 5. Manual review: you check every data point

There are two ways to review: **one data point at a time across every company** (best for judging a data point's waterfall), or **one company at a time** (best for judging a company as a whole).

### Step by step, one data point at a time

```bash
cd /Users/ali/Desktop/BtB/puzzle/ICP
source .venv/bin/activate
python -m enrich datapoints
python -m enrich datapoints --show M1
python -m enrich review accuracy_test --only M1
```

The first two commands show every data point's waterfall settings and then one in full. The last shows M1's waterfall for each company in turn. Then do M2, M3 and so on in this order:

| Group | Ids | Companies shown (test list) |
|---|---|---|
| Must-haves | M1, M2, M3, M4 | all 10 |
| Exclusions | E1, E2, E3, E4, E5, E6 | all 10 |
| Fit | F1 to F21 | the companies that pass phase 1 (Mintlify, thoughtbot) |
| Buying | B1 to B14 | the companies that pass phase 1 |
| Weak fit | W1 to W12 | the companies that pass phase 1 |

Several at once: `python -m enrich review accuracy_test --only E1,E2,E3,E4,E5,E6`. All 57 ids and their names: [SIGNALS.md](SIGNALS.md#quick-reference).

### One company at a time

```bash
python -m enrich review accuracy_test --group all
python -m enrich review linear.app
```

The first reviews every company in the list, section by section (Must-haves, Exclusions, Fit, Buying, Weak fit), and asks after each company whether its ICP verdict is right. The second reviews one company that isn't in a list. Groups: `--group must`, `exclusions`, `fit`, `buying`, `weak`, `all`.

### Your keys

| Key | Meaning |
|---|---|
| Enter | Right |
| n | Wrong: then the right answer (y = Yes, n = No, u = can't tell) and a short note |
| s | Skip this data point |
| c | Skip the rest of this company (one company at a time) |
| q | Stop. Saved as you go |

Running the same command again skips what you've already reviewed; add `--redo` to see those again.

**What each panel shows:** the final answer and the layer that decided it, the proof and its page, the five layers with what each found and its status, and the data point's waterfall settings with the file to edit (for example `config/datapoints/must_haves/based_in_us.json`).

**How much to review:** 57 data points for a company that passes phase 1; 10 (must-haves and exclusions) for one that doesn't, because phase 2 never counts for a NO. The test list is about 204 checks.

### When a data point's waterfall needs changing

```bash
open config/datapoints/must_haves/uses_fintech_tool.json
python -m enrich rules accuracy_test
python -m enrich review accuracy_test --only M3 --redo
```

Edit its file (for example set `ai_pages.run` to `if_open`), re-apply for free, and check it again. If the change adds AI questions, run `python ai_check.py accuracy_test --phase icp` (or `--phase signals` for fit, buying and weak data points); it shows the cost and asks first. The settings are explained in [PIPELINE.md](PIPELINE.md#1-the-whole-flow) and by `python -m enrich datapoints`.

**What makes an answer wrong:**

| Mark wrong when | Example note |
|---|---|
| The quote doesn't prove the answer | "quote says $100M ARR, that's revenue not a round" |
| The proof is about someone else | "Ramp is a customer here, not their card provider" |
| The page is the wrong kind | "this 'engineering blog' is an integrations listing" |
| A Yes or No was a guess | "California law in the terms doesn't mean not Delaware" |
| It's empty but the answer was clearly findable | "the US address is in the footer of /contact" |
| The deciding source is out of date | "that funding round is from 2019; they've raised since" |

Keep the note short and specific: what is wrong and where the right proof is. That tells us exactly which rule or prompt to fix.

**Where it goes:**

| File | Use |
|---|---|
| `lists/<list>/output/review.csv` | Every verdict with our answer, deciding layer, proof, page, your verdict, right answer and note. Tell Claude **"check the review"** to go through what you marked wrong |
| `lists/<list>/output/labels.csv` | Your answers as labels: `python -m enrich evaluate <list> --labels lists/<list>/output/labels.csv` |

The data point definitions and what counts as proof for each are in [SIGNALS.md](SIGNALS.md).

---

### One data point at a time through MCP (live review)

Isolated test lists only (names start with `dp_test`): the source list is copied read-only, answers stay in the test
list, nothing goes to the shared answer cache, no OpenAI or Prospeo calls (the attached model answers AI stages, $0 API).

In Claude Desktop, after `enrich_list` on a CSV (for example `examples/mcp_test.csv` as list `mcp_test`):

1. "Set up a dp_test list from mcp_test" (`dp_test_setup`)
2. "Run M1 for resend.com in dp_test" (`dp_test_run`): every stage with status, evidence, source, confidence, why the
   next stage ran or not, cost, final answer. Claude then waits for you.
3. Your review (`dp_test_record`): **approve**, **reject**, **correct** (give the expected value and notes),
   **investigate** (more evidence; stays on this data point), **retry** (re-runs only this data point), **stop**.
4. Claude asks "Ready to move to the next data point?" and runs the next one only after you say yes.
5. "Show dp_test progress" (`dp_test_summary`): tested, reviewed, approved, rejected, corrected, investigating,
   retries, current and remaining of the 57.

Test mode asks every question, also those a normal run would hold back (a data point waiting for its parent, or
skipped because the tier can't change); the result says what a normal run would do.

Reviews: `lists/dp_test/output/dp_test_log.csv`. Session: `lists/dp_test/output/dp_test_session.json`.
Automated tests for this mode (offline): `.venv/bin/python -m unittest discover -s tests -v`.

## 6. Measure accuracy: `evaluate`

```bash
python -m enrich evaluate accuracy_test --labels examples/accuracy_test.csv
python -m enrich evaluate accuracy_test --labels lists/accuracy_test/output/labels.csv
python -m enrich evaluate --template labels.csv
```

The first uses the test set, the second your review, and the third writes a blank answer key.

| Number | Meaning | Target |
|---|---|---|
| Good companies wrongly marked NO | Leads lost for good: the most important number | 0 |
| Companies wrongly marked YES | Costs a sales look, not a lead | As low as possible |
| ICP accuracy (decided) | Right YES/NO among companies not PENDING | 95%+ |
| Not decided | PENDING: site not reached or a check still open | Low; each one should have a clear reason |
| Tier accuracy | Strong / Weak fit right | 90%+, once the cut-off is set from Puzzle's labels |
| Per data point | Right, wrong, empty for each data point you labelled | Find the weakest ones |

Every disagreement, with our proof, is in `lists/<list>/output/evaluation.csv`. Each run is logged: `python -m enrich runs accuracy_test`.

**Setting the Strong fit cut-off:** once Puzzle's labelled list is in, compare the scores of customers with lost deals and pick the cut-off (now 12 in `config/scoring.json`) that separates them best.

---

## 7. After any change: regression checklist

Run after changing a rule, a prompt, `scoring.json` or `profile.yaml`:

1. `python -m enrich rules accuracy_test` (free; re-applies rules and saved answers without downloading)
2. `python -m enrich evaluate accuracy_test --labels examples/accuracy_test.csv`: same or better, and still 0 good companies wrongly NO
3. `python -m enrich evaluate accuracy_test --labels lists/accuracy_test/output/labels.csv`: your review still agrees
4. `python -m enrich trace linear.app --no-pause`: still NO, with the Series C proof
5. For a prompt change: answers made with the old prompt are re-asked automatically (version stamps); run `python ai_check.py accuracy_test --phase icp` and evaluate again

### Speed: `bench`

`python -m enrich bench leads.csv --label before` runs the free steps (download, job boards, code scan, directories,
rules; no AI, no Prospeo) on a fresh copy of the list, the same way MCP and the API do, and saves the wall time,
seconds per stage per company (median and slowest) and every answer. After a change, run it again with another label
and `python -m enrich bench --compare before after`: the speed-up, and every answer that differs. Live sites can
change between runs, so check any difference before calling it a regression.

### Comparing two runs

```bash
python -m enrich copy accuracy_test accuracy_test_b
python ai_check.py accuracy_test_b --phase icp --mode cheap
python -m enrich compare accuracy_test accuracy_test_b
```

`copy` clones the list without its AI answers. Then run the other setting on the copy, and `compare` lists every answer that differs. Use it for AI modes, prompt changes or a different model. Then evaluate both.

---

## 8. Testing the connections

**API** (`python -m enrich serve`, docs at http://127.0.0.1:8787/docs):

```bash
KEY=$(python -c "from enrich.core import settings; print(settings.api_key(create=False))")
curl -s http://127.0.0.1:8787/health
curl -s -X POST http://127.0.0.1:8787/v1/check -H "x-api-key: $KEY" -H "Content-Type: application/json" -d '{"domain": "linear.app"}'
curl -s -X POST http://127.0.0.1:8787/v1/lists/accuracy_test/ai -H "x-api-key: $KEY" -H "Content-Type: application/json" -d '{"phase": "icp", "ai_options": {"max_cost_usd": 0.01}}'
```

Expected: `{"ok":true}`; then a result with `ICP`, `Lead score`, `Reasoning`; then `402`, because the cost limit is too low and nothing is sent. Also: a wrong key gives `401`; a list started with `POST /v1/lists` carries on by itself if the server is restarted.

**MCP** (Claude Desktop or Claude Code): ask "What's the status of accuracy_test?", "Explain mintlify.com from accuracy_test", "Show my Clay tables". Setup: [INTEGRATIONS.md](INTEGRATIONS.md).

**Prospeo** (needs `PROSPEO_API_KEY`): `python -m enrich waterfall thoughtbot.com --providers` (1 credit), then `python -m enrich people accuracy_test --limit 2` (2 credits, emails off). Check: the data points Prospeo decided are right, contacts work at that company, and a "public" or "outside the US" from Prospeo only shows as Needs check.

**Clay:** `python -m enrich pull` lists your tables; pushing needs a Clay table with a Webhook source ([INTEGRATIONS.md](INTEGRATIONS.md)).

---

## Reporting a problem

Send (or tell Claude): the domain, the data point, what we said, what is right, and where the right proof is. A `review.csv` row has all of it. Each fix is added to the test set or your labels, so `evaluate` keeps checking it.
