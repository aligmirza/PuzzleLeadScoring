# Improvements log

What the pipeline changed about itself, from what it learned while scoring leads (`enrich/core/learning.py`, MCP `improvement_report`). Safe changes (crash fixes, web search switched to on-clue for a phase 2 data point that almost never finds anything) are made automatically and reported; prompt, rule and workflow changes only after approval. Newest last.

| Date | Proposal | Status | What and why |
|---|---|---|---|
| 2026-11-09 | M2: no working website is Unknown in services | applied (approved) | Ridgeline Drilling (coming-soon page) and CanAm Logistics (404), both paying customers, were failed on M2. `website_optional` in the services roles.json. |
| 2026-11-09 | W6: foreign prices on a pricing page need the AI | applied (approved) | Dueflow, a paying customer, was excluded for a UK fee table ("min £1"). Now Needs check; the AI answered Unknown. |
| 2026-11-09 | F16: 'hybrid' only next to work words | applied (approved) | Maverick Marketing got 'remote or hybrid' from "a hybrid between direct mail". |
| 2026-11-09 | F15 prompt: counts of people, projects or years are not growth | applied (approved) | Yes was given for "helped thousands of people", "over 20 dental practices", "4,200+ roofs". |
| 2026-11-09 | Services weights favour startups over won deals | proposed | 26-deal test: average score 1.8 for won vs 3.2 for lost. Lower startup weights; add HubSpot service type and deal stage as signals. Needs the user's and Alexey's OK. |
| 2026-11-09 | Code review of the fixes | applied (approved) | A dead domain or a for-sale page passed as YES: only a coming-soon page or a 404 is Unknown now, and with nothing proven the verdict is 'Needs a call: no website' (PENDING). 'hybrid model' still matched: tightened. The W6 change hit product too: now only where W6 is a hard stop. W6 prompt updated (v2). website_optional is a boolean. |
| 2026-11-13 | Services weights favour startups over won deals | decided: kept | The goal is recall for cold outreach, not predicting won vs lost; the score only orders the approved leads (Outreach priority). |
| 2026-11-13 | Pre-launch startups pass (soft M2/M4) | applied | 11 of 82 real leads were pre-launch and failed; several bought. Now "Yes (pre-launch)", priority C. Parked, for-sale, shut-down and dead domains still fail. |
| 2026-11-13 | E6, E5, S2, F17, B8: hard stop → weak signal (services) | applied | Verse, BoxHero, SEYA, Wealthie, Phoenix Bikes and Trinity River Church were real leads (Verse, SEYA and Wealthie won). -2 and "check on the call". |
| 2026-11-13 | Outreach priority column | applied | A at score 6+, B at 3+, else C; pre-launch and no-website leads are C. On the 82: A 9, B 22, C 41 (final). |
| 2026-11-13 | Rule fixes from the 82 leads | applied | Stripe donation button is not billing (F2); bios are not company age (S12); "our 150+ team" counts for S7; "work from anywhere" only on careers pages (F16); GoDaddy for-sale is parked; a 3PL warehouse is not own stock (E6 prompt). |
| 2026-11-13 | No working website is a NO (services) | applied (user) | Cold outreach skips leads without a website: `website_optional` off; a 404, coming-soon or parked page is a hard No on M2. Rejects CanAm, Ridgeline and Hub & Ranch (won) on purpose. |
| 2026-11-13 | Registered domain with no web address is no website | applied | Starson Tech, The Sense Lab and Benath Dev: public DNS has no address for the domain or www, so the site is dead (was "retry later"). |
| 2026-11-13 | Builder placeholder pages are coming-soon pages | applied | Harmonic Systems: "Launching Soon" repeated with a form and cookie banner passed the 600-character limit; now also judged by few distinct words. |
| 2026-11-13 | Bot check (CAPTCHA): fall back to the AI web step | applied (user) | A blocked site's must-haves and hard stops go to the web step instead of waiting for a retry; the check is never bypassed. Nothing found on the web: PENDING. |
| 2026-11-13 | Windows support | applied | Every text file is read and written as UTF-8 (73 places; Windows would use cp1252 and crash on ’ or —), with a test; Windows setup steps in README and DOCUMENTATION. |
