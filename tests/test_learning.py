"""The learning loop (enrich/core/learning.py): events in, proposals out, safe changes applied, the rest approved.

    .venv/bin/python -m unittest discover -s tests -v

Offline, in a temp folder: the real config, event log and changelog are never touched.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from enrich.checks import datapoints as D
from enrich.core import learning as L


class Loop(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = self.tmp / "f18.json"
        self.cfg.write_text(json.dumps({"id": "F18", "steps": {"ai_web": {"run": "if_open"}}}))
        patches = [mock.patch.object(L, "DIR", self.tmp), mock.patch.object(L, "EVENTS", self.tmp / "events.jsonl"),
                   mock.patch.object(L, "STATE", self.tmp / "proposals.json"),
                   mock.patch.object(L, "CHANGELOG", self.tmp / "IMPROVEMENTS.md"),
                   mock.patch.object(L, "_tests", return_value=(True, "OK")),
                   mock.patch.object(D, "path", lambda sid, cfg=None: self.cfg)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(D.load.cache_clear)

    def _web_unknowns(self, n, sid="F18"):
        for i in range(n):
            L.record("answer", sid=sid, prompt="x", step="web", outcome="unknown", domain=f"c{i}.com")

    def test_never_raises(self):
        with mock.patch.object(L, "EVENTS", Path("/nonexistent/dir/events.jsonl")):
            L.record("answer", sid="F18")  # no error

    def test_crash_becomes_an_auto_code_fix(self):
        try:
            {}["x"]
        except KeyError as e:
            L.error("mcp submit_answers", e, domain="a.com")
        p = L.report(auto_apply=False)["proposals"]
        self.assertEqual((p[0]["kind"], p[0]["safety"]), ("code_fix", "auto"))
        self.assertIn("KeyError", p[0]["title"])

    def test_web_that_never_finds_is_switched_to_on_clue_automatically(self):
        self._web_unknowns(L.MIN_SAMPLES - 1)
        self.assertEqual(L.report(auto_apply=True)["proposals"], [])  # too few answers yet
        self._web_unknowns(1)
        r = L.report(auto_apply=True)
        self.assertEqual(r["applied_automatically"][0]["data_point"], "F18")
        self.assertEqual(json.loads(self.cfg.read_text())["steps"]["ai_web"]["run"], "if_clue")
        self.assertIn("applied", (self.tmp / "IMPROVEMENTS.md").read_text())
        D.load.cache_clear()
        self.assertEqual(L.report(auto_apply=True)["proposals"], [])  # decided: gone until it happens again

    def test_failed_tests_revert_the_change(self):
        self._web_unknowns(L.MIN_SAMPLES)
        before = self.cfg.read_text()
        with mock.patch.object(L, "_tests", return_value=(False, "FAILED (failures=1)")):
            r = L.report(auto_apply=True)
        self.assertFalse(r["applied_automatically"][0]["applied"])
        self.assertEqual(self.cfg.read_text(), before)

    def test_phase_1_settings_are_never_changed_automatically(self):
        self._web_unknowns(20, sid="E3")
        self.assertEqual(L.report(auto_apply=True)["proposals"], [])

    def test_auto_can_be_switched_off(self):
        self._web_unknowns(L.MIN_SAMPLES)
        with mock.patch.dict("os.environ", {"LEARNING_AUTO_APPLY": "0"}):
            r = L.report()
        self.assertEqual(r["applied_automatically"], [])
        self.assertEqual(r["proposals"][0]["status"], "open")

    def test_repeated_rejections_need_approval(self):
        for i in range(L.REJECTED_MIN):
            L.record("answer", sid="B4", step="web", outcome="rejected", why="entity match name only", domain=f"c{i}.com")
        p = L.report(auto_apply=True)["proposals"][0]
        self.assertEqual((p["kind"], p["safety"]), ("prompt", "approve"))
        self.assertIn("wrong company or domain", p["title"])
        self.assertFalse(L.apply(p["id"])["applied"])  # made by editing the prompt, then mark_improvement

    def test_rejected_proposal_comes_back_only_when_it_happens_again(self):
        L.record("correction", sid="F16", domain="a.com", got="no", expected="yes", notes="hybrid")
        pid = L.report()["proposals"][0]["id"]
        L.mark(pid, "rejected", "user said no")
        self.assertEqual(L.report()["proposals"], [])
        with mock.patch("time.strftime", return_value="2999-01-01 00:00:00"):
            L.record("correction", sid="F16", domain="b.com", got="no", expected="yes")
        self.assertEqual(L.report()["proposals"][0]["status"], "open (seen again)")

    def test_answer_outcomes(self):
        result = {"signals": {"F5": {"value": "yes", "method": "ai"}, "F6": {"value": "unknown", "note": "low confidence"},
                              "F10": {"value": "unknown", "note": ""}}}
        answers = {"fit/startup_selling_nationally": {1: {"answer": {"answer": "yes"}}},
                   "fit/b2b_saas_or_tech_service": {2: {"answer": {"answer": "yes"}}},
                   "fit/ai_core_to_product": {1: {"answer": {"answer": "unknown"}}}}
        L.outcomes(result, [("fit/startup_selling_nationally", 1), ("fit/b2b_saas_or_tech_service", 2),
                            ("fit/ai_core_to_product", 1)], answers, "mcp", "lst", "a.com")
        got = {e["sid"]: e["outcome"] for e in L._events()}
        self.assertEqual(got, {"F5": "counted", "F6": "rejected", "F10": "unknown"})


if __name__ == "__main__":
    unittest.main()
