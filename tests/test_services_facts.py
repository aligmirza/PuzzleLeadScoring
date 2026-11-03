"""The services facts (S1-S8): found by free rules on sample pages, only from proof.

    .venv/bin/python -m unittest discover -s tests -v
"""
import unittest

from enrich.core import icp


def run(pages: dict[str, str], jobs=None, employees="", phase="signals", pipeline="services", record_extra=None):
    from enrich.checks.rules import extract
    pages = {"home": "<p>Acme makes software for teams.</p>", **pages}  # every site read has a homepage
    record = {"domain": "acme.com", "status": "live", "final_url": "https://acme.com/", "headers": {},
              "pages": {k: f"https://acme.com/{k}" for k in pages}, **(record_extra or {})}
    html = {k: f"<html><body><h1>Acme</h1>{v}<footer>© 2026 Acme</footer></body></html>" for k, v in pages.items()}
    with icp.use(pipeline):
        return extract("acme.com", "Acme", employees, record, html, jobs or [], phase)


def value(result, sid):
    return result["signals"].get(sid, {}).get("value")


class SoleProprietorshipOrPartnership(unittest.TestCase):
    def test_self_statement_counts(self):
        r = run({"terms": "<p>Acme Consulting is a general partnership organized in Texas.</p>"}, phase="icp")
        self.assertEqual(value(r, "S1"), "yes")

    def test_a_corporation_or_llc_in_the_legal_name_is_the_no(self):
        r = run({"terms": "<p>We are Acme Labs, Inc., doing business as Acme.</p>"}, phase="icp")
        self.assertEqual(value(r, "S1"), "no")

    def test_terms_about_customers_do_not(self):
        r = run({"terms": "<p>If you are a sole proprietor, you agree to these terms on your own behalf.</p>"}, phase="icp")
        self.assertNotEqual(value(r, "S1"), "yes")


class Marketplace(unittest.TestCase):
    def test_says_it_is_one(self):
        r = run({"home": "<p>Acme is a peer-to-peer trailer marketplace for owners and renters.</p>"}, phase="icp")
        self.assertEqual(value(r, "S2"), "yes")

    def test_listing_on_a_cloud_marketplace_is_not(self):
        r = run({"home": "<p>Acme is available on the AWS Marketplace and the Salesforce AppExchange.</p>"}, phase="icp")
        self.assertNotEqual(value(r, "S2"), "yes")

    def test_a_tool_for_marketplaces_is_not(self):
        r = run({"home": "<p>A tool that helps recruiters manage a two-sided marketplace.</p>"}, phase="icp")
        self.assertNotEqual(value(r, "S2"), "yes")


class BooksAndHelp(unittest.TestCase):
    JOBS = [{"title": "Part-time Bookkeeper", "location": "Remote", "remote": True,
             "description": "We need help with a catch-up of the last 8 months and ongoing reconciliations.",
             "url": "https://jobs.example/acme/1"}]

    def test_a_bookkeeper_job_with_catch_up_work(self):
        r = run({"home": "<p>Acme makes software.</p>"}, jobs=self.JOBS)
        self.assertEqual((value(r, "S3"), value(r, "S4")), ("yes", "yes"))

    def test_an_engineering_cleanup_is_not_books(self):
        jobs = [{"title": "Backend Engineer", "location": "NYC", "remote": False,
                 "description": "Clean up our technical backlog and data pipelines.", "url": ""}]
        r = run({"home": "<p>Acme makes software.</p>"}, jobs=jobs)
        self.assertNotIn(value(r, "S3"), ("yes", "check"))
        self.assertNotEqual(value(r, "S4"), "yes")

    def test_does_its_own_books(self):
        r = run({"about": "<p>We do our own bookkeeping in spreadsheets and Claude.</p>"})
        self.assertEqual(value(r, "S5"), "yes")


