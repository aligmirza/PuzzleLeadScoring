---
description: Enrich a CSV list (up to about 100 companies) with the full waterfall, Claude answering the AI questions in chat
argument-hint: <path/to/list.csv> [list_name]
---

Enrich this list with the puzzle-lead-scoring MCP server, Claude answering the AI steps: $ARGUMENTS

1. Call `enrich_list` with the CSV path (and the list name if given). Then call `list_status` every 30 seconds until
   the state is `done`. Tell me how many companies and the ICP YES / NO / PENDING counts.
2. Call `questions_overview(list_name)`. If more than 100 companies have open questions, stop and suggest `list_ai`
   (OpenAI, with a cost estimate first) instead of answering in chat.
3. Take `work_order` from `questions_overview` (best leads first) and, in that order, start one `company-answerer`
   subagent per company (give it the domain and the list name). Run them 5 at a time in parallel. If the usage limit
   is reached, stop and tell me how far it got: running `/enrich-chat` again later continues with what's left.
   After each batch, print one progress line: `answered X of Y companies`. Keep the chat short: only the subagents'
   one-line results.
4. Call `questions_overview(list_name)` again and report what is still open (only questions the subagents couldn't
   answer should remain).
5. Step 5, Prospeo: count the ICP YES companies whose finance check (F13, F14) is still open. Ask me: "Run the Prospeo
   finance check for N companies (at most N credits)?" Only if I say yes, call `check_company(domain, use_prospeo=true)`
   for each of them.
6. Call `list_results(list_name)` and show a short table: company, ICP, score, tier, top signals. Say where the files
   are (`lists/<list_name>/output/`, contacts in `<list_name>_contacts.csv`).

7. As the GTM engineer who owns this pipeline, run the **improve-pipeline** skill: say what was fixed automatically,
   fix any crash (tests must pass), and show the other proposals with their proof for my OK.

Never call `list_ai` or anything with OpenAI in this command unless I ask for it.
