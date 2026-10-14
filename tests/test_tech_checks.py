"""DNS check (sources/dns_check.py), what it settles in step 2, and Wappalyzer matching (sources/wappalyzer.py).

    .venv/bin/python -m unittest discover -s tests -v

Offline: DNS answers and fingerprints are faked.
"""
import unittest
from unittest import mock

from enrich.sources import dns_check, wappalyzer

ARGON_TXT = ["rippling-domain-verification=0d3261d34d9a6918", "oneleet-domain-verification-db34c35e",
             "google-site-verification=ZuQX", "v=spf1 include:_spf.google.com include:amazonses.com include:4.spf01.hubspotemail.net ~all"]


def fake_query(c, name, rtype):
    return ARGON_TXT if rtype == "TXT" else ["1 smtp.google.com."]


class DNSCheck(unittest.TestCase):
    def test_reads_verifications_spf_and_email(self):
        with mock.patch.object(dns_check, "_query", fake_query):
            d = dns_check.lookup("argon-ai.com")
        self.assertEqual(d["tools"], {"Rippling": "rippling-domain-verification=0d3261d34d9a6918"})
        self.assertEqual(d["verified_with"], ["google", "oneleet", "rippling"])
        self.assertIn("amazonses.com", d["spf_includes"])
        self.assertEqual(d["email"], "Google Workspace")

    def test_only_records_that_name_the_tool_count(self):
        with mock.patch.object(dns_check, "_query", lambda c, n, t: ["ramp-up-campaign=1", "brexit-news=2",
                                                                      "stripe-verification=abc"] if t == "TXT" else []):
            d = dns_check.lookup("x.com")
        self.assertEqual(list(d["tools"]), ["Stripe"])


class DNSInWaterfall(unittest.TestCase):
    def _signals(self, dns):
        from enrich.checks.rules import extract
        found = {"version": 0, "legal_name": None, "errors": {}, "complete": True, "DNS": dns}
        home = ("<html><body><h1>X</h1><p>X is a B2B SaaS platform. Plans from $49 per month.</p>"
                "<footer>X, Inc. 1 Main St, San Francisco, CA 94105</footer></body></html>")
        record = {"domain": "x.com", "status": "live", "final_url": "https://x.com/", "headers": {},
                  "pages": {"home": "https://x.com/", "about": "https://x.com/about", "pricing": "https://x.com/pricing"}}
        pages = {"home": home, "about": "<html><body><p>About X.</p></body></html>",
                 "pricing": "<html><body><p>Starter $49 per month.</p></body></html>"}
        with mock.patch("enrich.sources.directories.ENABLED", True):
            return extract("x.com", "X", "10", record, pages, [], "signals", lambda *a, **k: found)

    def test_rippling_record_settles_payroll_and_fintech_tool_at_medium(self):
        with mock.patch.object(dns_check, "_query", fake_query):
            dns = dns_check.lookup("x.com")
        r = self._signals(dns)
        f9 = r["signals"]["F9"]
        self.assertEqual(f9["value"], "yes")
        self.assertEqual(f9["method"], "directory")
        self.assertIn("rippling-domain-verification", f9["evidence"])
        self.assertIn("medium match", f9["note"])
        self.assertEqual(f9["url"], "https://dns.google/query?name=x.com&rr_type=TXT")
        self.assertEqual(r["signals"]["M3"]["value"], "yes")
        self.assertIn("DNS: Google Workspace, google, oneleet, rippling", r["directories"])

    def test_no_tool_records_change_nothing(self):
        r = self._signals({"txt": ["google-site-verification=a"], "mx": [], "spf_includes": [], "verified_with": ["google"],
                           "tools": {}, "email": ""})
        self.assertEqual(r["signals"]["F9"]["value"], "unknown")


