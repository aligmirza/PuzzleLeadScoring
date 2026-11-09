# Puzzle Lead Scoring: Hand-off Guide

November 9, 2026

Written for the Puzzle team: the sales and GTM people who use the scores, and the engineer who sets the tool up.

This tool checks a list of companies against Puzzle's ideal customer profile, scores the ones that fit, and tells sales how to approach each one. Puzzle's sales team uses it mainly through Clay; one engineer sets it up once.

## What it does

You give it company domains. It reads each company's website and free public records (YC, SEC, IRS, DNS), then uses AI only for what is still unknown. Every answer comes with a short quote and the page it came from. If something can't be found, it stays Unknown: a company is never marked down for missing information.

The default model is **services**: operators who want their bookkeeping done for them. A second model, **product**, scores early-stage startups for the platform.

Each company gets:

| Column | Example |
| --- | --- |
| ICP | YES, NO or PENDING (still being checked) |
| Reason | All 4 must-haves confirmed, no exclusion found |
| Lead score and tier | 7, Weak fit (Strong fit, Good fit, Weak fit) |
| Route | Qualify on the call: Full Service or Expert Review; Platform + MCP; Partner referral |
| Value angle | Clean monthly closes across processors, subscriptions, refunds and deferred revenue |
| Proof | The quote and link behind every answer |

## Three ways to use it

Most of the team only needs Clay. The other two are for ops and engineering.

| Who | How | What they need |
| --- | --- | --- |
| Sales and GTM team | Scores appear as columns in Clay tables | The API running on a server (set up once) |
| Ops or GTM lead | Ask Claude in plain English: "check acme.com", "score this list" | The repo and Claude Code or Claude Desktop on their computer |
| Engineer | Command line or API for big lists, schedules, or the CRM | The repo on a server or laptop |

## Set up once

An engineer needs about an hour. Use Puzzle's own accounts and keys; keys live only in the `.env` file, which is never committed.

1. Clone the repo and install: `python3 -m venv .venv`, then `.venv/bin/pip install -r requirements.txt`.
2. Add the keys with `python -m enrich settings`: OpenAI (AI step for big lists), Prospeo (company data and contacts) and the Clay webhook URLs.
3. Run the 14-company test list from the README to check it works, then `.venv/bin/python -m unittest discover -s tests`.
4. Start the API on a small always-on server: `python -m enrich serve --host 0.0.0.0`, with HTTPS in front. It needs disk space for saved pages, and Chrome for sites that only load with JavaScript (or the `--no-browser` option).
5. Show the API key with `python -m enrich settings --show-key` and give it only to people setting up Clay.

## Everyday use

**In Clay (sales team).** In any Clay table, add an **HTTP API** column:

1. Method `POST`, endpoint `https://<server address>/v1/check`.
2. Header `x-api-key` = the API key.
3. Body: `{"domain": "<Domain column>", "company": "<Company column>", "employees": "<Employees column>"}`.
4. Map the answer into columns: ICP, Reason, Lead score, Lead tier, Route, Value angle.
5. Start with a rate limit of about 5 requests per 10 seconds. A new company takes 5 to 35 seconds; repeats are instant.

Alternatively, an engineer scores a whole list and pushes it into a Clay table through a webhook. Decision-makers found through Prospeo go into a second Clay table.

**In Claude Code or Claude Desktop (ops).** Connect the MCP server once ([steps](DOCUMENTATION.md#mcp-server-claude-code-claude-desktop)), then ask in plain English, for example "check acme.com", "score leads.csv", "find decision-makers at the ICP companies", "push the results to Clay". Claude answers the AI questions itself, so lists up to about 100 companies cost nothing in OpenAI.

**Command line or API (engineers).** For big lists or a weekly batch: `python -m enrich run leads.csv`, then `python -m enrich ai` (shows the cost and asks first), then `python -m enrich push leads`. Every call is documented in [openapi.json](openapi.json) for the CRM or other tools.

## Changing the model

The model lives in config files, not code, so a change is usually one line. After a change, `python -m enrich rules <list> --phase both` re-scores saved lists in seconds for free.

| To change | File |
| --- | --- |
| Points per signal (high 3, medium 2, low 1) | config/icps/services/scoring.json |
| Must-haves, hard stops and which signals count | config/icps/services/roles.json |
| Customer types and the profile text | config/icps/services/profile.yaml |
| How one data point is found | config/datapoints/ |

To check the weights against real results, label companies Puzzle already knows are good or bad fits and run `python -m enrich calibrate`. It suggests weights and changes nothing until you add `--apply`.

## Costs and safety

- Website reading, rules and public records are free and run first.
- OpenAI runs only on request, after a cost estimate, and stops at a cost cap ($5 by default for the API).
- Prospeo credits are spent only with an approved limit; the first call always shows the most it could use.
- Nothing can be deleted through the API, and every API call needs the key. Keep the key and the server address private.

## Before go-live

A first test on 26 real Puzzle deals (November 9, 2026) found and fixed three rules that rejected paying customers. The model now passes 15 of 17 won deals, but its score does not yet rank won deals above lost ones. The checklist and every open decision are in [STATUS.md](STATUS.md).

## Where to read more

| Doc | Covers |
| --- | --- |
| [README.md](../README.md) | The short overview |
| [DOCUMENTATION.md](DOCUMENTATION.md) | Everything else: setup, commands, how the pipeline works, the ICP models, every data point, integrations, testing, decisions |
| [STATUS.md](STATUS.md) | What is done, the before-go-live checklist, open decisions |
| [IMPROVEMENTS.md](IMPROVEMENTS.md) | Every change the pipeline made to itself, and why |
