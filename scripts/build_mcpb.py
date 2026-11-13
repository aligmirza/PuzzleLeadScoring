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
VERSION = "1.4.0"

# Keys and caps people can enter in Claude Desktop (Settings > Extensions > Puzzle Lead Scoring > Configure), from the
# shared list in enrich/core/settings.py (the hosted connector's /settings page uses the same one). Secret fields are
# kept in the macOS Keychain / Windows Credential Manager and passed to the server as these env vars; they win over
# .env. A field left blank changes nothing: the launcher drops it, so .env's value is used.
# (field, env var, title, description, secret, type)
from enrich.core.settings import EDITABLE  # noqa: E402

SETTINGS = [(var.lower(), var, f"{title} (optional)", f"Optional. {desc} Example: {example}", secret,
             "number" if kind.startswith("number") else "string")
            for var, title, desc, secret, kind, example in EDITABLE]
LIMITS = {var.lower(): [int(x) for x in kind.split(":")[1].split("-")] for var, _, _, _, kind, _ in EDITABLE
          if kind.startswith("number:")}


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
            "the results to Clay\". The AI steps always run: Claude answers them itself for companies and lists up "
            "to about 100 (nothing in OpenAI); bigger lists use OpenAI inside the cost cap set under Configure. LeadMagic credits are spent only after you approve a limit. Needs the repository set up "
            "once (docs/HANDOFF.md, Setup). Add the LeadMagic key and the Clay webhooks under Configure; they are kept in "
            "your system keychain. Keys already in the project's .env work too."),
        "author": {"name": "Beat the Bush", "url": "https://beatthebush.co"},
        "icon": "icon.png",  # Puzzle's logo (puzzle.io app icon, 256x256)
        "repository": {"type": "git", "url": "https://github.com/aligmirza/PuzzleLeadScoring"},
        "server": {
            "type": "python",
            "entry_point": "server/main.py",
            "mcp_config": {
                "command": f"{folder}/.venv/bin/python",
                "args": ["${__dirname}/server/main.py"],
                "env": {"PUZZLE_PROJECT_DIR": folder, "PYTHONUTF8": "1",
                        **{env: f"${{user_config.{field}}}" for field, env, *_ in SETTINGS}},
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
            **{field: {"type": kind, "title": title, "description": desc, "required": False,
                       **({"sensitive": True} if secret else {}),
                       **({"min": LIMITS.get(field, [0])[0]} if kind == "number" else {}),
                       **({"max": LIMITS[field][1]} if field in LIMITS else {})}
               for field, _, title, desc, secret, kind in SETTINGS},
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
        z.write(SRC / "icon.png", "icon.png")
    print(f"Built {OUT.relative_to(ROOT)} ({len(m['tools'])} tools). Double-click it to install in Claude Desktop.")


if __name__ == "__main__":
    main()
