"""Conditional runs between data points (config/datapoints run_if): parents first, children skipped when ruled out.

    .venv/bin/python -m unittest discover -s tests -v
"""
import unittest
from unittest import mock

from enrich.checks import datapoints as D
from enrich.checks.rules import SIGNALS

Q = {sid: D.step(sid, "ai_pages")["prompt"] for sid in ("M1", "M2", "M3", "M4", "E1", "F1", "F12")}


def signals(**values):
    s = {sid: {"value": "unknown", "evidence": "", "url": "", "note": "", "method": "code"} for sid in SIGNALS}
    for sid, v in values.items():
        s[sid]["value"] = v
    return s


def result(sigs, prompts):
    return {"signals": sigs, "facts": {"segment": "MM"}, "crawl": {"status": "live"}, "ai": {"prompts": prompts}}


class Gate(unittest.TestCase):
    def test_settings_come_from_the_files(self):
        self.assertEqual(D.run_if("M2"), {"M1": ["yes", "unknown"]})
        self.assertEqual(list(D.run_if("M4")), ["M1", "M2", "M3"])
        self.assertEqual(list(D.run_if("E1")), ["M1", "M2", "M3", "M4"])
        self.assertEqual(D.run_if("F12"), {"F1": ["yes"]})
        self.assertEqual(D.run_if("M1"), {})

    def test_run_wait_skip(self):
        self.assertEqual(D.gate("F12", signals(F1="yes"))[0], "run")
        self.assertEqual(D.gate("F12", signals(F1="no")), ("skip", "F1 (Venture-backed, pre-seed to Series B) is No"))
        self.assertEqual(D.gate("F12", signals(), waiting={"F1"})[0], "wait")  # F1 still has a question
        self.assertEqual(D.gate("F12", signals(), waiting=set())[0], "skip")   # F1 ended Unknown: F12 needs Yes
        self.assertEqual(D.gate("F12", signals())[0], "wait")                  # not known yet: never skips
        self.assertEqual(D.gate("M2", signals(), waiting=set())[0], "run")     # a must-have goes on after Unknown


class Questions(unittest.TestCase):
    def _store(self, done=None):
        store = mock.MagicMock()
        store.get_ai.return_value = done or {}
        return store

    def test_must_haves_one_after_another(self):
        from enrich.integrations import client_ai
        r = result(signals(), [Q["M1"], Q["M2"], Q["M3"], Q["E1"]])
        self.assertEqual([p for p, _ in client_ai._pending(self._store(), "x.com", "icp", r)], [Q["M1"]])
        r["signals"]["M1"]["value"] = "yes"
        self.assertEqual([p for p, _ in client_ai._pending(self._store(), "x.com", "icp", r)], [Q["M2"]])

    def test_test_mode_asks_everything(self):
        from enrich.integrations import client_ai
        r = result(signals(), [Q["M1"], Q["M2"]])
        self.assertEqual(len(client_ai._pending(self._store(), "x.com", "icp", r, all_questions=True)), 2)

    def test_child_waits_then_runs(self):
        from enrich.integrations import client_ai
        r = {**result(signals(), [Q["F1"], Q["F12"]]), "facts": {}}  # size unknown: the tier never locks
        self.assertEqual([p for p, _ in client_ai._pending(self._store(), "x.com", "signals", r, all_questions=False)],
                         [Q["F1"]])
        r["signals"]["F1"]["value"] = "yes"
        self.assertIn(Q["F12"], [p for p, _ in client_ai._pending(self._store(), "x.com", "signals", r)])


