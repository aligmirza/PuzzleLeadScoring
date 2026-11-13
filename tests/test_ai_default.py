"""AI on by default in production (the API, `run`, `pull`), never through MCP, and skipped with a note (not a failure)
when there is no OpenAI key or the cost cap is reached.

    .venv/bin/python -m unittest discover -s tests -v

Nothing is sent to OpenAI: the AI step is replaced by a fake.
"""
import asyncio
import os
import unittest
from unittest import mock

from enrich.integrations import api, service


class ApiDefaults(unittest.TestCase):
    def test_ai_is_on_unless_the_caller_says_false(self):
        self.assertTrue(api.CheckIn(domain="acme.com").ai)
        self.assertTrue(api.ListIn(rows=[{"Domain": "acme.com"}]).ai)
        self.assertTrue(api.PullIn(table_id="t_1").ai)
        self.assertTrue(api.BatchIn(companies=[{"domain": "acme.com"}]).ai)
        self.assertFalse(api.CheckIn(domain="acme.com", ai=False).ai)


class SkipWithANote(unittest.TestCase):
    def run_skip(self, **env):
        with mock.patch.dict(os.environ, env):
            return asyncio.run(service._ai_or_skip(mock.Mock(), ["acme.com"], "icp", service.ai_options(None)))

    def test_no_openai_key_skips_and_says_why(self):
        with mock.patch.object(service, "_ai", mock.AsyncMock()) as ai:
            out = self.run_skip(OPENAI_API_KEY="")
        ai.assert_not_called()
        self.assertIn("OPENAI_API_KEY is not set", out["skipped"])

    def test_over_the_cost_cap_skips_instead_of_failing(self):
        with mock.patch.object(service, "_ai", mock.AsyncMock(side_effect=service.CostLimit("could cost up to $9"))):
            out = self.run_skip(OPENAI_API_KEY="sk-test")
        self.assertEqual(out, {"skipped": "could cost up to $9"})

    def test_with_a_key_the_ai_step_runs(self):
        with mock.patch.object(service, "_ai", mock.AsyncMock(return_value={"companies": 1, "cost_usd": 0.01})):
            self.assertEqual(self.run_skip(OPENAI_API_KEY="sk-test")["companies"], 1)


class Commands(unittest.TestCase):
    def test_run_and_pull_have_no_ai_flags(self):
        import enrich.__main__ as cli
        src = open(cli.__file__, encoding="utf-8").read()
        self.assertIn('"--no-ai"', src)
        self.assertIn("use_ai=not args.no_ai", src)
        self.assertIn('"--yes-ai"', src)


if __name__ == "__main__":
    unittest.main()
