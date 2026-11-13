"""Prospeo and Clay through MCP (enrich/integrations/mcp_server.py): a plan first, credits only with an approved cap.

    .venv/bin/python -m unittest discover -s tests -v

Temp lists only; Prospeo and Clay are mocked, so nothing is bought or sent.
"""
import asyncio
import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from enrich.integrations import mcp_server
from enrich.providers import run as prun

PLAN = {"domains": ["a.com", "b.com"], "credits_at_most": 2, "covers": ["Employees"]}


class ProspeoThroughMCP(unittest.TestCase):
    def setUp(self):
        self.lists = Path(tempfile.mkdtemp())
        (self.lists / "lst").mkdir()
        (self.lists / "lst" / "input.csv").write_text("Domain\na.com\nb.com\n")
        for p in (mock.patch.dict(os.environ, {"CONTACTS_PROVIDER": "prospeo"}),
                  mock.patch("enrich.pipeline.common.LISTS", self.lists),
                  mock.patch.object(prun, "status", return_value={"key_set": True, "credits_left": 100}),
                  mock.patch.object(prun, "fetch_one", side_effect=AssertionError("spent a Prospeo credit"))):
            p.start()
            self.addCleanup(p.stop)

    def test_without_a_cap_it_only_plans(self):
        with mock.patch.object(prun, "company_plan", return_value=PLAN):
            r = asyncio.run(mcp_server.provider_companies("lst"))
        self.assertTrue(r["estimate_only"])
        self.assertEqual(r["credits_at_most"], 2)
        with mock.patch.object(prun, "people_plan", return_value={**PLAN, "already_have_prospeo": []}):
            self.assertTrue(asyncio.run(mcp_server.find_contacts("lst", emails=True))["estimate_only"])

    def test_a_cap_below_the_plan_is_refused(self):
        with mock.patch.object(prun, "company_plan", return_value=PLAN), \
                mock.patch.object(prun.prospeo, "enabled", return_value=True), \
                mock.patch.object(prun, "load_leads", return_value=([], {})):
            r = asyncio.run(mcp_server.provider_companies("lst", max_credits=1))
        self.assertIn("above max_credits", r["refused"])

    def test_no_key_is_an_error_not_an_exit(self):
        with mock.patch.object(prun, "company_plan", return_value=PLAN), \
                mock.patch.object(prun.prospeo, "enabled", return_value=False):
            with self.assertRaises(ValueError):
                asyncio.run(mcp_server.provider_companies("lst", max_credits=5))

    def test_unknown_list_is_an_error(self):
        with self.assertRaises(ValueError):
            asyncio.run(mcp_server.provider_companies("nope"))

    def test_spending_tools_default_to_a_plan(self):
        tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
        for name in ("provider_companies", "find_contacts"):
            self.assertEqual(tools[name].input_schema["properties"]["max_credits"].get("default"), 0)
        self.assertIs(tools["find_contacts"].input_schema["properties"]["emails"].get("default"), False)


class ContactsToClay(unittest.TestCase):
    ROWS = [{"Domain": "a.com", "Name": "Ann", "Title": "CEO", "Decision maker": "yes", "Email": ""},
            {"Domain": "a.com", "Name": "Bo", "Title": "Engineer", "Decision maker": "", "Email": ""}]

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "lst").mkdir()
        (self.dir / "lst" / "input.csv").write_text("Domain\na.com\n")
        path = self.dir / "contacts.csv"
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(self.ROWS[0]))
            w.writeheader()
            w.writerows(self.ROWS)
        for p in (mock.patch("enrich.pipeline.common.LISTS", self.dir),
                  mock.patch("enrich.integrations.service.LISTS", self.dir),
                  mock.patch.object(prun, "write_contacts", return_value=path)):
            p.start()
            self.addCleanup(p.stop)

    def test_list_contacts_reads_what_was_bought(self):
        self.assertEqual(mcp_server.list_contacts("lst")["count"], 2)
        self.assertEqual(mcp_server.list_contacts("lst", decision_makers_only=True)["rows"][0]["Name"], "Ann")

    def test_contacts_need_their_own_webhook(self):
        with mock.patch.dict("os.environ", {"CLAY_WEBHOOK_URL": "https://companies", "CLAY_CONTACTS_WEBHOOK_URL": ""}):
            with self.assertRaises(RuntimeError):
                asyncio.run(mcp_server.push_contacts_to_clay("lst"))

    def test_push_sends_one_row_per_person(self):
        sent = mock.AsyncMock(return_value={"sent": 1, "failed": 0, "errors": []})
        with mock.patch("enrich.integrations.clay.push_rows", sent):
            asyncio.run(mcp_server.push_contacts_to_clay("lst", webhook_url="https://contacts", decision_makers_only=True))
        rows, url = sent.call_args.args
        self.assertEqual(([r["Name"] for r in rows], url), (["Ann"], "https://contacts"))


if __name__ == "__main__":
    unittest.main()