class NotApplicable(unittest.TestCase):
    def test_failed_must_have_closes_the_rest_of_phase_1(self):
        from enrich.engine.merge import gates
        r = result(signals(M1="no", M2="yes"), [])
        gates(r)
        s = r["signals"]
        self.assertEqual((s["M2"]["value"], s["M3"]["value"], s["E1"]["value"]), ("yes", "na", "na"))  # proof kept
        self.assertIn("ICP is already NO: M1", s["M3"]["note"])
        self.assertEqual(s["F12"]["value"], "unknown")  # phase 2 isn't touched by the ICP stop

    def test_parent_no_closes_the_child_and_its_children(self):
        from enrich.engine.merge import gates
        r = result(signals(F1="no"), [])
        gates(r)
        self.assertEqual(r["signals"]["F12"]["value"], "na")
        self.assertEqual(r["signals"]["F12"]["method"], "skipped")

    def test_found_proof_stays(self):
        from enrich.engine.merge import gates
        r = result(signals(F1="no", F12="yes"), [])
        gates(r)
        self.assertEqual(r["signals"]["F12"]["value"], "yes")

    def test_final_unknown_parent_closes_only_with_waiting_known(self):
        from enrich.engine.merge import gates
        r = result(signals(), [])
        gates(r)
        self.assertEqual(r["signals"]["F12"]["value"], "unknown")
        gates(r, waiting=set())
        self.assertEqual(r["signals"]["F12"]["value"], "na")

    def test_not_applicable_scores_nothing_and_is_not_asked(self):
        from enrich.checks.scoring import score
        from enrich.engine.layers import RULES, decided_by
        sig = {"value": "na", "evidence": "", "url": "", "note": "Not applicable: x", "method": "skipped"}
        self.assertFalse(D.still_open("F12", {"signals": {"F12": sig}}))
        self.assertEqual(score({"F12": sig}, "MM")["score"], 0)
        self.assertIs(decided_by(sig), RULES)


class MoreLinks(unittest.TestCase):
    def test_buying_signals_skipped_when_the_parent_is_no(self):
        from enrich.engine.merge import gates
        r = result(signals(F1="no", F8="no", F4="no"), [])
        gates(r)
        self.assertEqual([r["signals"][x]["value"] for x in ("B1", "B2", "B13", "B12", "B11")], ["na"] * 5)
        r = result(signals(), [])
        gates(r, waiting=set())  # parents ended Unknown: the children still run
        self.assertEqual([r["signals"][x]["value"] for x in ("B1", "B12", "B11")], ["unknown"] * 3)

    def test_opposites_settle_for_free(self):
        from enrich.engine.resolve import derive
        r = result(signals(F5="yes", M4="yes", F3="yes", F13="yes", M3="yes", E6="yes"), [])
        derive(r)
        s = r["signals"]
        self.assertEqual({x: s[x]["value"] for x in ("W1", "W3", "F7", "W5", "W4", "W10", "W12", "W9")},
                         dict.fromkeys(("W1", "W3", "F7", "W5", "W4", "W10", "W12", "W9"), "no"))
        self.assertEqual(s["W2"]["value"], "unknown")  # not an opposite: a national services firm can bill hours

    def test_opposite_waits_for_its_parent(self):
        self.assertEqual(D.gate("W12", signals(), waiting={"M3"})[0], "wait")
        self.assertEqual(D.gate("W12", signals(), waiting=set())[0], "run")
        self.assertEqual(D.gate("W12", signals(M3="no"), waiting={"M3"})[0], "run")


class WebSourcesAsPlainLinks(unittest.TestCase):
    def test_a_list_of_urls_is_accepted(self):
        from enrich.ai import apply as ai_apply
        r = result(signals(M1="yes", M2="yes", M3="yes", M4="yes"), [])
        r.update(company="Argon AI", domain="argon-ai.com")
        answer = {"answer": "no", "confidence": "high", "evidence_quote": "Argon AI in its $5.5 million Seed funding",
                  "source": "https://law.example/argon", "reason": "A seed round.", "entity_match": "domain",
                  "sources": ["https://law.example/argon"]}
        out = ai_apply.apply(r, {"exclusions/series_c_or_public": {2: {"answer": answer}}}, "icp")
        self.assertEqual((out["signals"]["E1"]["value"], out["signals"]["E1"]["url"]), ("no", "https://law.example/argon"))


if __name__ == "__main__":
    unittest.main()
