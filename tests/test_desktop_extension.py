"""Claude Desktop extension (connectors/): the settings form in its manifest and the launcher that reads it.

    .venv/bin/python -m unittest discover -s tests -v
"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_mcpb  # noqa: E402


def launcher():
    spec = importlib.util.spec_from_file_location("lps_launcher", ROOT / "connectors" / "claude-desktop" / "server" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # defines functions only: the server starts under __main__
    return mod


class SettingsForm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = build_mcpb.manifest()

    def test_every_field_reaches_the_server_as_its_env_var(self):
        env, form = self.m["server"]["mcp_config"]["env"], self.m["user_config"]
        for field, var, *_ in build_mcpb.SETTINGS:
            self.assertEqual(env[var], f"${{user_config.{field}}}")
            self.assertFalse(form[field]["required"])  # never blocks the install
        self.assertTrue(form["project_dir"]["required"])

    def test_keys_are_secret_caps_are_numbers(self):
        form = self.m["user_config"]
        for field in ("leadmagic_api_key", "openai_api_key", "clay_webhook_url", "clay_contacts_webhook_url", "clay_webhook_token"):
            self.assertTrue(form[field].get("sensitive"), field)
        for field in ("leadmagic_monthly_credits", "ai_monthly_budget_usd"):
            self.assertEqual(form[field]["type"], "number")
            self.assertNotIn("sensitive", form[field])

    def test_launcher_knows_every_setting(self):
        self.assertEqual(set(launcher().SETTINGS), {var for _, var, *_ in build_mcpb.SETTINGS})


class Launcher(unittest.TestCase):
    def test_blank_fields_are_dropped_so_env_file_keys_still_work(self):
        env = {"LEADMAGIC_API_KEY": "lm_from_form", "OPENAI_API_KEY": "", "CLAY_WEBHOOK_URL": "  ",
               "CLAY_WEBHOOK_TOKEN": "${user_config.clay_webhook_token}", "OTHER": "kept"}
        kept = launcher().drop_blank_settings(env)
        self.assertEqual(kept, ["LEADMAGIC_API_KEY"])
        self.assertEqual(env, {"LEADMAGIC_API_KEY": "lm_from_form", "OTHER": "kept"})

    def test_form_value_wins_over_env_file(self):
        import os
        import tempfile
        from unittest import mock
        from enrich.core import settings
        f = Path(tempfile.mkdtemp()) / ".env"
        f.write_text("LEADMAGIC_API_KEY=lm_from_file\nCLAY_WEBHOOK_URL=https://from-file\n", encoding="utf-8")
        with mock.patch.object(settings, "ENV_FILE", f), \
                mock.patch.dict(os.environ, {"LEADMAGIC_API_KEY": "lm_from_form"}, clear=False):
            os.environ.pop("CLAY_WEBHOOK_URL", None)
            settings.load_env()
            self.assertEqual(os.environ["LEADMAGIC_API_KEY"], "lm_from_form")
            self.assertEqual(os.environ["CLAY_WEBHOOK_URL"], "https://from-file")


if __name__ == "__main__":
    unittest.main()
