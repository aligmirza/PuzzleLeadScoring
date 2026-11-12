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
        r = run({"home": "<p>Acme is a peer-to-peer trailer marketplace for owners and renters.</p>"})
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


class CustomerTypes(unittest.TestCase):
    """S9-S12: Alexey's customer types (subscriptions, contractors), proven from code and the company's own words."""

    def test_several_processors(self):
        r = run({"pricing": '<script src="https://js.stripe.com/v3/"></script>'
                            '<script src="https://www.paypal.com/sdk/js?client-id=x"></script><p>Plans</p>'})
        self.assertEqual(value(r, "S9"), "yes")
        r = run({"pricing": '<script src="https://js.stripe.com/v3/"></script><p>Plans</p>'})
        self.assertNotEqual(value(r, "S9"), "yes")  # one processor is not several

    def test_subscription_price_or_membership(self):
        self.assertEqual(value(run({"pricing": "<p>Premium $24.99/month, billed annually</p>"}), "S10"), "yes")
        self.assertEqual(value(run({"home": "<p>Become a member and get every course.</p>"}), "S10"), "yes")
        self.assertNotEqual(value(run({"pricing": "<p>Projects from $5,000.</p>"}), "S10"), "yes")
        self.assertNotEqual(value(run({"home": "<p>Hosts lose $17,500 / month in fees.</p>"}), "S10"), "yes")  # a calculator

    def test_field_service_software(self):
        r = run({"home": '<p>Drilling contractors.</p><a href="https://clienthub.getjobber.com/booking/abc">Book</a>'})
        self.assertEqual(value(r, "S11"), "yes")

    def test_years_in_business(self):
        self.assertEqual(value(run({"about": "<p>Family owned and operated since 1998.</p>"}), "S12"), "yes")
        self.assertEqual(value(run({"about": "<p>Proudly serving Texas: over 25 years in business.</p>"}), "S12"), "yes")
        bio = "<p>Ian Ahl, CTO: 20+ years of experience in incident response.</p>"
        self.assertNotEqual(value(run({"about": bio}), "S12"), "yes")  # a person's career, not the company's age
        self.assertNotEqual(value(run({"about": "<p>Founded in 2023 in Brooklyn.</p>"}), "S12"), "yes")

    def test_they_score_for_services_only(self):
        from enrich.checks.scoring import score
        s = {"S10": {"value": "yes", "evidence": "x"}}
        with icp.use("services"):
            self.assertEqual(score(s, "SMB")["score"], 2)
        with icp.use("product"):
            self.assertEqual(score(s, "SMB")["score"], 0)


class TestOnRealDeals(unittest.TestCase):
    """Fixes from the 26-deal test (Nov 9, 2026): paying customers the rules wrongly failed or scored."""

    def test_no_working_website_is_unknown_for_services(self):
        soon = {"home": "<p>Coming Soon</p>"}
        r = run(soon, phase="icp")
        self.assertEqual(value(r, "M2"), "unknown")  # Ridgeline Drilling
        self.assertEqual(r["icp_verdict"], "Needs a call: no website")  # nothing proven: not a YES
        self.assertEqual(value(run(soon, phase="icp", pipeline="product"), "M2"), "no")  # startups still need a site
        gone = {"record_extra": {"status": "dead", "error": "http 404"}}
        r = run({}, phase="icp", **gone)
        self.assertEqual((value(r, "M2"), r["icp_verdict"]), ("unknown", "Needs a call: no website"))  # CanAm Logistics
        self.assertEqual(value(run({}, phase="icp", pipeline="product", **gone), "M2"), "no")

    def test_a_wrong_or_sold_domain_still_fails(self):
        no_dns = {"record_extra": {"status": "dead", "error": "domain does not resolve"}}
        self.assertEqual(run({}, phase="icp", **no_dns)["icp_verdict"], "No")
        for_sale = {"home": "<p>This domain is for sale. Buy this domain today.</p>"}
        self.assertEqual(run(for_sale, phase="icp")["icp_verdict"], "No")

    def test_foreign_fees_need_the_ai_only_where_it_is_a_hard_stop(self):
        fees = {"pricing": "<p>Plans from $49/month. Card 2.2%. UK bank transfer 1% (min £1)</p>"}
        self.assertEqual(value(run(fees, phase="icp"), "W6"), "check")  # Dueflow: not proof of multi-currency books
        self.assertEqual(value(run(fees, pipeline="product"), "W6"), "yes")  # a score-down only (phase 2): free

    def test_hybrid_must_be_about_the_team(self):
        for product in ("A hybrid between direct mail and digital ads.", "Our hybrid model pairs AI with expert bookkeepers.",
                        "A hybrid setup of software and people.", "Hybrid-cloud backups."):
            self.assertNotEqual(value(run({"about": f"<p>{product}</p>"}), "F16"), "yes", product)
        for team in ("We are a hybrid team based in Austin.", "Engineer, Austin (Hybrid)", "Location: Hybrid",
                     "Remote/hybrid", "Hybrid - Austin, TX", "Hybrid work, 3 days in office"):
            self.assertEqual(value(run({"careers": f"<p>{team}</p>"}), "F16"), "yes", team)


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
            self.assertEqual((SIGNALS["S2"][0], SIGNALS["S3"][0], SIGNALS["S5"][0], SIGNALS["E1"][0], SIGNALS["S10"][0]),
                             ("weak", "buying", "info", "info", "fit"))  # S5: no points off (Alexey, Nov 2026)
            zero = zero_point_sids()
            self.assertTrue({"E1", "F1", "F10", "F11"} <= zero)  # no paid questions on funding, AI, YC
            self.assertFalse({"S3", "S4", "B9"} & zero)

    def test_a_marketplace_is_not_a_hard_stop_for_cold_outreach(self):
        # Cold outreach (2026-11-12): a marketplace is a weak signal (-2, check on the call), never a rejection
        page = {"home": "<p>Acme is a peer-to-peer trailer marketplace for owners and renters.</p>"}
        for pipeline in ("product", "services"):
            self.assertNotEqual(run(page, pipeline=pipeline)["gate"], "excluded", pipeline)


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

    def test_diy_goes_to_the_platform_and_mcp(self):
        self.assertTrue(self.cols(("S5",))["Route"].startswith("Platform + MCP"))

    def test_real_estate_and_legal_ask_how_the_books_are_handled(self):
        from enrich.pipeline.final import services_columns
        c = services_columns("YES", {"gate": "pass"}, {}, "Legal")
        self.assertIn("ask how and by whom the books are handled", c["Route"])
        c = services_columns("YES", {"gate": "pass"}, {"B9": {"value": "yes"}}, "Legal")
        self.assertEqual(c["Route"], "Full Service")  # a stronger reason wins

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


