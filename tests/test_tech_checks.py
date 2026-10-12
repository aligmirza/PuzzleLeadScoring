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


if __name__ == "__main__":
    unittest.main()