class Wappalyzer(unittest.TestCase):
    DB = {"cats": {"41": "Payment processors", "1": "CMS", "27": "Programming languages"}, "compiled": {}, "techs": {
        "Stripe": {"cats": [41], "scriptSrc": ["js\\.stripe\\.com"], "dns": {"TXT": ["stripe-verification="]}},
        "WordPress": {"cats": [1], "meta": {"generator": "^WordPress ?([\\d.]+)?\\;version:\\1"}, "implies": ["PHP"]},
        "PHP": {"cats": [27], "headers": {"X-Powered-By": "^php/?([\\d.]+)?\\;version:\\1"}},
    }}

    def setUp(self):
        p = mock.patch.object(wappalyzer, "_db", {**self.DB, "compiled": {}})
        p.start()
        self.addCleanup(p.stop)

    def test_script_meta_header_dns_and_implies(self):
        html = '<meta name="generator" content="WordPress 6.4"><script src="https://js.stripe.com/v3/"></script>'
        f = wappalyzer.analyze({"https://x.com/": html})
        self.assertEqual(f["Stripe"]["categories"], ["Payment processors"])
        self.assertEqual(f["WordPress"]["version"], "6.4")
        self.assertEqual(f["PHP"]["how"], "implied by WordPress")
        self.assertIn("Stripe", wappalyzer.analyze(dns={"TXT": ["stripe-verification=abc"]}))
        self.assertEqual(wappalyzer.analyze(headers={"X-Powered-By": "PHP/8.2"})["PHP"]["version"], "8.2")

    def test_dom_selectors_become_html_rules(self):
        wappalyzer._db["techs"]["Carta"] = {"cats": [1], "dom": ["link[href*='.app.carta.com/']"]}
        f = wappalyzer.analyze({"https://x.com/": '<link rel="preconnect" href="https://acme.app.carta.com/x">'})
        self.assertIn("Carta", f)
        self.assertNotIn("Carta", wappalyzer.analyze({"https://x.com/": "<p>We love Carta</p>"}))

    def test_a_tool_fingerprint_settles_its_data_point(self):
        from enrich.checks.rules import extract
        home = ('<html><head><script src="https://js.stripe.com/v3/"></script></head><body><h1>X</h1>'
                "<p>X is a B2B SaaS platform. Plans from $49 per month.</p>"
                "<footer>X, Inc. 1 Main St, San Francisco, CA 94105</footer></body></html>")
        record = {"domain": "x.com", "status": "live", "final_url": "https://x.com/", "headers": {},
                  "pages": {"home": "https://x.com/", "about": "https://x.com/about", "pricing": "https://x.com/pricing"},
                  "technologies": {"Carta": {"categories": ["Cap table"], "how": "page code on https://x.com/careers", "version": ""}}}
        pages = {"home": home, "about": "<p>About</p>", "pricing": "<p>Starter $49 per month.</p>"}
        s = extract("x.com", "X", "10", record, pages, [], "signals")["signals"]
        self.assertEqual(s["F21"]["value"], "yes")
        self.assertIn("Wappalyzer fingerprint", s["F21"]["evidence"])
        self.assertEqual(s["F2"]["value"], "yes")

    def test_summary_lists_direct_finds_only(self):
        f = wappalyzer.analyze({"https://x.com/": '<meta name="generator" content="WordPress 6.4">'})
        self.assertEqual(wappalyzer.summary(f), "WordPress (CMS)")


