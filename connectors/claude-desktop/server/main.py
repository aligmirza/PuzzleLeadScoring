"""Claude Desktop extension launcher: runs the puzzle-lead-scoring MCP server from the project folder the user
picked when installing the extension. Claude Desktop starts it with that folder's own Python (.venv), so all the
project's packages, keys (.env), lists and settings are used as they are.

Keys entered in the extension's settings (Configure) arrive as environment variables and win over .env. A field left
blank arrives empty (or as the unfilled placeholder); it is dropped here, so the .env value is used instead."""
import os
import sys

SETTINGS = ("LEADMAGIC_API_KEY", "LEADMAGIC_MONTHLY_CREDITS", "LEADMAGIC_PEOPLE_LIMIT", "CLAY_WEBHOOK_URL",
            "CLAY_CONTACTS_WEBHOOK_URL", "CLAY_WEBHOOK_TOKEN", "OPENAI_API_KEY", "AI_MONTHLY_BUDGET_USD",
            "API_AI_MAX_COST_USD", "PROSPEO_API_KEY", "PROVIDER_MONTHLY_CREDITS", "CONTACTS_PROVIDER", "SEC_CONTACT_EMAIL")


def drop_blank_settings(env=os.environ) -> list[str]:
    """Remove settings the person left blank, so they don't hide the keys in .env. Returns the ones that are set."""
    kept = []
    for key in SETTINGS:
        value = (env.get(key) or "").strip()
        if not value or value.startswith("${user_config."):
            env.pop(key, None)
        else:
            kept.append(key)
    return kept


def run() -> None:
    project = os.environ.get("PUZZLE_PROJECT_DIR", "")
    if not project or not os.path.isfile(os.path.join(project, "enrich", "__main__.py")):
        sys.stderr.write(f"puzzle-lead-scoring: '{project}' is not the PuzzleLeadScoring project folder. Open the "
                         "extension's settings in Claude Desktop and pick the folder that holds enrich/, config/ and .venv/.\n")
        sys.exit(1)
    kept = drop_blank_settings()
    if kept:
        sys.stderr.write(f"puzzle-lead-scoring: using {', '.join(kept)} from the extension's settings\n")  # names only
    sys.path.insert(0, project)
    os.chdir(project)
    from enrich.integrations.mcp_server import main
    main()


if __name__ == "__main__":
    run()
