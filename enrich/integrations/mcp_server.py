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
from . import service
from ..checks.rules import SIGNALS, VALUE_NAMES  # noqa: E402
from ..core.store import Store  # noqa: E402

mcp = MCPServer(
    name="puzzle-lead-scoring",
    instructions=(
        "Checks companies against Puzzle's ideal customer profile (ICP) and scores them. "
        "check_company answers for one domain. enrich_list and pull_clay_table run whole lists in the background; "
        "follow up with list_status and list_results. Results say ICP YES / NO / PENDING with the reason, a lead score "
        "(high 3, medium 2, low 1 per signal) and the signals found. Set use_ai only when the user agrees to spend on OpenAI."
    ),
)


@mcp.tool()
async def check_company(domain: str, company: str = "", employees: str = "", include_signals: bool = True,
                        use_ai: bool = False) -> dict:
    """Check one company against the ICP and score it. Example: domain='linear.app'.
    use_ai runs OpenAI on open questions (costs money; needs OPENAI_API_KEY)."""
    return await service.check_one(domain, company, employees, include_signals, use_ai, wait_seconds=120)


@mcp.tool()
async def enrich_list(csv_path: str, list_name: str = "", include_signals: bool = True, use_ai: bool = False,
                      push_to_clay: bool = False) -> dict:
    """Start enriching a CSV file of companies (needs a Domain or Website column). Runs in the background;
    check progress with list_status. push_to_clay sends the results to the Clay webhook in settings when done."""
    import csv
    from pathlib import Path
    with open(Path(csv_path).expanduser(), newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return service.start_list(list_name or Path(csv_path).stem, rows, include_signals, use_ai, True, push_to_clay,
                              source="mcp")


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
                          use_ai: bool = False, push_to_clay: bool = False) -> dict:
    """Read a Clay table (id like t_abc123) and enrich it as a new list in the background."""
    return service.start_from_clay(table_id, list_name or None, limit, signals=include_signals, use_ai=use_ai,
                                   web=True, push_to_clay=push_to_clay)


@mcp.tool()
async def push_list_to_clay(list_name: str, webhook_url: str = "", only_icp: bool = False) -> dict:
    """Send a finished list's rows to a Clay table webhook (one row per request). Default webhook comes from settings."""
    return await service.push_list(list_name, webhook_url or None, only_icp)


def main():
    mcp.run("stdio")


if __name__ == "__main__":
    main()