class ProspeoTeam(unittest.TestCase):
    def _f13(self, team, searched=True):
        from enrich.checks.rules import SIGNALS, UNKNOWN
        from enrich.providers.apply import apply
        from enrich.providers.run import people_record
        people = [{"name": n, "title": tt, "person_id": n, "linkedin": ""} for n, tt in team]
        rec = people_record(people[:3], people)
        if not searched:
            rec.pop("team_searched")
        result = {"domain": "x.com", "signals": {k: {"value": UNKNOWN, "evidence": "", "url": "", "note": "", "method": "code"} for k in SIGNALS},
                  "crawl": {}, "facts": {}}
        return apply(result, {"prospeo": {"people": rec}}, "signals")["signals"]["F13"]

    def test_full_team_without_finance_is_yes(self):
        f13 = self._f13([("Ann", "Founder & CEO"), ("Bo", "CTO"), ("Cy", "Software Engineer")])
        self.assertEqual(f13["value"], "yes")
        self.assertIn("No finance or accounting title among the 3 people Prospeo lists at x.com: Ann (Founder & CEO); Bo (CTO)", f13["evidence"])
        self.assertEqual(f13["method"], "provider")

    def test_full_team_without_finance_means_no_finance_lead(self):
        from enrich.checks.rules import SIGNALS, UNKNOWN
        from enrich.providers.apply import apply
        from enrich.providers.run import people_record
        people = [{"name": n, "title": tt, "person_id": n} for n, tt in (("Ann", "CEO"), ("Bo", "Engineer"))]
        result = {"domain": "x.com", "signals": {k: {"value": UNKNOWN, "evidence": "", "url": "", "note": "", "method": "code"}
                                                 for k in SIGNALS}, "crawl": {}, "facts": {}}
        f14 = apply(result, {"prospeo": {"people": people_record(people, people)}}, "signals")["signals"]["F14"]
        self.assertEqual(f14["value"], "no")

    def test_any_finance_title_is_no(self):
        self.assertEqual(self._f13([("Ann", "CEO"), ("Dee", "Finance Associate")])["value"], "no")

    def test_full_page_or_old_saves_stay_open(self):
        self.assertEqual(self._f13([(f"P{i}", "Engineer") for i in range(25)])["value"], "unknown")
        self.assertEqual(self._f13([("Ann", "CEO"), ("Bo", "CTO")], searched=False)["value"], "unknown")


class OneCreditPerCompany(unittest.TestCase):
    COMPANY = {"name": "X", "domain": "x.com", "website": "https://x.com", "industry": "Software Development",
               "employee_count": 10, "location": {"city": "Brooklyn", "state": "New York", "country": "United States"},
               "founded": 2023, "funding": {"total_funding_printed": "$5.5M", "latest_funding_stage": "Seed",
                                            "latest_funding_date": "2025-08-02T00:00:00"},
               "linkedin_url": "https://www.linkedin.com/company/x"}

    def _search(self, found):
        res = [{"person": {"person_id": n, "full_name": n, "current_job_title": tt, "linkedin_url": f"https://linkedin.com/in/{n}",
                           "location": {"city": "NYC", "country": "United States"}}, "company": self.COMPANY} for n, tt in found]
        return mock.patch("enrich.providers.prospeo.search_people", return_value=(res, 1.0 if res else 0.0, False))

    def test_people_found_means_no_company_call(self):
        from enrich.providers import run
        with self._search([("Ann", "CEO"), ("Bo", "Engineer")]), \
                mock.patch("enrich.providers.prospeo.company", side_effect=AssertionError("second credit")) as comp:
            got = run.fetch_one("x.com")
        self.assertEqual(got["credits"], 1.0)
        self.assertEqual(got["company"]["industry"], "Software Development")
        comp.assert_not_called()

    def test_nobody_found_falls_back_to_the_company_record(self):
        from enrich.providers import run
        with self._search([]), mock.patch("enrich.providers.prospeo.company", return_value=(self.COMPANY, 1.0, False)):
            got = run.fetch_one("x.com")
        self.assertEqual(got["credits"], 1.0)
        self.assertEqual(got["people"], [])

    def test_contacts_csv_has_everyone_with_company_details(self):
        import csv
        import tempfile
        from pathlib import Path
        from enrich.core.store import Store
        from enrich.providers import run
        folder = Path(tempfile.mkdtemp()) / "lst"
        store = Store(folder)
        with self._search([("Ann", "Co-Founder, CEO"), ("Bo", "Engineer"), ("Cy", "Designer"), ("Di", "Intern")]):
            run.save_one(store, "x.com", run.fetch_one("x.com"))
        self.assertTrue(run.has_prospeo(store, "x.com"))
        rows = list(csv.DictReader(open(run.write_contacts(folder, store))))
        self.assertEqual([r["Name"] for r in rows], ["Ann", "Bo", "Cy", "Di"])  # all 4, not just the top 3
        self.assertEqual(rows[0]["Decision maker"], "yes")
        self.assertEqual((rows[1]["Industry"], rows[1]["HQ"], rows[1]["Latest round"]),
                         ("Software Development", "Brooklyn, New York, United States", "Seed"))


