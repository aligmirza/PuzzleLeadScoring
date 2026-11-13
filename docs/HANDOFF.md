# Lead Scoring Model

## Summary

Puzzle now has an automated way to decide which companies to contact in cold outreach, and in what order. Every lead is checked against Puzzle's ideal customer profile before the sales team spends time on it.

* Less wasted sales time: Leads Puzzle cannot serve are removed before outreach.
* A clear call order: The strongest leads are contacted first.
* Better first conversations: Each lead comes with the right offer and an opening line.
* Answers the team can trust: Every conclusion is backed by proof; nothing is guessed.
* Proven on real leads: Tested on 82 real Puzzle leads from HubSpot, including won deals. Nearly 9 in 10 were approved for outreach, and every won customer with a working website passed.
* Low cost: Paid sources are used only with approval, and a company is checked in seconds.
* Owned by Puzzle: The model and all of its code are Puzzle's, in one GitHub repository: [github.com/aligmirza/PuzzleLeadScoring](https://github.com/aligmirza/PuzzleLeadScoring). Once it is transferred to Puzzle's GitHub, this link redirects to the new location.

Good to know:

* The ranking is a guide, not a forecast: it decides who is called first, not who is called.
* Leads without a working website are skipped by design.

## How to Use It

Anyone can score leads by asking Claude, in two steps:

1. Connect Claude to the model. Add the Puzzle Lead Scoring connector in claude.ai under Settings > Connectors, once. This needs no code on your computer: the model runs on Puzzle's server. If the shared connector is not set up yet, clone the repository and set it up (Setup, steps 1 to 3), then double-click `connectors/puzzle-lead-scoring.mcpb` to install it in Claude Desktop, or open Claude Code in the project folder.
2. Ask Claude to score. For example "score leads.csv", "check acme.com", "find emails for the decision-makers" or "push the results to Clay". Claude returns who to contact, in what order, the offer to lead with and the proof.

Scores can also appear directly in Clay, and the model can later be built into HubSpot (see Everyday Use).

The rest of this document is for the team that runs the model.

## Process

There are two models:

* Services (the default): operators who want their bookkeeping done for them.
* Product: early-stage startups for the platform.

Each company goes through the same steps, from the free sources to the paid ones:

* Website and public records: The company's website, job posts and free public records (YC, SEC, IRS, DNS) are read first, at no cost.
* AI: Used only for what is still unknown. Through Claude (MCP) it costs nothing; from the command line it uses OpenAI, after a cost estimate and only with approval.
* Bot checks: If a website shows a bot check (CAPTCHA), the model does not try to get past it. It answers the key questions from other public sources on the web instead.
* LeadMagic: Used only for the finance-person check and for contacts (who to reach, their work email and, if asked, their mobile number), with an approved credit limit. Prospeo works the same way and is used only if there is no LeadMagic key.
* Proof: Every answer carries a short quote and the page it came from. Anything not found stays Unknown and never counts against a company.

How a lead is decided:

   * Hard stops: The lead is rejected when there is proof that it is outside the US, an accounting firm, a sole proprietorship or partnership, keeps its books in another currency, is a manufacturer, or is too big (Series D or later, 100+ people, or NetSuite with a finance team).
   * No working website: A 404, a coming-soon or parked page, or a dead domain means the lead is skipped.
   * Pre-launch: A startup with a real website and a waitlist passes, and is contacted last.
   * Score: Every approved lead gets points for fit and buying signals, minus 2 for each weak signal (for example holding stock, a marketplace, several US companies, a new subsidiary or a non-profit, which are checked on the call).
   * Outreach priority: A (score 6 or more) is contacted first, then B (3 to 5), then C (under 3, and all pre-launch leads).

What sales receives for each company:

   * ICP: YES (contact), NO (skip) or PENDING (still being checked).
   * Outreach priority: A, B or C.
   * Reason: one line, for example "All 4 must-haves confirmed, no exclusion found".
   * Route: the offer to lead with, for example Full Service, Expert Review, Platform + MCP or a partner referral.
   * Value angle: the opening line, for example "Clean monthly closes across processors, subscriptions, refunds and deferred revenue".
   * Lead score and tier, and the proof behind every answer.

## Setup

An engineer needs about an hour, with Puzzle's own accounts and keys. Keys live only in the `.env` file, which is never committed. It runs on macOS, Linux and Windows.

1. Install. Clone the repo (`git clone https://github.com/aligmirza/PuzzleLeadScoring.git`), then run `python3 -m venv .venv` and `.venv/bin/pip install -r requirements.txt`. On Windows use `py -m venv .venv` and `.venv\Scripts\pip`, and `.venv\Scripts\python` wherever these steps say `.venv/bin/python`.
2. Add the keys. On the server, use the hosted connector's settings page; with the Claude Desktop extension, its Configure screen (both in Everyday Use). Otherwise run `python -m enrich settings` to add OpenAI (AI step for big lists), LeadMagic (contacts, their emails and mobiles, and company data) and the Clay webhook URLs.
3. Check it works. Run the 14-company test list from the README, then `.venv/bin/python -m unittest discover -s tests`.
4. Start the API. Run `python -m enrich serve --host 0.0.0.0` on a small always-on server, with HTTPS in front. It needs disk space for saved pages, and Chrome for sites that only load with JavaScript (or the `--no-browser` option).
5. Share the key. Show it with `python -m enrich settings --show-key` and give it only to the people setting up Clay.

