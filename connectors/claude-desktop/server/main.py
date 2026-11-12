"""Claude Desktop extension launcher: runs the puzzle-lead-scoring MCP server from the project folder the user
picked when installing the extension. Claude Desktop starts it with that folder's own Python (.venv), so all the
project's packages, keys (.env), lists and settings are used as they are."""
import os
import sys

project = os.environ.get("PUZZLE_PROJECT_DIR", "")
if not project or not os.path.isfile(os.path.join(project, "enrich", "__main__.py")):
    sys.stderr.write(f"puzzle-lead-scoring: '{project}' is not the PuzzleLeadScoring project folder. Open the extension's "
                     "settings in Claude Desktop and pick the folder that holds enrich/, config/ and .venv/.\n")
    sys.exit(1)
sys.path.insert(0, project)
os.chdir(project)

from enrich.integrations.mcp_server import main  # noqa: E402

main()
