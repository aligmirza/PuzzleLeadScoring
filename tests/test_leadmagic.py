"""LeadMagic person enrichment (enrich/providers/leadmagic.py, run.emails_step, the find_emails MCP tool).

    .venv/bin/python -m unittest discover -s tests -v

HTTP is mocked: nothing is bought.
"""
import asyncio
import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from enrich.core.store import Store
from enrich.integrations import mcp_server
from enrich.providers import leadmagic
from enrich.providers import run as prun

TEAM = [{"name": "Ann Lee", "title": "Founder & CEO", "linkedin": "https://linkedin.com/in/annlee", "person_id": "p1"},
        {"name": "Bo Diaz", "title": "Controller", "linkedin": "https://linkedin.com/in/bodiaz", "person_id": "p2"},
        {"name": "Cy Ray", "title": "Engineer", "linkedin": "", "person_id": "p3"}]


class FakeResponse:
    def __init__(self, status, data, headers=None):
        self.status_code, self._data, self.headers, self.text = status, data, headers or {}, str(data)

    def json(self):
        return self._data


class Http:
    """Stands in for httpx.Client; answers by path and records each call."""
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        ans = self.answers[url.split("leadmagic.io")[1]]
        return ans(json) if callable(ans) else ans

    def get(self, url, headers=None):
        self.calls.append((url, None, headers))
        return self.answers[url.split("leadmagic.io")[1]]


def email_answer(body):
    if body.get("first_name") == "Ann":
        return FakeResponse(200, {"email": "ann@acme.com", "status": "valid", "credits_consumed": 1})
    return FakeResponse(200, {"email": None, "status": None, "credits_consumed": 0, "message": "No email found"})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.http = Http({"/v1/people/email-finder": email_answer,
                          "/v1/people/mobile-finder": FakeResponse(200, {"mobile_number": "+1-555-0100", "credits_consumed": 5}),
                          "/v1/credits": FakeResponse(200, {"credits": 250})})
        for p in (mock.patch.dict(os.environ, {"LEADMAGIC_API_KEY": "lm_test", "LEADMAGIC_MONTHLY_CREDITS": "",
                                                   "PROSPEO_API_KEY": ""}),
                  mock.patch("enrich.providers.prospeo.CACHE", self.tmp),
                  mock.patch("enrich.providers.prospeo.CACHE_DB", self.tmp / "providers.sqlite"),
                  mock.patch.object(leadmagic.httpx, "Client", self.http),
                  mock.patch.object(leadmagic.ratelimit, "wait", lambda *a: None)):
            p.start()
            self.addCleanup(p.stop)


class Client(Base):
    def test_finds_an_email_and_sends_the_key(self):
        got, credits, cached = leadmagic.find_email("Ann Lee", "acme.com")
        self.assertEqual(got["email"], "ann@acme.com")
        self.assertEqual(credits, 1)
        url, body, headers = self.http.calls[-1]
        self.assertTrue(url.endswith("/v1/people/email-finder"))
        self.assertEqual(body, {"domain": "acme.com", "first_name": "Ann", "last_name": "Lee"})
        self.assertEqual(headers["X-API-Key"], "lm_test")

    def test_repeats_come_from_the_cache_for_free(self):
        leadmagic.find_email("Ann Lee", "acme.com")
        got, credits, cached = leadmagic.find_email("Ann Lee", "acme.com")
        self.assertTrue(cached)
        self.assertEqual(credits, 0)
        self.assertEqual(len(self.http.calls), 1)

    def test_not_found_and_other_domains_count_as_none(self):
        self.assertIsNone(leadmagic.find_email("Bo Diaz", "acme.com")[0])
        self.http.answers["/v1/people/email-finder"] = FakeResponse(
            200, {"email": "ann@other.com", "status": "valid", "domain_mismatch": True, "credits_consumed": 0})
        self.assertIsNone(leadmagic.find_email("Ann Smith", "acme.com")[0])

    def test_one_word_name_uses_the_linkedin_profile(self):
        leadmagic.find_email("Cher", "acme.com", "https://linkedin.com/in/cher")
        self.assertEqual(self.http.calls[-1][1], {"domain": "acme.com", "profile_url": "https://linkedin.com/in/cher"})
        self.assertEqual(leadmagic.find_email("Cher", "acme.com"), (None, 0.0, False))

    def test_out_of_credits_is_an_error(self):
        self.http.answers["/v1/people/email-finder"] = FakeResponse(402, {"code": "insufficient_credits"})
        with self.assertRaisesRegex(leadmagic.LeadMagicError, "no credits"):
            leadmagic.find_email("Dee Moe", "acme.com")

    def test_no_key(self):
        with mock.patch.dict(os.environ, {"LEADMAGIC_API_KEY": ""}):
            self.assertFalse(leadmagic.enabled())
            with self.assertRaises(leadmagic.LeadMagicError):
                leadmagic.find_email("Dee Moe", "acme.com")

    def test_credits_left(self):
        self.assertEqual(leadmagic.credits_left(), 250)