class ColdOutreach(unittest.TestCase):
    """Cold outreach (Nov 12, 2026): the 82 real leads; recall first, the score is the outreach order."""

    def test_a_waitlist_startup_passes_as_pre_launch(self):
        r = run({"home": "<p>Acme helps clinics bill faster. Join the waitlist for early access.</p>"}, phase="icp")
        self.assertEqual(value(r, "M4"), "no")
        self.assertNotEqual(r["icp_verdict"], "No")  # was rejected before; now the AI confirms the site, then pre-launch
        p = run({"home": "<p>Acme helps clinics bill faster. Join the waitlist.</p>"}, phase="icp", pipeline="product")
        self.assertNotEqual(p["icp_verdict"], "Yes (pre-launch)")

    def test_a_hard_no_on_a_soft_must_still_fails(self):
        from enrich.checks.rules import decide, SIGNALS
        s = {sid: {"value": "unknown", "evidence": ""} for sid in SIGNALS}
        s.update({m: {"value": "yes", "evidence": "x"} for m in ("M1", "M3", "M4")})
        with icp.use("services"):
            s["M2"] = {"value": "no", "evidence": "parked", "hard": True}
            self.assertEqual(decide(s, "live")["verdict"], "No")
            s["M2"] = {"value": "no", "evidence": "waitlist"}
            self.assertEqual(decide(s, "live")["verdict"], "Yes (pre-launch)")
            s["M2"], s["M4"] = {"value": "yes", "evidence": "x"}, {"value": "no", "evidence": "waitlist only"}
            self.assertEqual(decide(s, "live")["verdict"], "Yes (pre-launch)")

    def test_a_godaddy_for_sale_page_fails(self):
        r = run({"home": "<p>acmeroofing.com is for sale! Get this domain.</p>"}, phase="icp")
        self.assertEqual(r["icp_verdict"], "No")

    def test_a_stripe_donation_button_is_not_billing(self):
        page = {"home": "<p>Support our work: donate through Stripe.</p>"}
        self.assertNotEqual(value(run(page), "F2"), "yes")

    def test_a_bio_is_not_the_company_age(self):
        bio = {"about": "<p>Jane, our founder, has worked in finance for 25 years.</p>"}
        self.assertNotEqual(value(run(bio), "S12"), "yes")
        own = {"about": "<p>Proudly serving Austin families since 1995.</p>"}
        self.assertEqual(value(run(own), "S12"), "yes")

    def test_the_sites_own_headcount_counts(self):
        r = run({"about": "<p>Our 150+ team supports brands across the US.</p>"}, phase="icp")
        self.assertEqual(value(r, "S7"), "yes")
        r = run({"about": "<p>We are a team of 12 bookkeeping fans.</p>"}, phase="icp")
        self.assertEqual(value(r, "S7"), "no")

    def test_work_from_anywhere_counts_only_on_the_careers_page(self):
        self.assertNotEqual(value(run({"home": "<p>Send invoices and work from anywhere.</p>"}), "F16"), "yes")
        self.assertEqual(value(run({"careers": "<p>Join us. Work from anywhere in the US.</p>"}), "F16"), "yes")

    def test_a_3pl_warehouse_is_a_weak_signal_at_most(self):
        from enrich.checks.rules import SIGNALS
        with icp.use("services"):
            self.assertEqual(SIGNALS["E6"][0], "weak")

    def test_outreach_priority(self):
        from enrich.pipeline.final import outreach_priority
        with icp.use("services"):
            self.assertEqual(outreach_priority("YES", "Yes", 9), "A")
            self.assertEqual(outreach_priority("YES", "Yes", 4), "B")
            self.assertEqual(outreach_priority("YES", "Yes", 1), "C")
            self.assertEqual(outreach_priority("YES", "Yes (pre-launch)", 12), "C")
            self.assertEqual(outreach_priority("PENDING", "Needs a call: no website", 0), "C")
            self.assertEqual(outreach_priority("NO", "No", 9), "")
