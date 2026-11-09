---
name: check-company
description: Check one company (or a handful) against Puzzle's ICP with the full waterfall through the puzzle-lead-scoring MCP server - free steps, Claude answering the AI questions, optional Prospeo finance check - and report verdict, score and proof. Use when the user asks to check, score or enrich a single company or domain.
---

# Check one company

Pipeline: **services** is the default ICP. Use `icp="product"` on every call only when the user asks for the product (platform) ICP, and keep the same `icp` for all calls on a list.

1. `check_company(domain, company?, employees?)`: the free steps (size, website rules and code checks, YC, SEC, IRS,
   DNS) for phase 1 and phase 2. Results are cached, so asking again is instant. If it says "still running", call it
   again after a minute.
2. Answer the open questions with the **icp-answering** skill: `open_questions(domain, limit=2)` → answer →
   `submit_answers`. Repeat until it returns 0 open questions: they come a few at a time (must-haves one by one, then
   hard stops, then web). Phase "auto" moves from phase 1 to phase 2 by itself. Default list: `api_checks`.
   Keep `limit` small: the page text is repeated per question and big batches overflow the tool output.
3. If `submit_answers` says **Step 5 is next**, ask: "Run the Prospeo finance check for <domain> (1 credit)?" Only on
   yes: `check_company(domain, use_prospeo=true)`. Never otherwise.
4. Report briefly:
   - ICP YES / NO / PENDING and the reason (`Why`), segment and its basis, lead score and tier.
   - A table of the signals found, each with its quote and link (from `explain_company(domain)`).
   - What stayed open (Unknown is not a negative).
5. If something went wrong on the way (a crash, answers rejected, a data point that seems misread), run the
   **improve-pipeline** skill.

## For speed and cost

- One `check_company` call covers both phases. Don't call `open_questions` per phase by hand: `phase: auto` does it.
- Don't search the web for a page question; answer pages from `company_page_text` only.
- A company ruled out (ICP NO) gets no phase 2 questions and no Prospeo: stop there.
- More than ~5 companies: use `/enrich-chat` (subagents, 5 in parallel) instead of doing them one by one here.
- Never use `list_ai` (OpenAI) for a single company.
