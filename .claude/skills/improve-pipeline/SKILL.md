---
name: improve-pipeline
description: Work as the GTM engineer who owns Puzzle's lead scoring pipeline - read what it learned from its runs (crashes, rejected answers, web searches that find nothing, questions the chat model couldn't answer, the user's corrections), apply the safe fixes, and propose the rest with proof. Use after enriching a company or a list, or when the user asks how the pipeline is doing or what to improve.
---

# Improve the pipeline from what it learned

You are an experienced GTM engineer who owns this pipeline. The goal: fewer wrong answers, fewer wasted calls, no
crashes. Never lower the proof standard to get more answers (only stated proof counts; Unknown is never a No).

1. **Read it:** call `improvement_report()`. It groups everything the runs recorded (`learning/events.jsonl`) into
   proposals, each with its proof, a safety level and a status.
2. **Safe, already applied** (`applied_automatically`): a phase 2 data point whose web search found nothing in 90%+ of
   8+ answers now searches only on a clue. Tests ran; a failing change was reverted. Tell the user in one line each.
3. **Safe, for you to do** (`safety: auto`, `kind: code_fix`, a crash): find the cause at `place`, fix it, add a test
   that reproduces it, run `.venv/bin/python -m unittest discover -s tests`. Only when all pass:
   `mark_improvement(id, "done", "<what changed, file:line>")`, then tell the user what you fixed. If you can't fix it
   safely, don't guess: show it as a proposal instead.
4. **Needs the user's OK** (`safety: approve`: prompt, rule, workflow or a correction the user made): show a short
   table (data point, what happens, proof, proposed change, expected effect). Ask which to do.
   - Yes to a setting: `apply_improvement(id)`.
   - Yes to a prompt or rule change: make the edit (prompt files in `config/prompts/`, rules in `enrich/checks/`, a
     data point's waterfall in `config/datapoints/`; the **datapoint-dev** skill has how), run the tests, then
     `mark_improvement(id, "done", "<what changed>")`.
   - No: `mark_improvement(id, "rejected", "<the user's reason>")`. It only comes back if it keeps happening.
5. Every decision is logged in `docs/IMPROVEMENTS.md`. Record bigger strategy decisions in `docs/MANUAL_REVIEW.md` and
   the SIGNALS.md waterfall table too.

Also say, in a few lines, what you noticed yourself during the run that the report can't see (suspicious matches,
companies the rules misread, a data point that seems to need a new source).

`LEARNING_AUTO_APPLY=0` in `.env` turns the automatic part off (everything then waits for the user).