class SizeFirst(unittest.TestCase):
    def _run(self, employees="", hint=None):
        from enrich.checks.rules import extract
        home = ("<html><body><h1>X</h1><p>X is a B2B SaaS platform. Plans from $49 per month.</p>"
                "<footer>X, Inc. 1 Main St, San Francisco, CA 94105</footer></body></html>")
        record = {"domain": "x.com", "status": "live", "final_url": "https://x.com/", "headers": {},
                  "pages": {"home": "https://x.com/", "about": "https://x.com/about", "pricing": "https://x.com/pricing",
                            "team": "https://x.com/team"}, **({"headcount_hint": hint} if hint else {})}
        pages = {"home": home, "about": "<html><body><p>About X.</p></body></html>",
                 "pricing": "<html><body><p>Starter $49 per month.</p></body></html>",
                 "team": "<html><body><p>Ann, CEO. Bo, CTO.</p></body></html>"}
        return extract("x.com", "X", employees, record, pages, [], "signals")

    def test_list_count_first(self):
        r = self._run("3")
        self.assertEqual(r["facts"]["segment"], "SMB")
        self.assertNotIn("fit/finance_lead_present", r["ai"]["prompts"])  # F14 can't score for SMB: not asked
        self.assertIn("not scored for SMB", r["signals"]["F14"]["note"])

    def test_prospeo_headcount_when_the_list_has_none(self):
        r = self._run("", {"value": 10, "source": "Prospeo company record"})
        self.assertEqual((r["facts"]["segment"], r["facts"]["segment_basis"]), ("MM", "10 people per the Prospeo company record"))
        self.assertNotIn("fields/segment_headcount", r["ai"]["prompts"])

    def test_ent_skips_no_finance_person(self):
        r = self._run("300")
        self.assertEqual(r["facts"]["segment"], "ENT")
        self.assertNotIn("fit/no_finance_person_visible", r["ai"]["prompts"])

    def test_yc_team_size_after_prospeo(self):
        from enrich.checks.rules import Extractor
        x = Extractor("x.com", "X", "", {"status": "live", "pages": {}}, {}, [], "signals")
        x.facts["team_size_directory"] = {"value": 4, "source": "YC directory"}
        self.assertEqual(x.size(), ("SMB", "4 people per the YC directory"))
        x.record["headcount_hint"] = {"value": 10, "source": "Prospeo company record"}
        self.assertEqual(x.size()[0], "MM")

    def test_unknown_size_asks_both(self):
        r = self._run("")
        self.assertIsNone(r["facts"]["segment"])

    def test_ent_search_is_the_finance_department(self):
        from enrich.providers import run
        with mock.patch("enrich.providers.prospeo.search_people", return_value=([], 0.0, False)) as s, \
                mock.patch("enrich.providers.prospeo.company", side_effect=AssertionError("no fallback for ENT")):
            got = run.fetch_one("x.com", "ENT")
        self.assertEqual(s.call_args.kwargs["departments"], ["Finance"])
        self.assertEqual(got["search"], "finance department")
        rec = run.people_record([], [{"name": "Eve", "title": "Senior Analyst"}], "finance department")
        self.assertFalse(rec["team_searched"])          # can't prove "nobody in finance"
        self.assertEqual(rec["finance"][0]["name"], "Eve")  # everyone found is in finance


