---
name: review-datapoints
description: Run or resume the user's manual review of the 57 data points with the dp_test MCP tools - one data point at a time, full waterfall shown, wait for the user's verdict, ask before moving on, record decisions in docs/MANUAL_REVIEW.md. Use when the user says resume the review, next data point, approve and next, or tests one data point.
---

# Manual data point review

Read `docs/MANUAL_REVIEW.md` first: its standing instructions, the decisions so far and where the review stopped.
Test list: `dp_test_lead_scoring` (isolated copy; production is never touched). Company so far: `argon-ai.com`.

## One data point

1. `dp_test_run(domain, data_point, "dp_test_lead_scoring", confirm_next=<true only after the user said yes/next>)`.
   If it answers `paused`, the previous data point is waiting for a review or a "yes": ask the user.
2. If `next_step` waits for an AI stage, answer it with the **icp-answering** skill via `dp_test_answer` (pages
   first; web only if the waterfall asks next). Never use OpenAI or Prospeo here unless the user asks.
3. Show the result as a short table: step | status | proof (quote + link). One or two lines of explanation, no more.
   Point out real problems (weak proof, missing link, a rule that should have decided it) and offer a fix.
4. Wait. Pass the user's choice to `dp_test_record(action)`: approve, reject, correct (expected_value + notes),
   partially_correct, investigate, retry, stop. Never review for them.
5. Ask "Ready to move to the next data point?" and wait. "approve and next" is both: record, then run the next one.

Order: M1-M4, E1-E6, F1-F21, B1-B14, W1-W12.

## When the user asks for a change

- Make it in the code or config (see the **datapoint-dev** skill), run the tests, then `dp_test_record(action="retry")`
  (or `dp_test_run` again) to show the data point after the change.
- Record the decision: add or update the data point's row in `docs/MANUAL_REVIEW.md` and its row in the
  "Waterfall per data point" table of `docs/SIGNALS.md` (the second table with that ID, not the quick reference).
- Don't commit or push unless asked; then author Muhammad Ali, no Claude co-author line.

## Stopping

`dp_test_record(action="stop")`, then update the status line and "Still to review" in `docs/MANUAL_REVIEW.md`.
