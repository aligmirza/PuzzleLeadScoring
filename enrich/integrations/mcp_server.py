"""MCP server: the pipeline as tools for Claude Code, Claude Desktop or any MCP client.

Runs locally over stdio:   python -m enrich mcp
(Registered for this project in .mcp.json.) Later, on a server, the same tools can run over HTTP.
"""
import sys

from ..pipeline import common

# stdout carries the MCP protocol, so all of the tool's own printing goes to stderr
common.console.file = sys.stderr

from mcp.server.mcpserver import MCPServer  # noqa: E402

from . import clay  # noqa: E402
from . import client_ai, service  # noqa: E402
from ..checks.rules import SIGNALS, VALUE_NAMES  # noqa: E402
from ..core.store import Store  # noqa: E402

mcp = MCPServer(
    name="puzzle-lead-scoring",
    instructions=(
        "Checks companies against Puzzle's ideal customer profile (ICP) and scores them. Through MCP YOU are the only "
        "AI: you answer every AI question with the project's prompts; the tool never calls OpenAI or any other AI API. "
        "A question you can't answer the way it asks (e.g. no web search tool) is sent back as cannot_answer and stays open. "
        "Each data point follows its own waterfall (config/datapoints): free steps in check_company / enrich_list, then "
        "your answers (pages, then web), then step 5, Prospeo, only for the finance-person check: when submit_answers "
        "says it's next, ask the user (1 credit per company) and only then call check_company with use_prospeo=true. Workflow for a company: check_company(domain) runs the free layers "
        "(website rules and directories); then open_questions(domain) lists what is still open, with the page text and "
        "instructions; answer each one yourself (read the page text, or search the web for answer_from = web if you can), "
        "quoting your proof exactly; then submit_answers. Answers pass the same quality checks as any AI answer, and the "
        "result says which counted. Repeat open_questions until nothing is left; if the company becomes ICP YES, run "
        "check_company again with include_signals and continue with phase 'signals'. For a whole list: enrich_list, "
        "list_status, then questions_overview to see which companies have open questions. Results say ICP YES / NO / "
        "PENDING with the reason, a lead score (high 3, medium 2, low 1 per signal) and the proof for every answer. "
        "TEST MODE (dp_test_* tools, isolated test lists only): one data point at a time. After every dp_test_run (or "
        "retry) show the user the complete result, then wait for them to choose approve, reject, investigate, retry, "
        "correct or stop, and pass exactly that to dp_test_record. Never review on the user's behalf and never move on by "
        "yourself: after a review ask 'Ready to move to the next data point?' and call dp_test_run with confirm_next=true "
        "only after an explicit yes. If dp_test_run says an AI stage is waiting, you may answer it with dp_test_answer "
        "(that is still the same data point) and show the updated result."
    ),
)


@mcp.tool()
async def check_company(domain: str, company: str = "", employees: str = "", include_signals: bool = True,
                        use_prospeo: bool = False) -> dict:
    """Check one company against the ICP: the free waterfall steps (size, website rules and code checks, YC, SEC,
    IRS, DNS). Example: domain='linear.app'. No OpenAI: questions still open are answered by you via open_questions
    and submit_answers. use_prospeo=true (only after the user agrees: 1 Prospeo credit at most) runs step 5, the
    finance-person check (F13, F14), if it is still open after your answers."""
    return await service.check_one(domain, company, employees, include_signals, False, wait_seconds=120,
                                   use_prospeo=use_prospeo)


