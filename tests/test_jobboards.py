"""Open jobs from job boards and careers pages (enrich/sources/jobboards.py) and the crawler step that uses them.

    .venv/bin/python -m unittest discover -s tests -v

Offline: board answers are samples in each board's real format (checked live on 2026-10-14).
"""
import asyncio
import json
import unittest

from enrich.sources import jobboards as jb


class Detect(unittest.TestCase):
    def test_each_board_from_its_links(self):
        cases = {
            '<a href="https://cibc.wd3.myworkdayjobs.com/en-US/search/jobs">': ("workday", {"tenant": "cibc", "wd": "wd3", "site": "search"}),
            '<script src="https://allhandsandhearts.bamboohr.com/js/embed.js">': ("bamboohr", {"slug": "allhandsandhearts"}),
            '<a href="https://app.dover.com/dover/careers/346d422a-8246-4c02-8ef8-95fe38db5abf">': ("dover", {"slug": "346d422a-8246-4c02-8ef8-95fe38db5abf"}),
            '<a href="https://jobs.ashbyhq.com/argon-ai">': ("ashby", {"slug": "argon-ai"}),
            '<a href="https://ats.rippling.com/acme/jobs">': ("rippling", {"slug": "acme"}),
            '<a href="https://jobs.smartrecruiters.com/AcmeInc">': ("smartrecruiters", {"slug": "AcmeInc"}),
            '<a href="https://acme.recruitee.com/">': ("recruitee", {"slug": "acme"}),
            '<a href="https://acme.teamtailor.com/jobs">': ("teamtailor", {"slug": "acme"}),
            '<a href="https://acme.jobs.personio.de/">': ("personio", {"slug": "acme", "tld": "de"}),
            '<a href="https://acme.breezy.hr/">': ("breezy", {"slug": "acme"}),
        }
        for html, want in cases.items():
            self.assertEqual(jb.detect(html)[0], want, html)

    def test_marketing_links_are_not_boards(self):
        self.assertEqual(jb.detect('<a href="https://www.bamboohr.com/">HR software</a>'
                                   '<img src="https://cdn.site/x/bamboohr.png">'), [])

    def test_public_links(self):
        self.assertEqual(jb.public_url("workday", {"tenant": "cibc", "wd": "wd3", "site": "search"}),
                         "https://cibc.wd3.myworkdayjobs.com/search")
        self.assertEqual(jb.request("workday", {"tenant": "cibc", "wd": "wd3", "site": "search"})[0], "POST")


class Parse(unittest.TestCase):
    def test_json_boards(self):
        samples = {
            "workday": ({"tenant": "cibc", "wd": "wd3", "site": "search"},
                        {"total": 1, "jobPostings": [{"title": "Financial Controller", "locationsText": "Toronto, ON",
                                                      "externalPath": "/job/Toronto/Controller_1"}]}),
            "bamboohr": ({"slug": "acme"}, {"meta": {"totalCount": 1}, "result": [
                {"id": "766", "jobOpeningName": "PROJECT MANAGER", "location": {"city": "Austin", "state": "TX"}, "isRemote": None}]}),
            "dover": ({"slug": "x"}, {"count": 1, "results": [{"title": "Customer Success Manager",
                                                               "locations": [{"location_type": "REMOTE", "name": "Remote, US"}]}]}),
            "rippling": ({"slug": "acme"}, {"items": [{"name": "Business Operations Manager", "url": "https://ats.rippling.com/acme/jobs/1",
                                                       "locations": [{"name": "New York, NY"}]}]}),
            "smartrecruiters": ({"slug": "acme"}, {"content": [{"name": "Accountant", "location": {"city": "Denver", "remote": False}}]}),
            "recruitee": ({"slug": "acme"}, {"offers": [{"title": "Head of Finance", "location": "Berlin", "remote": True,
                                                         "description": "<p>Own the books</p>"}]}),
            "breezy": ({"slug": "acme"}, [{"name": "Bookkeeper", "location": {"name": "Remote", "is_remote": True}}]),
        }
        for board, (params, data) in samples.items():
            jobs = jb.parse(board, params, json.dumps(data))
            self.assertEqual(len(jobs), 1, board)
            self.assertTrue(jobs[0]["title"], board)
        self.assertEqual(jb.parse("workday", samples["workday"][0], json.dumps(samples["workday"][1]))[0]["url"],
                         "https://cibc.wd3.myworkdayjobs.com/search/job/Toronto/Controller_1")
        self.assertTrue(jb.parse("dover", {}, json.dumps(samples["dover"][1]))[0]["remote"])
        self.assertEqual(jb.parse("recruitee", {}, json.dumps(samples["recruitee"][1]))[0]["description"], "Own the books")

    def test_xml_boards(self):
        rss = ('<?xml version="1.0"?><rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel>'
               "<item><title>Finance Manager</title><link>https://acme.teamtailor.com/jobs/1</link>"
               "<description>&lt;p&gt;Run payroll in Gusto&lt;/p&gt;</description></item></channel></rss>")
        job = jb.parse("teamtailor", {"slug": "acme"}, rss)[0]
        self.assertEqual((job["title"], job["description"]), ("Finance Manager", "Run payroll in Gusto"))
        xml = ("<?xml version='1.0'?><workzag-jobs><position><name>Controller</name><office>Munich</office>"
               "<jobDescriptions><jobDescription><value>&lt;p&gt;Close the books monthly&lt;/p&gt;</value></jobDescription>"
               "</jobDescriptions></position></workzag-jobs>")
        job = jb.parse("personio", {"slug": "acme"}, xml)[0]
        self.assertEqual((job["title"], job["location"], job["description"]), ("Controller", "Munich", "Close the books monthly"))

    def test_an_error_page_is_not_an_empty_board(self):
        self.assertIsNone(jb.parse("bamboohr", {}, "<html>Not found</html>"))
        self.assertEqual(jb.parse("dover", {}, json.dumps({"count": 0, "results": []})), [])


