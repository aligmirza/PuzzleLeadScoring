"""Single data point test mode (enrich/integrations/dp_test.py and its MCP tools).

    .venv/bin/python -m unittest discover -s tests -v

Offline: a synthetic company in a temporary lists folder, directories switched off, OpenAI and providers blocked.
"""
import asyncio
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from enrich.checks import datapoints
from enrich.checks.rules import SIGNALS
from enrich.core import shared
from enrich.core.store import Store
from enrich.engine import layers as L
from enrich.integrations import dp_test
from enrich.pipeline.common import run_rules
from enrich.sources import directories

DOMAIN = "examplesaas.com"
HOME = """<html><head><title>ExampleSaaS - invoicing software for startups</title></head><body>
<h1>ExampleSaaS</h1><p>ExampleSaaS is a B2B SaaS platform that helps startups send invoices. Start your free trial.</p>
<p>Plans from $49 per month. Sign up today.</p>
<footer>ExampleSaaS, Inc. 123 Market Street, San Francisco, CA 94105. &copy; 2026 ExampleSaaS, Inc.</footer>
</body></html>"""
ABOUT = """<html><body><h1>About us</h1><p>We are a team of 20 based in San Francisco, backed by Y Combinator and
seed investors.</p></body></html>"""

PRICING = """<html><body><h1>Pricing</h1><p>Starter $49 per month. Growth $199 per month. Start free trial.</p>
</body></html>"""


