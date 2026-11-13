"""The hosted connector's /settings page (enrich/integrations/settings_page.py): admin sign-in, keys never shown,
values checked, saved to .env and to the running server.

    .venv/bin/python -m unittest discover -s tests -v

Uses a temporary .env; the real one is never touched.
"""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from enrich.core import settings
from enrich.integrations import settings_page

BASE = "http://localhost"
VARS = [v for v, *_ in settings.EDITABLE]


class SettingsPage(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        env = self.dir / ".env"
        env.write_text("LEADMAGIC_API_KEY=lm_old_secret_value\nOTHER=keep\n", encoding="utf-8")
        clean = {v: "" for v in VARS}
        for p in (mock.patch.object(settings, "ENV_FILE", env), mock.patch.object(settings, "EXAMPLE_FILE", self.dir / ".env.example"),
                  mock.patch.dict(os.environ, {**clean, "LEADMAGIC_API_KEY": "lm_old_secret_value"})):
            p.start()
            self.addCleanup(p.stop)
        self.page = settings_page.Page(BASE, "admin-pass")
        app = Starlette(routes=[Route("/settings", self.page.get, methods=["GET"]),
                                Route("/settings", self.page.save, methods=["POST"]),
                                Route("/settings/login", self.page.login, methods=["POST"]),
                                Route("/settings/logout", self.page.logout, methods=["POST"])])
        self.c = TestClient(app, base_url=BASE)
        self.sleep = mock.patch.object(settings_page.anyio, "sleep", mock.AsyncMock())
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def _sign_in(self, pw="admin-pass"):
        return self.c.post("/settings/login", data={"password": pw}, follow_redirects=False)

    def _csrf(self):
        return re.search(r'name="csrf" value="([^"]+)"', self.c.get("/settings").text).group(1)

    def test_needs_the_admin_password(self):
        self.assertIn("Admin password", self.c.get("/settings").text)
        self.assertEqual(self._sign_in("connector-password").status_code, 401)
        r = self.c.post("/settings", data={"LEADMAGIC_API_KEY": "lm_new"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(os.environ["LEADMAGIC_API_KEY"], "lm_old_secret_value")

    def test_keys_are_never_shown_back(self):
        self.assertEqual(self._sign_in().status_code, 303)
        page = self.c.get("/settings").text
        self.assertNotIn("lm_old_secret_value", page)
        self.assertIn('LeadMagic API key <span class="opt">(optional)</span>: <span class="set">set', page)
        self.assertIn('OpenAI API key, big lists only <span class="opt">(optional)</span>: <span class="unset">not set', page)

    def test_every_box_is_optional_with_an_example(self):
        self._sign_in()
        page = self.c.get("/settings").text
        for var, title, *_ in settings.EDITABLE:
            self.assertIn(f'{title} <span class="opt">(optional)</span>', page.replace("&#x27;", "'"))
        self.assertIn('placeholder="Optional, e.g. sk-..."', page)
        self.assertIn('placeholder="Optional, e.g. https://api.clay.com/v3/sources/webhook/..."', page)
        self.assertIn('placeholder="Optional. Paste a new value to replace it"', page)  # LeadMagic is set
        r = self.c.post("/settings", data={"csrf": self._csrf()}, follow_redirects=False)  # all empty: fine, nothing changes
        self.assertEqual(r.status_code, 303)
        self.assertEqual(os.environ["LEADMAGIC_API_KEY"], "lm_old_secret_value")
        self.assertEqual(self.c.get("/settings").headers["cache-control"], "no-store")

    def test_save_replace_keep_and_remove(self):
        self._sign_in()
        r = self.c.post("/settings", data={"csrf": self._csrf(), "OPENAI_API_KEY": "sk-test123", "LEADMAGIC_MONTHLY_CREDITS": "500",
                                           "CONTACTS_PROVIDER": "LeadMagic", "LEADMAGIC_PEOPLE_LIMIT": "10",
                                           "SEC_CONTACT_EMAIL": "ops@puzzle.io", "PROSPEO_API_KEY": "pk_test",
                                           "CLAY_WEBHOOK_URL": "https://api.clay.com/v3/sources/webhook/abc"},
                        follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-test123")  # live in the running server
        text = settings.ENV_FILE.read_text(encoding="utf-8")
        for line in ("OPENAI_API_KEY=sk-test123", "LEADMAGIC_MONTHLY_CREDITS=500", "CONTACTS_PROVIDER=leadmagic",
                     "LEADMAGIC_PEOPLE_LIMIT=10", "SEC_CONTACT_EMAIL=ops@puzzle.io", "PROSPEO_API_KEY=pk_test", "LEADMAGIC_API_KEY=lm_old_secret_value", "OTHER=keep"):
            self.assertIn(line, text)  # empty box kept the LeadMagic key; unknown keys kept
        self.c.post("/settings", data={"csrf": self._csrf(), "remove_OPENAI_API_KEY": "on"})
        self.assertNotIn("OPENAI_API_KEY", os.environ)
        self.assertNotIn("OPENAI_API_KEY=sk", settings.ENV_FILE.read_text(encoding="utf-8"))

    def test_bad_values_save_nothing(self):
        self._sign_in()
        csrf = self._csrf()
        for field, value in (("LEADMAGIC_API_KEY", "lm_x\nPUZZLE_API_KEY=stolen"), ("CLAY_WEBHOOK_URL", "http://not-https"),
                             ("LEADMAGIC_MONTHLY_CREDITS", "lots"), ("OPENAI_API_KEY", "sk with spaces"),
                             ("LEADMAGIC_PEOPLE_LIMIT", "80"), ("CONTACTS_PROVIDER", "apollo"),
                             ("SEC_CONTACT_EMAIL", "not-an-email")):
            r = self.c.post("/settings", data={"csrf": csrf, field: value, "AI_MONTHLY_BUDGET_USD": "20"})
            self.assertEqual(r.status_code, 400, field)
        text = settings.ENV_FILE.read_text(encoding="utf-8")
        self.assertNotIn("stolen", text)
        self.assertNotIn("AI_MONTHLY_BUDGET_USD=20", text)  # one bad box: nothing saved

    def test_forms_need_the_session_token_and_this_site(self):
        self._sign_in()
        csrf = self._csrf()
        self.assertEqual(self.c.post("/settings", data={"csrf": "forged", "OPENAI_API_KEY": "sk-x"}).status_code, 401)
        r = self.c.post("/settings", data={"csrf": csrf, "OPENAI_API_KEY": "sk-x"}, headers={"origin": "https://evil.example"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(os.environ.get("OPENAI_API_KEY"), "")

    def test_lockout_after_five_wrong_passwords(self):
        for _ in range(5):
            self._sign_in("guess")
        self.assertEqual(self._sign_in().status_code, 429)  # even the right one, for 10 minutes

    def test_sign_out_ends_the_session(self):
        self._sign_in()
        self.c.post("/settings/logout", data={"csrf": self._csrf()})
        self.assertIn("Admin password", self.c.get("/settings").text)

    def test_plain_http_only_on_this_computer(self):
        page = settings_page.Page("http://example.com", "pw")
        req = mock.Mock(client=mock.Mock(host="203.0.113.9"))
        self.assertFalse(page.allowed_transport(req))
        self.assertTrue(settings_page.Page("https://x.example", "pw").allowed_transport(req))


class OnTheHostedConnector(unittest.TestCase):
    def test_routes_are_added(self):
        from enrich.integrations.mcp_server import mcp
        before = list(mcp._custom_starlette_routes)
        try:
            settings_page.enable(mcp, BASE, "pw")
            paths = {(r.path, tuple(sorted(r.methods or []))) for r in mcp._custom_starlette_routes}
            self.assertIn(("/settings/login", ("POST",)), paths)
            self.assertTrue(any(p == "/settings" for p, _ in paths))
        finally:
            mcp._custom_starlette_routes[:] = before


if __name__ == "__main__":
    unittest.main()