class GrowthNumbers(unittest.TestCase):
    def _f15(self, about="", jobs=None):
        from enrich.checks.rules import extract
        home = ("<html><body><h1>X</h1><p>X is a B2B SaaS platform. Plans from $49 per month.</p>"
                "<footer>X, Inc. 1 Main St, San Francisco, CA 94105</footer></body></html>")
        record = {"domain": "x.com", "status": "live", "final_url": "https://x.com/", "headers": {}, "ats": "ashby",
                  "ats_slug": "x", "jobs_count": len(jobs or []),
                  "pages": {"home": "https://x.com/", "about": "https://x.com/about", "pricing": "https://x.com/pricing"}}
        pages = {"home": home, "about": f"<html><body><p>{about}</p></body></html>",
                 "pricing": "<html><body><p>Starter $49 per month.</p></body></html>"}
        return extract("x.com", "X", "10", record, pages, jobs or [], "signals")["signals"]["F15"]

    def test_job_post_growth_line_is_yes(self):
        job = {"title": "Founding Member", "location": "NYC", "remote": False,
               "description": "About us. " * 100 + "We're growing quickly, with 7-figure ARR up 5x year-to-date."}
        f15 = self._f15(jobs=[job])
        self.assertEqual(f15["value"], "yes")
        self.assertIn("7-figure ARR up 5x", f15["evidence"])
        self.assertEqual(f15["url"], "https://jobs.ashbyhq.com/x")
        self.assertIn("job post 'Founding Member'", f15["note"])

    def test_others_numbers_dont_count(self):
        self.assertEqual(self._f15("Our customers save 40% on fees. AI is a $50B market.")["value"], "unknown")

    def test_number_without_a_subject_is_a_clue(self):
        self.assertEqual(self._f15("The platform passed $10M ARR in 2025.")["value"], "check")


class StopWhenDecided(unittest.TestCase):
    def _signals(self, yes=(), open_=(), weak_open=()):
        from enrich.checks.rules import SIGNALS
        s = {sid: {"value": "no", "evidence": "", "url": "", "note": "", "method": "code"} for sid in SIGNALS}
        for sid in yes:
            s[sid]["value"] = "yes"
        for sid in (*open_, *weak_open):
            s[sid]["value"] = "unknown"
        return s

    def test_locked_when_no_open_signal_can_cross_the_line(self):
        from enrich.checks.scoring import tier_range
        strong = self._signals(yes=("F1", "F2", "F3", "F4", "F5"), open_=("F11",))  # 15 points, F11 open (+2)
        r = tier_range(strong, "MM")
        self.assertTrue(r["locked"])
        self.assertEqual(r["tier"], "Strong fit")
        self.assertIn("the tier stays Strong fit", r["why"])

    def test_not_locked_when_answers_could_change_the_tier(self):
        from enrich.checks.scoring import tier_range
        close = self._signals(yes=("F1", "F2", "F3"), open_=("F5", "F6"))  # 9 + up to 6
        self.assertFalse(tier_range(close, "MM")["locked"])
        weak_risk = self._signals(yes=("F1", "F2", "F3", "F4"), weak_open=("W1",))  # 12, a weak signal could drop it
        self.assertFalse(tier_range(weak_risk, "MM")["locked"])

    def test_unknown_after_its_waterfall_is_final(self):
        from enrich.checks.scoring import tier_range
        s = self._signals(yes=("F1", "F2", "F3", "F4"), weak_open=("W1", "W2"))  # 12 points; W1, W2 still Unknown
        self.assertFalse(tier_range(s, "MM")["locked"])               # if they could still be asked: open
        self.assertTrue(tier_range(s, "MM", open_sids=set())["locked"])  # nothing left to ask about them: final

    def test_unknown_size_never_locks(self):
        from enrich.checks.scoring import tier_range
        self.assertFalse(tier_range(self._signals(yes=("F1", "F2", "F3", "F4", "F5")), None)["locked"])

    def test_locked_company_gets_no_phase2_questions_but_can_ask_all(self):
        from enrich.integrations import client_ai
        result = {"signals": self._signals(yes=("F1", "F2", "F3", "F4", "F5"), open_=("F11",)),
                  "facts": {"segment": "MM"}, "crawl": {"status": "live"}, "ai": {"prompts": ["fit/yc_or_accelerator"]}}
        store = mock.MagicMock()
        store.get_ai.return_value = {}
        self.assertEqual(client_ai._pending(store, "x.com", "signals", result), [])
        self.assertEqual(len(client_ai._pending(store, "x.com", "signals", result, all_questions=True)), 1)
        self.assertNotEqual(client_ai._pending(store, "x.com", "icp", {**result}), [])  # phase 1 never stops early

    def test_most_valuable_questions_first(self):
        from enrich.integrations import client_ai
        result = {"facts": {"segment": "MM"}}
        worth = {pid: client_ai._worth(pid, result) for pid in
                 ("fit/venture_backed_stage", "fit/yc_or_accelerator", "fit/banks_mercury_or_brex", "fields/vertical")}
        self.assertEqual(sorted(worth, key=lambda p: -worth[p])[:1], ["fit/venture_backed_stage"])  # high (3) first
        self.assertEqual(worth["fields/vertical"], 0)

    def test_helper_runs_on_haiku(self):
        from pathlib import Path
        self.assertIn("model: haiku", (Path(__file__).parent.parent / ".claude/agents/company-answerer.md").read_text())


