"""ICP pipelines (core/icp.py): the same facts, counted by the pipeline that runs.

    .venv/bin/python -m unittest discover -s tests -v

Uses temporary pipelines next to the real ones' config; nothing under config/ is changed.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from enrich.core import icp
from enrich.core.paths import CONFIG


class Pipelines(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        shutil.copytree(CONFIG / "icps" / "product", self.dir / "t_product")
        shutil.copytree(CONFIG / "icps" / "product", self.dir / "t_flip")
        roles = json.loads((self.dir / "t_flip" / "roles.json").read_text())
        roles["groups"]["F17"] = "exclusion"  # several entities: a fit signal for product, a hard stop here
        roles["run_if"] = {}
        roles["opposites"] = {}
        (self.dir / "t_flip" / "roles.json").write_text(json.dumps(roles))
        scoring = json.loads((self.dir / "t_flip" / "scoring.json").read_text())
        scoring["fit_signals"].pop("Runs several US companies")
        (self.dir / "t_flip" / "scoring.json").write_text(json.dumps(scoring))
        p = mock.patch.object(icp, "DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)

    def test_services_is_the_default(self):
        self.assertEqual(icp.DEFAULT, "services")
        self.assertEqual(sorted(icp.available()), ["t_flip", "t_product"])

    def test_unknown_pipeline_is_an_error(self):
        with self.assertRaises(ValueError):
            with icp.use("nope"):
                pass

    def test_a_fact_counts_by_the_running_pipeline(self):
        from enrich.checks.rules import SIGNALS, phase_of
        with icp.use("t_product"):
            self.assertEqual((SIGNALS["F17"][0], phase_of("F17")), ("fit", "signals"))
            self.assertEqual(icp.run_if("F12"), {"F1": ["yes"]})
        with icp.use("t_flip"):
            self.assertEqual((SIGNALS["F17"][0], phase_of("F17")), ("exclusion", "icp"))
            self.assertEqual(icp.run_if("F12"), {})
            self.assertEqual(SIGNALS["F17"][1], "Runs several US companies")  # the fact keeps its name

    def test_a_proven_fact_excludes_only_where_it_is_a_hard_stop(self):
        from enrich.checks.rules import SIGNALS, decide
        s = {sid: {"value": "unknown", "evidence": ""} for sid in SIGNALS}
        s.update({m: {"value": "yes", "evidence": "x"} for m in ("M1", "M2", "M3", "M4")})
        s["F17"] = {"value": "yes", "evidence": "Acme Holdings runs Acme Labs and Acme Studio"}
        with icp.use("t_product"):
            self.assertEqual(decide(s, "live")["gate"], "pass")
        with icp.use("t_flip"):
            self.assertEqual(decide(s, "live")["gate"], "excluded")

    def test_results_are_kept_per_pipeline(self):
        from enrich.core.store import Store
        st = Store(Path(tempfile.mkdtemp()))
        with icp.use("t_product"):
            st.save_result("acme.com", {"v": "product"}, "icp")
        with icp.use("t_flip"):
            self.assertIsNone(st.get_result("acme.com", "icp"))
            st.save_result("acme.com", {"v": "flip"}, "icp")
        with icp.use("t_product"):
            self.assertEqual(st.get_result("acme.com", "icp"), {"v": "product"})

    def test_the_pipeline_follows_threads(self):
        import asyncio
        from enrich.pipeline.common import in_thread
        with icp.use("t_flip"):
            self.assertEqual(asyncio.run(in_thread(icp.name)), "t_flip")


class ServicesAgainstProduct(unittest.TestCase):
    """The real config/icps/services and config/icps/product."""

    def _signals(self, **values):
        from enrich.checks.rules import SIGNALS
        s = {sid: {"value": "unknown", "evidence": ""} for sid in SIGNALS}
        s.update({m: {"value": "yes", "evidence": "x"} for m in ("M1", "M2", "M3", "M4")})
        for sid, v in values.items():
            s[sid] = {"value": v, "evidence": "x"}
        return s

    def test_softened_hard_stops_only_cost_points_in_services(self):
        # Cold outreach (2026-11-12): several companies, new subsidiary, stock, marketplace and non-profit
        # are weak signals (-2, check on the call) until Alexey decides; none of them rejects a lead
        from enrich.checks.rules import SIGNALS, decide
        with icp.use("services"):
            for sid in ("F17", "B8", "E6", "S2", "E5"):
                self.assertEqual(SIGNALS[sid][0], "weak", sid)
                self.assertEqual(decide(self._signals(**{sid: "yes"}), "live")["gate"], "pass", sid)
        with icp.use("product"):
            self.assertEqual(decide(self._signals(F17="yes"), "live")["gate"], "pass")

    def test_non_usd_books_and_netsuite_are_hard_stops_for_services_only(self):
        from enrich.checks.rules import decide
        for sid in ("W6", "W10", "W7"):
            with icp.use("product"):
                self.assertNotEqual(decide(self._signals(**{sid: "yes"}), "live")["gate"], "excluded", sid)
            with icp.use("services"):
                self.assertEqual(decide(self._signals(**{sid: "yes"}), "live")["gate"], "excluded", sid)

    def test_an_llc_is_scored_down_for_product_only(self):
        from enrich.checks.scoring import score
        s = self._signals(W4="yes")
        with icp.use("product"):
            self.assertEqual(score(s, "SMB")["score"], -2)
        with icp.use("services"):
            self.assertEqual(score(s, "SMB")["score"], 0)

    def test_funding_scores_for_product_and_a_failed_bookkeeper_for_services(self):
        from enrich.checks.scoring import score
        with icp.use("product"):
            self.assertEqual(score(self._signals(F1="yes"), "SMB")["score"], 3)
            self.assertEqual(score(self._signals(B9="yes"), "SMB")["score"], 2)
        with icp.use("services"):
            self.assertEqual(score(self._signals(F1="yes"), "SMB")["score"], 0)
            self.assertEqual(score(self._signals(B9="yes"), "SMB")["score"], 3)

    def test_too_big_is_one_rule_for_services(self):
        from enrich.checks.rules import decide
        s = self._signals(S7="yes")
        s["S7"]["evidence"] = "140 employees"
        with icp.use("services"):
            self.assertEqual(decide(s, "live")["exclusion_reason"],
                             "Too big for services (100 or more people): 140 employees")
        s = self._signals(E3="yes")
        with icp.use("services"):
            self.assertFalse(decide(s, "live")["exclusion_reason"].startswith("Too big"))

    def test_output_files_are_named_per_pipeline(self):
        folder = Path("/tmp/lists/acme")
        with icp.use("services"):
            self.assertEqual(icp.enriched_csv(folder).name, "acme_enriched.csv")
        with icp.use("product"):
            self.assertEqual(icp.enriched_csv(folder).name, "acme_product_enriched.csv")

    def test_every_weight_names_a_data_point_of_that_group(self):
        from enrich.checks.rules import FACTS, SIGNALS
        from enrich.checks.scoring import config
        for pipeline in icp.available():
            with icp.use(pipeline):
                cfg = config()
                by_name = {n: sid for sid, (_, n) in FACTS.items()}
                for name in cfg["fit_signals"]:
                    self.assertEqual(SIGNALS[by_name[name]][0], "fit", (pipeline, name))
                for name in cfg["buying_signals"]:
                    self.assertEqual(SIGNALS[by_name[name]][0], "buying", (pipeline, name))


if __name__ == "__main__":
    unittest.main()
