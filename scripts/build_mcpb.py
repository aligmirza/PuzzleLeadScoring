"""Build the one-click Claude Desktop extension: connectors/puzzle-lead-scoring.mcpb.

    .venv/bin/python scripts/build_mcpb.py

Writes connectors/claude-desktop/manifest.json (the tool list comes from the MCP server itself, so it never goes
stale) and packs it with the launcher into one .mcpb file (a zip). Double-click the .mcpb to install it in Claude
Desktop on macOS or Windows; it asks once for the project folder. Rebuild after adding or renaming a tool.
"""
import asyncio
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SRC = ROOT / "connectors" / "claude-desktop"
OUT = ROOT / "connectors" / "puzzle-lead-scoring.mcpb"
VERSION = "1.1.0"


def manifest() -> dict:
    from enrich.integrations.mcp_server import mcp
    tools = asyncio.run(mcp.list_tools())
    folder = "${user_config.project_dir}"
    return {
        "manifest_version": "0.3",
        "name": "puzzle-lead-scoring",
        "display_name": "Puzzle Lead Scoring",
        "version": VERSION,
        "description": "Check companies against Puzzle's ideal customer profile, score them and set the outreach order.",
        "long_description": (
            "Runs the Puzzle lead scoring pipeline from your copy of the PuzzleLeadScoring repository. Ask in plain "
            "English: \"check acme.com\", \"score leads.csv\", \"find decision-makers at the ICP companies\", \"push "
            "the results to Clay\". Claude answers the AI questions itself, so lists up to about 100 companies cost "
            "nothing in OpenAI. Prospeo credits are spent only after you approve a limit. Needs the repository set up "
            "once (docs/HANDOFF.md, Setup)."),
        "author": {"name": "Beat the Bush", "url": "https://beatthebush.co"},
        "repository": {"type": "git", "url": "https://github.com/aligmirza/PuzzleLeadScoring"},
        "server": {
            "type": "python",
            "entry_point": "server/main.py",
            "mcp_config": {
                "command": f"{folder}/.venv/bin/python",
                "args": ["${__dirname}/server/main.py"],
                "env": {"PUZZLE_PROJECT_DIR": folder, "PYTHONUTF8": "1"},
                "platform_overrides": {"win32": {"command": f"{folder}\\.venv\\Scripts\\python.exe"}},
            },
        },
        "user_config": {
            "project_dir": {
                "type": "directory",
                "title": "PuzzleLeadScoring folder",
                "description": "The folder you cloned the repository into (it holds enrich/, config/ and .venv/).",
                "required": True,
            },
        },
        "tools": [{"name": t.name, "description": (t.description or "").strip().split("\n")[0][:300]} for t in tools],
        "compatibility": {"platforms": ["darwin", "win32", "linux"]},
    }


def main() -> None:
    m = manifest()
    (SRC / "manifest.json").write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(SRC / "manifest.json", "manifest.json")
        z.write(SRC / "server" / "main.py", "server/main.py")
    print(f"Built {OUT.relative_to(ROOT)} ({len(m['tools'])} tools). Double-click it to install in Claude Desktop.")


if __name__ == "__main__":
    main()