def _digest(folder: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*")):
        if p.is_file() and not p.name.endswith(("-wal", "-shm")):
            h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()


class DataPointTestMode(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.lists = self.tmp / "lists"
        patches = [mock.patch.object(dp_test, "LISTS", self.lists),
                   mock.patch.object(directories, "ENABLED", False),
                   mock.patch.object(shared, "put_ai", side_effect=AssertionError("test mode wrote to the shared cache")),
                   mock.patch("enrich.ai.runner.Runner", side_effect=AssertionError("test mode called OpenAI")),
                   mock.patch("enrich.providers.prospeo.company", side_effect=AssertionError("test mode called Prospeo"))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        src = self.lists / "source_list"
        store = Store(src)
        (src / "input.csv").write_text(f"Company Name,Domain,Employees\nExampleSaaS,{DOMAIN},20\n")
        store.save_page(DOMAIN, "home", HOME)
        store.save_page(DOMAIN, "about", ABOUT)
        store.save_page(DOMAIN, "pricing", PRICING)
        record = {"domain": DOMAIN, "status": "live", "error": None, "final_url": f"https://{DOMAIN}/",
                  "pages": {"home": f"https://{DOMAIN}/", "about": f"https://{DOMAIN}/about",
                            "pricing": f"https://{DOMAIN}/pricing"}, "headers": {},
                  "robots_blocked": [], "input": {"company": "ExampleSaaS", "employees": "20"}}
        store.save_site(DOMAIN, record)
        run_rules(store, {"domain": DOMAIN, "company": "ExampleSaaS", "employees": "20", "input": {}}, record, "icp")
        store.db.close()
        self.source = src
        self.before = _digest(src)
        dp_test.setup("source_list", "dp_test")

    def _sid_waiting_for_pages(self) -> str:
        """A phase 1 data point the free layers leave open and whose waterfall asks the page question next."""
        for sid, (group, _) in SIGNALS.items():
            if group in ("must", "exclusion") and datapoints.step(sid, "ai_pages").get("prompt"):
                rep = dp_test._report(DOMAIN, sid, "dp_test", include_page_text=False)
                if (rep["next_step"] or {}).get("answer_from") == "pages":
                    return sid
        self.skipTest("no phase 1 data point is waiting for a page answer on the synthetic company")

    def _first_two(self) -> tuple[str, str]:
        sids = [sid for sid, (g, _) in SIGNALS.items() if g in ("must", "exclusion")]
        return sids[0], sids[1]

    # 1 and 2 --------------------------------------------------------------------------------------------------------
    def test_only_the_selected_data_point_runs_and_every_stage_is_reported(self):
        a, _ = self._first_two()
        with mock.patch.object(dp_test, "_report", wraps=dp_test._report) as spy:
            rep = dp_test.run(DOMAIN, a, "dp_test")
        self.assertEqual([c.args[1] for c in spy.call_args_list], [a])
        self.assertEqual(rep["test_information"]["data_point_id"], a)
        self.assertEqual([s["stage"] for s in rep["waterfall"]], [layer.name for layer in L.ORDER])
        for st in rep["waterfall"]:
            for field in ("status", "executed", "fallback_reason", "cost"):
                self.assertIn(field, st)
        self.assertTrue(rep["waterfall"][0]["executed"])
        self.assertIn(rep["final_result"]["value"], ("Yes", "No", "Needs check", "Unknown"))
        self.assertEqual(rep["final_result"]["actual_api_cost_usd"], 0)
        self.assertEqual(list(dp_test._session(self.lists / "dp_test")["runs"]), [f"{DOMAIN}|{a}"])

    # 3 ------------------------------------------------------------------------------------------------------------
    def test_skipped_stages_and_reasons(self):
        a, _ = self._first_two()
        rep = dp_test.run(DOMAIN, a, "dp_test")
        directory, provider = rep["waterfall"][1], rep["waterfall"][4]
        self.assertFalse(directory["executed"])  # switched off for the test
        self.assertEqual(directory["source"], "not consulted")
        if a in L.DIRECTORY_COVERS:
            self.assertTrue(directory["status"].startswith("unavailable"))
            self.assertIn("switched off", directory["fallback_reason"])
        self.assertFalse(provider["executed"])
        self.assertTrue(any(r in provider["fallback_reason"] for r in ("Not run", "already decided", "cannot answer",
                                                                        "Switched off", "switched off")), provider["fallback_reason"])
        for st in rep["waterfall"][2:4]:
            if not st["executed"]:
                self.assertTrue(st["fallback_reason"])
                self.assertEqual(st["cost"]["actual_usd"], None)

    # 4 and 5 ------------------------------------------------------------------------------------------------------
    def test_pauses_for_review_and_needs_explicit_confirmation(self):
        a, b = self._first_two()
        dp_test.run(DOMAIN, a, "dp_test")
        blocked = dp_test.run(DOMAIN, b, "dp_test")
        self.assertTrue(blocked["paused"])
        self.assertFalse(blocked["executed"])
        reviewed = dp_test.review(action="approve", test_list="dp_test")
        self.assertIn("Ready to move to the next data point?", reviewed["next"])
        self.assertEqual(reviewed["progress"]["approved"], 1)
        still = dp_test.run(DOMAIN, b, "dp_test")  # no confirmation yet
        self.assertTrue(still["paused"])
        self.assertIn("Ready to move to the next data point?", still["why"])
        moved = dp_test.run(DOMAIN, b, "dp_test", confirm_next=True)
        self.assertEqual(moved["test_information"]["data_point_id"], b)
        self.assertEqual(moved["progress"]["current"]["data_point_id"], b)

    def test_investigate_stays_on_the_data_point(self):
        a, b = self._first_two()
        dp_test.run(DOMAIN, a, "dp_test")
        out = dp_test.review(action="investigate", notes="why unknown?", test_list="dp_test")
        self.assertIn("how_it_was_produced", out["investigation"])
        self.assertEqual(out["progress"]["state"], "awaiting_review")
        self.assertEqual(out["progress"]["needs_investigation"], [f"{DOMAIN}|{a}"])
        self.assertTrue(dp_test.run(DOMAIN, b, "dp_test", confirm_next=True)["paused"])

    def test_correct_needs_expected_value_and_is_tracked(self):
        a, _ = self._first_two()
        dp_test.run(DOMAIN, a, "dp_test")
        with self.assertRaises(ValueError):
            dp_test.review(action="correct", test_list="dp_test")
        out = dp_test.review(action="correct", expected_value="Yes", notes="the footer shows a US address",
                             test_list="dp_test")
        self.assertEqual(out["progress"]["corrected"], 1)
        self.assertEqual(out["progress"]["corrections"][f"{DOMAIN}|{a}"]["expected"], "Yes")
        self.assertNotIn(a, out["progress"]["remaining"])
        self.assertEqual(out["row"]["Manual: correct answer"], "Yes")

    def test_stop_then_restart_needs_confirmation(self):
        a, b = self._first_two()
        dp_test.run(DOMAIN, a, "dp_test")
        self.assertTrue(dp_test.review(action="stop", test_list="dp_test")["stopped"])
        self.assertTrue(dp_test.run(DOMAIN, b, "dp_test")["paused"])
        self.assertFalse(dp_test.run(DOMAIN, b, "dp_test", confirm_next=True).get("paused"))

    def test_reviewing_another_data_point_is_refused(self):
        a, b = self._first_two()
        dp_test.run(DOMAIN, a, "dp_test")
        with self.assertRaises(ValueError):
            dp_test.review(DOMAIN, b, "approve", test_list="dp_test")

    # 6 ------------------------------------------------------------------------------------------------------------
    def test_retry_reruns_only_the_current_data_point(self):
        sid = self._sid_waiting_for_pages()
        dp_test.run(DOMAIN, sid, "dp_test")
        dp_test.answer(DOMAIN, sid, {"answer_from": "pages", "answer": "unknown", "confidence": "low",
                                     "evidence_quote": "", "reason": "test"}, "dp_test")
        pid = datapoints.step(sid, "ai_pages")["prompt"]
        store = Store(self.lists / "dp_test")
        self.assertIn(pid, store.get_ai(DOMAIN, "icp"))
        with mock.patch.object(dp_test, "_report", wraps=dp_test._report) as spy:
            out = dp_test.run(DOMAIN, sid, "dp_test")  # same data point while awaiting review = retry
        self.assertEqual({c.args[1] for c in spy.call_args_list}, {sid})
        self.assertEqual(out["review"]["retry"], 1)
        self.assertNotIn(pid, store.get_ai(DOMAIN, "icp"))  # its test answers were cleared, so the AI stage asks again
        self.assertEqual(out["next_step"]["answer_from"], "pages")
        self.assertEqual(out["progress"]["retries"], 1)

    # 7 ------------------------------------------------------------------------------------------------------------
    def test_answers_stay_in_the_test_list(self):
        sid = self._sid_waiting_for_pages()
        dp_test.run(DOMAIN, sid, "dp_test")
        out = dp_test.answer(DOMAIN, sid, {"answer_from": "pages", "answer": "unknown", "confidence": "low",
                                           "evidence_quote": "", "reason": "test"}, "dp_test")
        self.assertIn("answer_saved", out)
        dp_test.review(action="reject", notes="test", test_list="dp_test")
        self.assertEqual(_digest(self.source), self.before)  # the source list is untouched
        self.assertEqual(Store(self.source).get_ai(DOMAIN, "icp"), {})
        shared.put_ai.assert_not_called()

    def test_production_list_names_are_refused(self):
        for call in (lambda: dp_test.run(DOMAIN, "M1", "source_list"),
                     lambda: dp_test.review(action="approve", test_list="mcp_test"),
                     lambda: dp_test.setup("source_list", "source_list")):
            with self.assertRaises(ValueError):
                call()

    def test_answer_only_for_the_data_point_under_test(self):
        a, b = self._first_two()
        dp_test.run(DOMAIN, a, "dp_test")
        with self.assertRaises(ValueError):
            dp_test.answer(DOMAIN, b, {"answer_from": "pages", "answer": "yes"}, "dp_test")


    def test_live_site_rules_out_e2(self):
        rep = dp_test._report(DOMAIN, "E2", "dp_test", include_page_text=False)
        self.assertEqual(rep["final_result"]["value"], "No")
        self.assertEqual(rep["final_result"]["decided_by"], "rules")
        self.assertIn("site is live", rep["final_result"]["evidence"])


    def test_e4_links_to_the_same_page_as_m1(self):
        m1 = dp_test._report(DOMAIN, "M1", "dp_test", include_page_text=False)["final_result"]
        e4 = dp_test._report(DOMAIN, "E4", "dp_test", include_page_text=False)["final_result"]
        self.assertEqual(m1["value"], "Yes")
        self.assertEqual(e4["value"], "No")
        self.assertTrue(e4["source"].startswith("https://"))
        self.assertEqual(e4["source"], m1["source"])


# rules fixed during the manual review -------------------------------------------------------------------------------
class ReviewFixes(unittest.TestCase):
    def test_backed_by_needs_a_named_investor(self):
        from enrich.checks.rules import RE_NAMED_INVESTOR as named
        self.assertFalse(named.search("Cyrus Jia Founder Backed by From data to insights at the speed of AI Terms"))
        self.assertFalse(named.search("Backed by the best. Contact us"))
        for s in ("We are backed by Crosslink Capital and Wireframe Ventures.", "Backed by Y Combinator",
                  "Our seed round was led by Sequoia", "Backed by Founders Fund, Accel and others"):
            self.assertTrue(named.search(s), s)


    def _f1_from_directory(self):
        return {"signals": {"F1": {"value": "yes", "method": "directory", "evidence": "Y Combinator Winter 2024",
                                   "url": "https://www.ycombinator.com/companies/x", "note": "stage not shown"}}}

    def test_confirmed_round_becomes_f1_proof(self):
        from enrich.ai.apply import _confirm_stage
        r = self._f1_from_directory()
        _confirm_stage(r, {"value": "Seed", "basis": "confirmed", "confidence": "high", "entity_match": "domain",
                           "evidence_quote": "raised $5.5 million in seed funding", "source": "https://example.com/pr"}, 2)
        self.assertEqual(r["signals"]["F1"]["value"], "yes")
        self.assertEqual(r["signals"]["F1"]["url"], "https://example.com/pr")
        self.assertIn("Seed (confirmed)", r["signals"]["F1"]["note"])

    def test_estimated_or_unquoted_stage_keeps_the_directory_proof(self):
        from enrich.ai.apply import _confirm_stage
        for data in ({"value": "Seed", "basis": "estimated", "confidence": "medium", "evidence_quote": "small team"},
                     {"value": "Seed", "basis": "confirmed", "confidence": "high", "evidence_quote": ""}):
            r = self._f1_from_directory()
            _confirm_stage(r, data, 1)
            self.assertEqual(r["signals"]["F1"]["method"], "directory")

    def test_confirmed_series_c_turns_f1_to_no_only_with_high_and_domain(self):
        from enrich.ai.apply import _confirm_stage
        weak, strong = self._f1_from_directory(), self._f1_from_directory()
        base = {"value": "Series C or later", "basis": "confirmed", "evidence_quote": "raised a $90M Series C"}
        _confirm_stage(weak, {**base, "confidence": "medium", "entity_match": "domain"}, 2)
        _confirm_stage(strong, {**base, "confidence": "high", "entity_match": "domain"}, 2)
        self.assertEqual(weak["signals"]["F1"]["value"], "yes")
        self.assertEqual(strong["signals"]["F1"]["value"], "no")

    def test_f1_stays_open_for_ai_until_the_stage_is_confirmed(self):
        from enrich.checks import datapoints as dps
        r = self._f1_from_directory()
        self.assertTrue(dps.still_open("F1", r))
        r["funding_stage"] = "Seed (confirmed)"
        self.assertFalse(dps.still_open("F1", r))
        self.assertFalse(dps.still_open("M1", {"signals": {"M1": {"value": "yes"}}}))


# 8 ----------------------------------------------------------------------------------------------------------------
class ExistingTools(unittest.TestCase):
    def test_tools_unchanged_and_no_new_ones(self):
        from enrich.integrations import mcp_server
        names = [t.name for t in asyncio.run(mcp_server.mcp.list_tools())]
        self.assertEqual(sorted(names), sorted([
            "check_company", "enrich_list", "list_status", "list_results", "explain_company", "clay_tables",
            "pull_clay_table", "push_list_to_clay", "open_questions", "submit_answers", "questions_overview",
            "dp_test_setup", "dp_test_run", "dp_test_answer", "dp_test_record", "dp_test_summary"]))

    def test_production_waterfall_still_imports_and_runs_offline(self):
        from enrich.integrations import client_ai, service
        self.assertTrue(callable(client_ai.submit_answers) and callable(service.check_one))  # production paths load
        from enrich.pipeline import waterfall
        self.assertEqual(len(waterfall.LAYERS), 5)
        self.assertEqual(len(SIGNALS), 57)
        self.assertEqual(len(datapoints.load()), 57)


if __name__ == "__main__":
    unittest.main()