class EmailsStep(Base):
    def setUp(self):
        super().setUp()
        self.folder = self.tmp / "lst"
        (self.folder / "output").mkdir(parents=True)
        (self.folder / "input.csv").write_text("Domain,Company\nacme.com,Acme\n", encoding="utf-8")
        store = Store(self.folder)
        store.save_provider("acme.com", "prospeo", "people", prun.people_record(TEAM[:3], TEAM))
        store.save_provider("acme.com", "prospeo", "company", {"no_match": True})
        rows = [{"_domain": "acme.com", "ICP": "YES", "Lead score": "5", "Lead tier": "B"}]
        for p in (mock.patch.object(prun.final, "read_rows", return_value=rows),
                  mock.patch.object(prun, "log", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)

    def test_plan_spends_nothing(self):
        plan = prun.emails_plan(self.folder, per_company=3)
        self.assertEqual((plan["people"], plan["emails_to_find"], plan["credits_at_most"]), (3, 3, 3))
        self.assertEqual(prun.emails_plan(self.folder, 3, phones=True)["credits_at_most"], 3 + 2 * 5)  # Cy has no LinkedIn
        self.assertEqual(prun.emails_plan(self.folder, 3, decision_makers_only=True)["people"], 2)
        self.assertEqual(self.http.calls, [])

    def test_fills_emails_into_the_contacts_file_and_never_asks_twice(self):
        out = prun.emails_step(self.folder, per_company=2, assume_yes=True, max_credits=5)
        self.assertEqual((out["emails_found"], out["credits"]), (1, 1))
        with open(out["file"], newline="", encoding="utf-8") as f:
            rows = {r["Name"]: r for r in csv.DictReader(f)}
        self.assertEqual((rows["Ann Lee"]["Email"], rows["Ann Lee"]["Email source"]), ("ann@acme.com", "LeadMagic"))
        self.assertEqual(rows["Bo Diaz"]["Email"], "")
        calls = len(self.http.calls)
        self.assertEqual(prun.emails_plan(self.folder, per_company=2)["people"], 0)  # Bo was looked up: not again
        prun.emails_step(self.folder, per_company=2, assume_yes=True, max_credits=5)
        self.assertEqual(len(self.http.calls), calls)

    def test_prospeo_emails_are_kept_and_not_bought_again(self):
        team = [dict(TEAM[0], email="ann@acme.com", email_status="VERIFIED"), *TEAM[1:]]
        Store(self.folder).save_provider("acme.com", "prospeo", "people", prun.people_record(team[:1], team))
        self.assertEqual(prun.emails_plan(self.folder, per_company=2)["people"], 1)

    def test_mobiles(self):
        out = prun.emails_step(self.folder, per_company=1, phones=True, assume_yes=True, max_credits=10)
        self.assertEqual((out["mobiles_found"], out["credits"]), (1, 6))

    def test_cap_below_the_plan_is_refused(self):
        out = prun.emails_step(self.folder, per_company=3, assume_yes=True, max_credits=2)
        self.assertIn("above max_credits", out["refused"])
        self.assertEqual(self.http.calls, [])

    def test_monthly_cap(self):
        with mock.patch.dict(os.environ, {"LEADMAGIC_MONTHLY_CREDITS": "2"}), \
                mock.patch.object(prun, "_credits_this_month", return_value=0):
            out = prun.emails_step(self.folder, per_company=3, assume_yes=True, max_credits=10)
        self.assertIn("LEADMAGIC_MONTHLY_CREDITS", out["refused"])


class ThroughMCP(EmailsStep):
    def test_without_a_cap_it_only_plans(self):
        with mock.patch("enrich.integrations.mcp_server._list_folder", return_value=self.folder):
            r = asyncio.run(mcp_server.find_emails("lst"))
        self.assertTrue(r["estimate_only"])
        self.assertEqual(r["credits_at_most"], 3)
        self.assertEqual(r["leadmagic"]["credits_left"], 250)
        self.assertEqual([c for c in self.http.calls if "credits" not in c[0]], [])

    def test_runs_with_an_approved_cap(self):
        with mock.patch("enrich.integrations.mcp_server._list_folder", return_value=self.folder):
            r = asyncio.run(mcp_server.find_emails("lst", max_credits=3))
        self.assertEqual(r["emails_found"], 1)

    def test_tool_defaults_to_a_plan(self):
        tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
        self.assertEqual(tools["find_emails"].input_schema["properties"]["max_credits"].get("default"), 0)
        self.assertIs(tools["find_emails"].input_schema["properties"]["phones"].get("default"), False)


LM_COMPANY = {"company_domain": "acme.com", "company_name": "Acme", "company_website": "https://acme.com",
              "company_industry_linkedin": "Software Development", "linkedin_employee_count": 12,
              "hq_city": "Austin", "hq_state": "Texas", "hq_country": "United States", "hq_country_code": "US",
              "founded_year": 2022, "total_funding": 5500000, "last_funding_type": "Seed", "last_funding_date": "2025-08-02"}


def lm_person(first, last, title, fn="Engineering"):
    return {"contact_full_name": f"{first} {last}", "contact_first_name": first, "contact_last_name": last,
            "contact_job_title": title, "contact_job_function": fn, "company_domain": "acme.com",
            "contact_linkedin_url": f"https://linkedin.com/in/{first.lower()}", "contact_city": "Austin",
            "contact_state_code": "TX", "contact_country_code": "US", "company": LM_COMPANY}


class LeadMagicFirst(Base):
    """Puzzle has LeadMagic, not Prospeo: people, company data and the finance check come from LeadMagic."""
    TEAM = [lm_person("Bo", "Diaz", "Software Engineer"), lm_person("Ann", "Lee", "Co-Founder & CEO", "Executive"),
            lm_person("Cy", "Ray", "Designer", "Design")]

    def setUp(self):
        super().setUp()
        self.http.answers["/v3/people/search"] = lambda body: FakeResponse(
            200, {"people": [] if body.get("company_domain") in ("empty.com", "other.co") else
                  [dict(p, company_domain="other.com") for p in self.TEAM] if body.get("linkedin_url") else self.TEAM,
                  "credits_consumed": 1 if body.get("company_domain") in ("empty.com", "other.co") else 0})
        self.http.answers["/v3/companies/search"] = FakeResponse(200, {"found": True, "company": LM_COMPANY, "credits_consumed": 1})
        p = mock.patch.object(prun.prospeo, "_call", side_effect=AssertionError("Prospeo was called"))
        p.start()
        self.addCleanup(p.stop)

    def test_leadmagic_is_chosen_when_its_key_is_set(self):
        with mock.patch.dict(os.environ, {"PROSPEO_API_KEY": "pk_test"}):
            self.assertEqual(prun.source(), "leadmagic")
            with mock.patch.dict(os.environ, {"CONTACTS_PROVIDER": "prospeo"}):
                self.assertEqual(prun.source(), "prospeo")
        with mock.patch.dict(os.environ, {"LEADMAGIC_API_KEY": "", "PROSPEO_API_KEY": "pk_test"}):
            self.assertEqual(prun.source(), "prospeo")

    def test_team_search_ranks_the_buyer_first_and_brings_the_company(self):
        got = prun.fetch_one("acme.com")
        self.assertEqual(got["provider"], "leadmagic")
        self.assertEqual([p["name"] for p in got["people"]], ["Ann Lee", "Bo Diaz", "Cy Ray"])
        self.assertEqual(got["people"][0]["linkedin"], "https://linkedin.com/in/ann")
        self.assertEqual((got["company"]["employee_count"], got["company"]["location"]["country_code"]), (12, "US"))
        body = self.http.calls[-1][1]
        self.assertEqual(body, {"company_domain": "acme.com", "limit": 25, "include_company": True})
        self.assertFalse(any("companies/search" in c[0] for c in self.http.calls))  # no second call

    def test_finance_vps_are_decision_makers(self):
        for title in ("VP of Finance and Business Operations", "VP, Finance", "Vice President of Finance", "Controller"):
            self.assertTrue(prun.RE_DECISION.search(title), title)

    def test_large_company_searches_its_finance_department(self):
        prun.fetch_one("acme.com", "ENT")
        self.assertEqual(self.http.calls[-1][1]["people_filters"], {"contact_job_function": ["Finance"]})

    def test_nobody_found_by_website_searches_the_linkedin_page(self):
        self.http.answers["/v3/companies/search"] = FakeResponse(
            200, {"found": True, "company": dict(LM_COMPANY, linkedin_url="https://www.linkedin.com/company/acme"), "credits_consumed": 1})
        got = prun.fetch_one("other.co")  # LeadMagic files the people under other.com
        self.assertEqual(got["people"][0]["name"], "Ann Lee")
        self.assertEqual(self.http.calls[-1][1], {"linkedin_url": "https://www.linkedin.com/company/acme", "limit": 25})
        self.assertEqual(got["credits"], 2)  # the empty search and the company lookup

    def test_no_linkedin_page_keeps_the_company_record_only(self):
        got = prun.fetch_one("empty.com")
        self.assertEqual((got["people"], got["company"]["name"], got["credits"]), ([], "Acme", 2))

    def test_finance_check_and_proof_name_leadmagic(self):
        from enrich.checks.rules import SIGNALS, UNKNOWN
        from enrich.providers.apply import apply, headcount
        store = Store(self.tmp / "lst")
        prun.save_one(store, "acme.com", prun.fetch_one("acme.com"))
        bought = store.get_provider("acme.com")
        self.assertIn("leadmagic", bought)
        self.assertNotIn("prospeo", bought)
        self.assertTrue(prun.has_bought(store, "acme.com"))
        result = {"domain": "acme.com", "crawl": {}, "facts": {},
                  "signals": {k: {"value": UNKNOWN, "evidence": "", "url": "", "note": "", "method": "code"} for k in SIGNALS}}
        f13 = apply(result, bought, "signals")["signals"]["F13"]
        self.assertEqual(f13["value"], "yes")
        self.assertIn("3 people LeadMagic lists at acme.com", f13["evidence"])
        self.assertEqual(f13["url"], "https://leadmagic.io")
        self.assertEqual(headcount(bought)["source"], "LeadMagic company record")

    def test_people_step_finds_emails_with_leadmagic(self):
        folder = self.tmp / "lst2"
        (folder / "output").mkdir(parents=True)
        (folder / "input.csv").write_text("Domain\nacme.com\n", encoding="utf-8")
        rows = [{"_domain": "acme.com", "ICP": "YES", "Lead tier": "B"}]
        with mock.patch.object(prun.final, "read_rows", return_value=rows), mock.patch.object(prun.final, "write"), \
                mock.patch.object(prun, "_rewrite"), mock.patch.object(prun, "log", lambda *a, **k: None), \
                mock.patch.object(prun, "finance_open", return_value=["F13"]):
            plan = prun.people_plan(folder, emails=True, per_company=2)
            self.assertEqual((plan["provider"], plan["credits_at_most"]), ("LeadMagic", 27 + 2))
            out = prun.people_step(folder, emails=True, per_company=2, assume_yes=True, max_credits=30)
        with open(out["file"], newline="", encoding="utf-8") as f:
            ann = next(r for r in csv.DictReader(f) if r["Name"] == "Ann Lee")
        self.assertEqual((ann["Email"], ann["Email source"], ann["HQ"]), ("ann@acme.com", "LeadMagic", "Austin, Texas, United States"))


if __name__ == "__main__":
    unittest.main()