## Everyday Use

There are three ways to use the model:

* Claude (anyone): Connect it once, then ask in plain English, for example "check acme.com", "score leads.csv", "find decision-makers at the ICP companies" or "push the results to Clay". Claude answers the AI questions itself, so lists up to about 100 companies cost nothing in OpenAI. There are two one-click ways to connect:
   * Hosted connector (whole team, no install): Once the server runs (Setup, step 4), start the connector next to it with `python -m enrich mcp --http --host 0.0.0.0 --public-url https://<connector address>`, with HTTPS in front. Each person adds `https://<connector address>/mcp` in claude.ai under Settings > Connectors > Add custom connector, and enters the connector password once (MCP_CONNECTOR_PASSWORD in the server's `.env`). It then works in Claude on the web, desktop and phone. Keys are added in a browser at `https://<connector address>/settings`, signed in with the admin password (MCP_ADMIN_PASSWORD in the server's `.env`, separate from the team's connector password): one box each for the LeadMagic key, the Clay webhooks, the OpenAI key and the monthly caps. Keys are never shown back, only "set" or "not set".
   * Desktop extension (one computer): Double-click `connectors/puzzle-lead-scoring.mcpb` and Claude Desktop installs it on macOS or Windows, asking once for the project folder. The repository must be set up on that computer (Setup, steps 1 to 3). Keys can then be added without a terminal: in Claude Desktop, Settings > Extensions > Puzzle Lead Scoring > Configure has a box for the LeadMagic key, the Clay webhooks, the OpenAI key and the monthly caps. Keys are kept in the computer's keychain; boxes left empty use the keys in `.env`. Claude Code connects by itself when opened in the project folder ([steps](DOCUMENTATION.md#mcp-server-claude-code-claude-desktop)).
   * Which one needs the code: The model runs as one piece (website reading, rules, public records, scoring, settings, saved lists and the LeadMagic and Clay keys), so the connector always runs where the code is. With the hosted connector the code lives only on the server, and everyone else just adds the address and enters the password; nothing is installed on their computers. The desktop extension and Claude Code run from a copy of the repository on that computer, set up with its own keys (Setup, steps 1 to 3). For the sales team and leadership, the hosted connector is the one to use.
* Clay (sales and GTM team): Scores appear as columns in Clay tables. In any table, add an HTTP API column:
   1. Method `POST`, endpoint `https://<server address>/v1/check`.
   2. Header `x-api-key` set to the API key.
   3. Body: `{"domain": "<Domain column>", "company": "<Company column>", "employees": "<Employees column>"}`.
   4. Map the answer into columns: ICP, Reason, Outreach priority, Route, Value angle, Lead score, Lead tier.
   5. Start with a rate limit of about 5 requests per 10 seconds. A new company takes 5 to 35 seconds; repeats are instant.

   Whole lists: an engineer can also score a whole list and push it into a Clay table through a webhook. Decision-makers found through LeadMagic, with their emails and mobiles, go into a second Clay table.
* Command line or API (engineers): For big lists or a weekly batch, run `python -m enrich run leads.csv`, then `python -m enrich ai` (shows the cost and asks first), then `python -m enrich push leads`. For contacts: `python -m enrich people leads --emails` (LeadMagic), then `python -m enrich emails leads --phones` for mobile numbers. Every API call is documented in [openapi.json](openapi.json) for the CRM or other tools.

## Changing the Model

The model lives in config files, not code, so most changes are one line. After a change, `python -m enrich rules <list> --phase both` re-scores saved lists for free.

* Points per signal and the A/B priority cut-offs: `config/icps/services/scoring.json`.
* Must-haves, hard stops and weak signals: `config/icps/services/roles.json`.
* Customer types and the profile text: `config/icps/services/profile.yaml`.
* How each data point is found: `config/datapoints/`.

To check the weights against real results, label companies Puzzle already knows are good or bad fits and run `python -m enrich calibrate`. It suggests weights and changes nothing until you add `--apply`. Every change the pipeline makes to itself is logged in `learning/improvements.md`.

## Costs and Safety

The model spends money only when asked:

* Free steps: Website reading, rules and public records always run first.
* OpenAI: Runs only on request, after a cost estimate, and stops at a cost cap ($5 by default for the API).
* LeadMagic: Credits are spent only with an approved limit; the first call shows the most it could use, and `LEADMAGIC_MONTHLY_CREDITS` caps each month. A company's people search costs 1 credit per person returned (1 if nobody is found), an email 1 credit and a mobile 5 (emails and mobiles not found are free). No company or person is paid for twice. On a test, one company plus its finance lead's email cost 4 credits.
* Access: Nothing can be deleted through the API, and every call needs the key. Keep the key and the server address private.

## Further Reading

* [README.md](../README.md): the short overview.
* [DOCUMENTATION.md](DOCUMENTATION.md): everything else, including commands, how the pipeline works, the ICP models, every data point, integrations, testing and decisions.
