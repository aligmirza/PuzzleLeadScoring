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