class FormDHistory(unittest.TestCase):
    def _signals(self, details, name="Argon AI, Inc.", form_d=("2024-06-13",)):
        from enrich.checks.rules import extract
        sec = {"cik": "2026471", "name": name, "match": "strong", "basis": "legal name", "tickers": [], "exchanges": [],
               "state_of_incorporation": "DE", "form_d": list(form_d), "former_names": [], "business_city": "BROOKLYN",
               "business_state": "NY", "form_d_details": {"url": "https://www.sec.gov/x.xml", **details}}
        found = {"version": 0, "legal_name": None, "errors": {}, "complete": True, "SEC": sec}
        home = ("<html><body><h1>X</h1><p>X is a B2B SaaS platform. Plans from $49 per month.</p>"
                "<footer>Argon AI, Inc. 1 Main St, Brooklyn, NY 11211</footer></body></html>")
        record = {"domain": "x.com", "status": "live", "final_url": "https://x.com/", "headers": {},
                  "pages": {"home": "https://x.com/", "about": "https://x.com/about", "pricing": "https://x.com/pricing"}}
        pages = {"home": home, "about": "<p>About</p>", "pricing": "<p>Starter $49 per month.</p>"}
        with mock.patch("enrich.sources.directories.ENABLED", True):
            return extract("x.com", "Argon AI", "10", record, pages, [], "signals", lambda *a, **k: found)["signals"]

    def test_argon_like_filing_settles_b6_and_b7_no(self):
        s = self._signals({"year_of_incorporation": "2023", "entity_type": "Corporation", "jurisdiction": "DELAWARE",
                           "previous_names": ["Incyte Bio, Inc."]})
        self.assertEqual(s["B7"]["value"], "no")
        self.assertIn("incorporated in 2023", s["B7"]["evidence"])
        self.assertEqual(s["B6"]["value"], "no")
        self.assertIn("previous name Incyte Bio, Inc. was also a corporation", s["B6"]["evidence"])

    def test_llc_previous_name_is_a_conversion(self):
        from datetime import date, timedelta
        recent = (date.today() - timedelta(days=60)).isoformat()
        s = self._signals({"year_of_incorporation": "2021", "entity_type": "Corporation", "jurisdiction": "DELAWARE",
                           "previous_names": ["Argon AI LLC"]}, form_d=(recent,))
        self.assertEqual(s["B6"]["value"], "yes")

    def test_last_year_is_not_decided(self):
        from datetime import date
        s = self._signals({"year_of_incorporation": str(date.today().year - 1), "entity_type": "Corporation",
                           "previous_names": []})
        self.assertEqual(s["B7"]["value"], "unknown")  # may or may not be within 12 months