class SizeAndStage(unittest.TestCase):
    def test_headcount_bands(self):
        self.assertEqual([value(run({"home": "<p>x</p>"}, employees="120", phase="icp"), "S7")], ["yes"])
        r = run({"home": "<p>x</p>"}, employees="51-200")  # the lower band: 51
        self.assertEqual((value(r, "S7"), value(r, "S8")), ("no", "yes"))
        r = run({"home": "<p>x</p>"}, employees="12")
        self.assertEqual((value(r, "S7"), value(r, "S8")), ("no", "no"))

    def test_own_seed_round_rules_out_series_d(self):
        r = run({"blog": "<p>Acme Raises $5.5 Million Seed Round 7/2/2025</p>"}, phase="icp")
        self.assertEqual(value(r, "S6"), "no")
        r = run({"blog": "<p>We help founders plant the seed of a great idea.</p>"}, phase="icp")
        self.assertNotEqual(value(r, "S6"), "no")

    def test_size_is_credited_to_its_source(self):
        r = run({"home": "<p>x</p>"}, employees="12", phase="icp")
        self.assertEqual(r["signals"]["S7"]["method"], "code")
        r = run({"home": "<p>x</p>"}, phase="icp", record_extra={"headcount_hint": {"value": 30, "source": "Prospeo headcount"}})
        self.assertEqual((r["signals"]["S7"]["value"], r["signals"]["S7"]["method"]), ("no", "provider"))

    def test_no_count_no_answer(self):
        self.assertEqual(value(run({"home": "<p>x</p>"}, phase="icp"), "S7"), "unknown")


class PipelineRoles(unittest.TestCase):
    def test_new_facts_are_info_for_product_and_count_for_services(self):
        from enrich.checks.rules import SIGNALS, zero_point_sids
        with icp.use("product"):
            self.assertEqual({SIGNALS[s][0] for s in ("S1", "S2", "S3", "S6", "S7")}, {"info"})
            self.assertTrue({"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"} <= zero_point_sids())
            self.assertNotIn("F1", zero_point_sids())
        with icp.use("services"):
            self.assertEqual((SIGNALS["S2"][0], SIGNALS["S3"][0], SIGNALS["S5"][0], SIGNALS["E1"][0]),
                             ("exclusion", "buying", "weak", "info"))
            zero = zero_point_sids()
            self.assertTrue({"E1", "F1", "F10", "F11"} <= zero)  # no paid questions on funding, AI, YC
            self.assertFalse({"S3", "S4", "B9"} & zero)

    def test_a_marketplace_is_excluded_by_services_only(self):
        page = {"home": "<p>Acme is a peer-to-peer trailer marketplace for owners and renters.</p>"}
        self.assertNotEqual(run(page, phase="icp", pipeline="product")["gate"], "excluded")
        self.assertEqual(run(page, phase="icp", pipeline="services")["gate"], "excluded")


class RouteArrivalAngle(unittest.TestCase):
    def cols(self, yes=(), yes_no="YES", gate="pass", reason=""):
        from enrich.pipeline.final import services_columns
        s = {sid: {"value": "yes"} for sid in yes}
        return services_columns(yes_no, {"gate": gate, "exclusion_reason": reason}, s)

    def test_books_behind_means_catch_up_and_reactive(self):
        c = self.cols(("S3", "F9"))
        self.assertEqual((c["Route"], c["Arrival"]), ("Catch-Up, then Full Service", "Reactive"))

    def test_a_failed_bookkeeper_means_full_service_with_the_replacement_angle(self):
        c = self.cols(("B9",))
        self.assertEqual(c["Route"], "Full Service")
        self.assertIn("replacement", c["Value angle"])

    def test_diy_goes_to_the_platform(self):
        self.assertTrue(self.cols(("S5",))["Route"].startswith("Platform only"))

    def test_just_raised_is_proactive(self):
        self.assertEqual(self.cols(("B1",))["Arrival"], "Proactive")

    def test_nothing_known_is_qualified_on_the_call(self):
        c = self.cols()
        self.assertEqual((c["Route"], c["Arrival"]), ("Qualify on the call: Full Service or Expert Review", ""))

    def test_hard_stops_go_to_a_partner_and_accounting_firms_to_the_firm_icp(self):
        self.assertEqual(self.cols(("S2",), yes_no="NO", gate="excluded")["Route"], "Partner referral (hard stop)")
        self.assertTrue(self.cols(("E3",), yes_no="NO", gate="excluded")["Route"].startswith("Firm ICP"))
        self.assertEqual(self.cols((), yes_no="NO", gate="failed must-have")["Route"], "Not ICP")

    def test_site_not_reached_is_a_retry(self):
        from enrich.pipeline.final import services_columns
        c = services_columns("PENDING", {"icp_verdict": "Unknown: site not reached", "gate": ""}, {})
        self.assertEqual(c["Route"], "Retry: website not reached")


if __name__ == "__main__":
    unittest.main()
