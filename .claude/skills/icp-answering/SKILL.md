---
name: icp-answering
description: Rules for answering puzzle-lead-scoring AI questions (waterfall steps 3 and 4) through MCP - proof, confidence, entity matching, cannot_answer. Load whenever you answer open_questions, dp_test steps or company-answerer work.
---

# Answering the AI questions (steps 3 and 4)

Through MCP you are the AI. `open_questions` (or `dp_test_run` → `next_step`) gives you, per question, the same
prompt the OpenAI step uses: `system_prompt` (from `config/prompts/_shared.json`), `instructions` (the question's own
`prompt_file`), `answer_fields`, and `company_page_text` for page questions. Follow them exactly.

## The answer

- **pages** (`answer_from: "pages"`): use only `company_page_text`. Job posts are in it ("Open job posts").
- **web** (`answer_from: "web"`): WebSearch, then WebFetch the best source. Prefer the company's own announcements,
  official records (SEC EDGAR), then reputable press. Put the URL in `source`.
- Return every field in `answer_fields`, plus `id` and `answer_from`. Copy `evidence_quote` exactly from the text or
  source (300 characters max).

## Proof rules (the user's standing instructions)

1. **Only stated proof.** Every part of the definition must be shown by the quote. If one part is only implied, the
   answer is `unknown` and the waterfall moves on. Unknown is never a negative.
2. **No absence-based "no".** "The site doesn't mention X" is `unknown`. A reason that says "does not say / no mention"
   gets the answer rejected by the quality checks. Answer `no` only on a positive statement of the opposite.
3. **Confidence:** `high` = the source clearly states it, about this company (domain match), under ~2 years old, any
   source type; `medium` = clearly stated but matched by name and location or founders only; `low` = implied,
   name-only, outdated or sources disagree (never counts).
4. **entity_match (web):** `domain` only if the source shows or links the company's domain; `name_and_location`;
   `name_only`; `none`. Watch for same-name companies (Argon ST vs Argon AI, Stratify vs Stratifyd): ignore them.
5. **Exclusions** (E1-E6 yes) and **must-have no** need `high` + `domain`.
6. **Words that look like signals but aren't:** "hybrid search" (technology, not hybrid work), "chase" inside
   "purchase", "Backed by" followed by logo images (no investor named), boilerplate "our subsidiaries".
7. Can't answer the way it asks (no web tool)? Send `{"id", "answer_from", "cannot_answer": "<why>"}`. It stays open.
   Never guess, never answer from memory.

## Data point notes

- **F1:** the stage matters; quote the round ("$5.5 million in seed funding"), `basis: confirmed` only when stated.
- **F5:** a clearly stated software product = Yes (sells nationally); a services firm needs stated customers beyond
  its city.
- **F8:** prices must be named plans with amounts; "Contact Sales" only is No (the rules usually settle it).
- **F13 / F14:** need a real team list; leave them to Prospeo (step 5), never guess from the founders shown.
- **F15:** the company's own ARR, revenue, customer count or growth, not the market's or its customers'.

## After answering

`submit_answers(domain, answers, list_name)`. Read each answer's `result`: "not counted (...)" tells you why. Call
`open_questions` again while `next` says questions remain. If `next` mentions **Step 5 / Prospeo**, ask the user before
`check_company(domain, use_prospeo=true)` (1 credit).