class FounderSearches(unittest.TestCase):
    def test_linkedin_post_search_uses_a_founder_when_known(self):
        from enrich.ai import prompts as P
        pid = "buying/new_board_member_or_investor_updates"
        plain = P.queries(pid, P.variables("Argon AI", "argon-ai.com"))[-1]
        named = P.queries(pid, P.variables("Argon AI", "argon-ai.com", founders=["Samy Danesh"]))[-1]
        self.assertTrue(plain.startswith('site:linkedin.com/posts "Argon AI" '))
        self.assertIn('("Argon AI" OR "Samy Danesh")', named)

    def test_founders_come_from_the_team_list(self):
        from enrich.checks.rules import SIGNALS, UNKNOWN
        from enrich.providers.apply import apply
        from enrich.providers.run import people_record
        team = [{"name": "Samy Danesh", "title": "Co-Founder, CEO"}, {"name": "Bo", "title": "Engineer"},
                {"name": "Cyrus Jia", "title": "Co-Founder"}]
        result = {"domain": "x.com", "signals": {k: {"value": UNKNOWN, "evidence": "", "url": "", "note": "", "method": "code"}
                                                 for k in SIGNALS}, "crawl": {}, "facts": {}}
        out = apply(result, {"prospeo": {"people": people_record(team, team)}}, "signals")
        self.assertEqual(out["facts"]["founders"], ["Samy Danesh", "Cyrus Jia"])


class Opposites(unittest.TestCase):
    def test_w1_is_no_when_f5_is_proven(self):
        from enrich.checks.rules import SIGNALS
        from enrich.engine.layers import RULES, decided_by
        from enrich.engine.resolve import derive
        s = {k: {"value": "unknown", "evidence": "", "url": "", "note": "", "method": "code"} for k in SIGNALS}
        s["F5"] = {"value": "yes", "evidence": "Out of the box software", "url": "https://x.com/", "note": "", "method": "ai"}
        r = {"signals": s}
        derive(r)
        self.assertEqual(s["W1"]["value"], "no")
        self.assertIn("Startup selling nationally is Yes", s["W1"]["evidence"])
        self.assertIs(decided_by(s["W1"]), RULES)

    def test_never_overrides_a_decided_answer_or_an_open_f5(self):
        from enrich.checks.rules import SIGNALS
        from enrich.engine.resolve import derive
        s = {k: {"value": "unknown", "evidence": "", "url": "", "note": "", "method": "code"} for k in SIGNALS}
        derive({"signals": s})
        self.assertEqual(s["W1"]["value"], "unknown")  # F5 still open: nothing derived
        s["F5"]["value"], s["W1"]["value"] = "yes", "yes"
        derive({"signals": s})
        self.assertEqual(s["W1"]["value"], "yes")  # already decided by its own proof: left alone


class LongPageExcerpt(unittest.TestCase):
    def test_decisive_sentences_past_the_cut_are_kept(self):
        from enrich.checks.rules import excerpt
        text = "Intro sentence about the service. " * 200 + "All payments shall be in US dollars. " + "Filler text here. " * 100
        out = excerpt(text, 4000)
        self.assertLessEqual(len(out), 4000)
        self.assertIn("All payments shall be in US dollars.", out)
        self.assertTrue(out.startswith("Intro sentence"))

    def test_short_pages_are_whole(self):
        from enrich.checks.rules import excerpt
        self.assertEqual(excerpt("Short terms. Payments in USD.", 4000), "Short terms. Payments in USD.")


class WebOnClue(unittest.TestCase):
    def test_rarely_public_data_points_search_only_on_a_clue(self):
        from enrich.checks import datapoints as d
        pid = "weak/accountant_controls_tools"  # W11
        self.assertEqual(d.step("W11", "ai_web")["run"], "if_clue")
        self.assertFalse(d.web_after(pid, {"answer": "unknown"}, True))
        self.assertTrue(d.web_after(pid, {"answer": "yes", "confidence": "low"}, True))
        self.assertTrue(d.web_after(pid, {"answer": "unknown"}, True, clue=True))
        self.assertFalse(d.web_after(pid, {"answer": "yes"}, False))  # already decided
        self.assertEqual(d.step("B1", "ai_web")["run"], "if_open")      # others unchanged


if __name__ == "__main__":
    unittest.main()