@mcp.tool()
async def enrich_list(csv_path: str, list_name: str = "", include_signals: bool = True, push_to_clay: bool = False,
                      use_prospeo: bool = False) -> dict:
    """Start enriching a CSV file of companies (needs a Domain or Website column) with the free waterfall steps.
    Runs in the background; check progress with list_status, then answer open questions (questions_overview).
    push_to_clay sends the results to the Clay webhook in settings when done. use_prospeo=true (only after the user
    agrees: at most 1 Prospeo credit per company) runs the finance-person check where it is still open."""
    import csv
    from pathlib import Path
    with open(Path(csv_path).expanduser(), newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return service.start_list(list_name or Path(csv_path).stem, rows, include_signals, False, True, push_to_clay,
                              source="mcp", use_prospeo=use_prospeo)


@mcp.tool()
def list_status(list_name: str) -> dict:
    """Progress and summary of a list: state, current step, ICP YES / NO / PENDING counts, Strong fit count."""
    return service.list_status(list_name)


@mcp.tool()
def list_results(list_name: str, only_icp: bool = True, limit: int = 25) -> dict:
    """Rows of a finished list with ICP, lead score, tier and signals found. only_icp=True returns ICP = YES only."""
    keep = ("Domain", "Company Name", "Company", "ICP", "ICP status", "Why", "Lead score", "Lead tier", "Segment",
            "Vertical", "Fit signals found", "Buying signals found", "Weak fit signals found", "Funding stage",
            "Partners found", "Notes")
    rows = service.list_results(list_name, only_icp, limit)
    return {"count": len(rows), "rows": [{k: v for k, v in r.items() if k in keep and v} for r in rows]}


@mcp.tool()
def explain_company(domain: str, list_name: str = service.SINGLE_LIST) -> dict:
    """Every answer for one company with its proof (quote and page link), for phase 1 and phase 2."""
    from ..pipeline.common import LISTS
    from ..core.inputs import normalize_domain
    d = normalize_domain(domain) or domain
    store = Store(LISTS / list_name)
    out = {}
    for phase in ("icp", "signals"):
        r = store.get_result(d, phase)
        if r:
            out[phase] = {SIGNALS[k][1]: {"answer": VALUE_NAMES[v["value"]], "proof": v["evidence"], "page": v["url"],
                                          "note": v["note"]}
                          for k, v in r["signals"].items() if v["value"] != "unknown"}
            if phase == "icp":
                out["icp_verdict"] = r.get("icp_verdict")
    if not out:
        raise ValueError(f"No result for {d} in list '{list_name}'. Run check_company first.")
    return out


@mcp.tool()
def clay_tables() -> dict:
    """Tables in the signed-in Clay workspace (id, name, workbook)."""
    return {"tables": clay.list_tables()}


@mcp.tool()
async def pull_clay_table(table_id: str, list_name: str = "", limit: int = 0, include_signals: bool = True,
                          push_to_clay: bool = False) -> dict:
    """Read a Clay table (id like t_abc123) and enrich it as a new list in the background."""
    return service.start_from_clay(table_id, list_name or None, limit, signals=include_signals, use_ai=False,
                                   web=True, push_to_clay=push_to_clay)


@mcp.tool()
async def push_list_to_clay(list_name: str, webhook_url: str = "", only_icp: bool = False) -> dict:
    """Send a finished list's rows to a Clay table webhook (one row per request). Default webhook comes from settings."""
    return await service.push_list(list_name, webhook_url or None, only_icp)


@mcp.tool()
def open_questions(domain: str, list_name: str = service.SINGLE_LIST, phase: str = "auto", limit: int = 12) -> dict:
    """The questions the free layers left open for a company, for YOU to answer (not OpenAI): the company's page text,
    each question's instructions and its answer fields, and whether to answer from the pages or the web.
    phase: auto (default), icp or signals. Answer them, then call submit_answers."""
    return client_ai.open_questions(domain, list_name, phase, limit)


@mcp.tool()
def submit_answers(domain: str, answers: list[dict], list_name: str = service.SINGLE_LIST, phase: str = "auto") -> dict:
    """Save your answers to open_questions. Each answer: {"id": <question id>, "answer_from": "pages" or "web", plus the
    answer fields (answer, confidence, evidence_quote, source, reason, and for web: entity_match)}. They pass the same
    quality checks as any AI answer; the result says which counted, the new ICP verdict and score, and what's next.
    A question you can't answer the way it asks (e.g. web, and you have no web search tool): send
    {"id", "answer_from", "cannot_answer": "<why>"}; it stays open. No other AI (no OpenAI) is ever used through MCP."""
    return client_ai.submit_answers(domain, answers, list_name, phase)


@mcp.tool()
def questions_overview(list_name: str) -> dict:
    """For a whole list: how many questions are still open per company (phase 1 and phase 2), to work through them."""
    return client_ai.overview(list_name)


# single data point test mode (isolated: lists named dp_test*, never the shared answer cache) --------------------------
from . import dp_test  # noqa: E402


@mcp.tool()
def dp_test_setup(source_list: str = "mcp_test", test_list: str = "dp_test", reset: bool = False) -> dict:
    """TEST MODE. Copy a finished list into an isolated test list (name must start with dp_test), without AI answers.
    The source list is opened read-only and never changed."""
    return dp_test.setup(source_list, test_list, reset)


@mcp.tool()
def dp_test_run(domain: str, data_point: str, test_list: str = "dp_test", confirm_next: bool = False) -> dict:
    """TEST MODE. Run ONE data point (ID like E1 or its name) for ONE company through its own waterfall and return
    every stage: executed / skipped / waiting, source, evidence, output, confidence, why the next stage was or wasn't
    triggered, actual and estimated cost, the final answer, and the next question if an AI stage is waiting.
    Then STOP: show the user the whole result and wait for their review (dp_test_record). While a result awaits review
    nothing else runs (the same data point again is a retry). After a review, pass confirm_next=true only once the
    user has said yes to "Ready to move to the next data point?"."""
    return dp_test.run(domain, data_point, test_list, confirm_next)


@mcp.tool()
def dp_test_answer(domain: str, data_point: str, answer: dict, test_list: str = "dp_test") -> dict:
    """TEST MODE. Save YOUR answer for the stage dp_test_run says is next (answer_from 'pages' or 'web', plus the
    answer fields). Same quality checks as production; saved to the test list only, never the shared cache."""
    return dp_test.answer(domain, data_point, answer, test_list)


@mcp.tool()
def dp_test_record(action: str, expected_value: str = "", notes: str = "", reference: str = "", domain: str = "",
                   data_point: str = "", test_list: str = "dp_test") -> dict:
    """TEST MODE. The user's review of the data point under test; call it only with the action THE USER chose.
    approve | reject | correct (expected_value required, notes optional) | partially_correct | insufficient_evidence |
    conflicting_evidence: logged, then ask "Ready to move to the next data point?" and wait.
    investigate: returns more evidence and how the result was produced; stays on this data point.
    retry: re-runs only this data point (its test answers cleared); stays on it.  stop: ends the session.
    domain and data_point default to the current one; progress across the 57 data points comes back every time."""
    return dp_test.review(domain, data_point, action, expected_value, notes, reference, test_list)


@mcp.tool()
def dp_test_summary(test_list: str = "dp_test") -> dict:
    """TEST MODE. Progress across the 57 data points (tested, reviewed, approved, rejected, corrected, needing
    investigation, retries, current, remaining) and totals from the review log."""
    return dp_test.summary(test_list)


def main():
    mcp.run("stdio")


if __name__ == "__main__":
    main()