class CareersPage(unittest.TestCase):
    URL = "https://acme.com/careers"

    def test_schema_org_job_postings(self):
        html = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Staff Accountant",'
                '"jobLocation":{"address":{"addressLocality":"Austin","addressRegion":"TX"}},'
                '"description":"<p>Reconcile accounts</p>"}</script>')
        job = jb.careers_page_jobs(html, self.URL)[0]
        self.assertEqual((job["title"], job["location"], job["description"]), ("Staff Accountant", "Austin, TX", "Reconcile accounts"))

    def test_job_links_and_card_text(self):
        html = ('<a href="/careers/software-engineer">Software Engineer San Francisco In Person Full-time View role</a>'
                '<a href="/careers/ops-lead">Operations Lead Remote</a>'
                '<a href="/careers">View all open positions</a><a href="/careers/benefits">Our benefits</a>'
                '<a href="https://other.com/jobs/designer">Product Designer</a>')
        jobs = jb.careers_page_jobs(html, self.URL)
        self.assertEqual([j["title"] for j in jobs], ["Software Engineer San Francisco", "Operations Lead"])
        self.assertEqual(jobs[0]["location"], "in person")
        self.assertTrue(jobs[1]["remote"])

    def test_titles_written_as_headings(self):
        html = "<h2>Join us</h2><h3>Financial Controller</h3><h3>Our culture</h3><h4>Geotechnical Applications Engineer</h4>"
        self.assertEqual([j["title"] for j in jb.careers_page_jobs(html, self.URL)],
                         ["Financial Controller", "Geotechnical Applications Engineer"])

    def test_no_roles_listed(self):
        self.assertEqual(jb.careers_page_jobs("<h2>We're not hiring right now</h2><p>Check back soon.</p>", self.URL), [])


class CrawlerStep(unittest.TestCase):
    def test_board_first_then_careers_page(self):
        from enrich.sources.crawl import Crawler

        class Resp:
            def __init__(self, status, text):
                self.status_code, self.text = status, text

        async def run(board_answer):
            c = Crawler()

            async def get(url, **_):
                return Resp(*board_answer)
            c.client.get = get
            record = {"pages": {"careers": "https://acme.com/careers"}}
            pages = {"home": '<a href="https://acme.breezy.hr/">Jobs</a>',
                     "careers": "<h3>Financial Controller</h3>"}
            jobs = await c.find_jobs("https://acme.com/", pages, record)
            await c.close()
            return jobs, record

        jobs, rec = asyncio.run(run((200, json.dumps([{"name": "Bookkeeper", "location": {"name": "Remote", "is_remote": True}}]))))
        self.assertEqual((rec["ats"], rec["jobs_count"], rec["jobs_url"], jobs[0]["title"]),
                         ("breezy", 1, "https://acme.breezy.hr", "Bookkeeper"))
        jobs, rec = asyncio.run(run((404, "<html>gone</html>")))  # board doesn't answer: the careers page
        self.assertEqual((rec["ats"], jobs[0]["title"]), ("careers page", "Financial Controller"))


class StructuredAddress(unittest.TestCase):
    def test_country_as_an_object(self):
        from enrich.checks.rules import extract
        home = ('<html><head><script type="application/ld+json">{"@type":"Organization","address":{"@type":"PostalAddress",'
                '"addressCountry":{"@type":"Country","name":"United States"}}}</script></head><body><h1>X</h1>'
                "<p>X is a B2B SaaS platform.</p></body></html>")
        record = {"domain": "x.com", "status": "live", "final_url": "https://x.com/", "headers": {}, "pages": {"home": "https://x.com/"}}
        s = extract("x.com", "X", "10", record, {"home": home}, [], "icp")["signals"]
        self.assertEqual(s["M1"]["value"], "yes")


class RenderJavaScriptPages(unittest.TestCase):
    SHELL = ("<html><body>" + "<div class='nav'></div>" * 120 + "<a href='/about'>About</a> Announcements "
             "Loading blog... <footer>© 2026 Argon AI.</footer></body></html>")
    FULL = ("<html><body><h1>Announcements</h1>" + "".join(
        f"<article><h2>Post {i}: Argon Intelligence - CI Solution</h2><p>{'Launch notes. ' * 20}</p></article>" for i in range(4))
        + "</body></html>")

    def test_placeholder_pages_are_unrendered(self):
        from enrich.sources.crawl import unrendered
        self.assertTrue(unrendered(self.SHELL))
        self.assertFalse(unrendered(self.FULL))
        self.assertFalse(unrendered("<html><body><p>Short page, no placeholder.</p></body></html>"))

    def test_rendered_copy_is_kept_when_it_has_more_text(self):
        from enrich.sources.crawl import Crawler, Fetched

        async def run(browser_html):
            c = Crawler()

            async def fetch(url):
                return Fetched(url, 200, "text/html", browser_html, {}), None
            c.browser.fetch = fetch
            record = {}
            got = await c.render_if_needed("blog", Fetched("https://x.com/blog", 200, "text/html", self.SHELL, {}), record)
            await c.close()
            return got, record

        got, rec = asyncio.run(run(self.FULL))
        self.assertIn("Argon Intelligence", got.text)
        self.assertEqual(rec["rendered_pages"], ["blog"])
        got, rec = asyncio.run(run(self.SHELL))  # the browser didn't help: keep the plain copy
        self.assertNotIn("rendered_pages", rec)


if __name__ == "__main__":
    unittest.main()
