"""Payment gateways in code (enrich/sources/payments.py), the crawler's scan, and F2 using it.

    .venv/bin/python -m unittest discover -s tests -v

Offline: downloads are faked.
"""
import asyncio
import unittest

from enrich.sources import payments
from enrich.sources.crawl import Crawler, Fetched


def gateways(text):
    return {(f["gateway"], f["how"]) for f in payments.detect(text, "https://x.com/")}


class Detect(unittest.TestCase):
    def test_every_stripe_method(self):
        cases = {
            '<script src="https://js.stripe.com/v3/"></script>': "Stripe.js loaded",
            "const stripe = Stripe('pk_live_51Habcdefghijklmnop');": "Stripe initialised with a publishable key",
            "const p = loadStripe(process.env.KEY)": "loadStripe() in the app code",
            "const el = stripe.elements({clientSecret})": "Stripe Elements / Payment Element",
            '<stripe-pricing-table pricing-table-id="prctbl_1"></stripe-pricing-table>': "Stripe pricing table or buy button",
            '<a href="https://checkout.stripe.com/c/pay/cs_live_a1">Buy</a>': "Stripe Checkout link",
            '<a href="https://buy.stripe.com/14k5kD">Subscribe</a>': "Stripe Payment Link",
            '<a href="https://billing.stripe.com/p/login/abc">Manage billing</a>': "Stripe customer billing portal",
            '<a href="https://invoice.stripe.com/i/acct_1/inv">Pay invoice</a>': "Stripe hosted invoice",
            '<iframe src="https://m.stripe.network/inner.html"></iframe>': "Stripe fraud-detection frame",
            'fetch("https://api.stripe.com/v1/customers")': "Stripe API called from code",
            '<button data-stripe-key="x">Pay</button>': "data-stripe attributes",
        }
        for text, how in cases.items():
            self.assertIn(("Stripe", how), gateways(text), text)

    def test_other_gateways(self):
        self.assertIn(("Paddle", "Paddle.js loaded"), gateways('<script src="https://cdn.paddle.com/paddle/v2/paddle.js">'))
        self.assertIn(("Chargebee", "Chargebee.js loaded"), gateways('<script src="https://js.chargebee.com/v2/chargebee.js">'))
        self.assertIn(("PayPal", "PayPal SDK loaded"), gateways('<script src="https://www.paypal.com/sdk/js?client-id=x">'))
        self.assertIn(("Braintree", "Braintree loaded"), gateways('<script src="https://js.braintreegateway.com/web/3/js/client.min.js">'))

    def test_text_and_other_keys_are_not_code(self):
        self.assertEqual(gateways("All payment data is stored by Stripe. See https://stripe.com/privacy"), set())
        clerk = '<script data-clerk-publishable-key="pk_live_Y2xlcmsuYXJnb24tYWkuY29tJA" src="https://clerk.x.com/npm/clerk.js">'
        self.assertEqual(gateways(clerk), set())

    def test_keys_are_never_stored(self):
        found = payments.detect("const stripe = Stripe('pk_live_51HsecretSecretSecret');", "https://x.com/")
        self.assertTrue(found)
        self.assertNotIn("51HsecretSecretSecret", found[0]["snippet"])

    def test_own_scripts_and_app_pages(self):
        html = ('<script src="/_next/static/chunks/main-1.js"></script><script src="https://cdn.x.com/app.js"></script>'
                '<script src="https://www.googletagmanager.com/gtm.js"></script>'
                '<a href="https://app.x.com/sign-in">Log in</a><a href="/login">Login</a><a href="https://other.com/login">x</a>')
        scripts = payments.own_scripts(html, "https://x.com/", {"x.com"})
        self.assertEqual(set(scripts), {"https://x.com/_next/static/chunks/main-1.js", "https://cdn.x.com/app.js"})
        self.assertFalse(any("googletagmanager" in s for s in scripts))
        self.assertEqual(payments.app_pages(html, "https://x.com/", {"x.com"}),
                         ["https://app.x.com/sign-in", "https://x.com/login"])


class CrawlerScan(unittest.TestCase):
    def test_finds_stripe_in_the_app_bundle(self):
        site = {
            "https://x.com/robots.txt": (404, ""),
            "https://app.x.com/sign-in": (200, '<script src="/_next/static/chunks/app-9.js"></script>'),
            "https://app.x.com/_next/static/chunks/app-9.js": (200, "var s=loadStripe(e.NEXT_PUBLIC_KEY);"),
            "https://x.com/static/site.js": (200, "console.log('hi')"),
        }
        requested = []

        async def fake_fetch(url):
            requested.append(url)
            status, body = site.get(url, (404, ""))
            return Fetched(url, status, "text/html", body, {}), None

        async def run():
            c = Crawler()
            c.fetch = fake_fetch
            record = {"domain": "x.com", "pages": {"home": "https://x.com/"}}
            home = '<script src="/static/site.js"></script><a href="https://app.x.com/sign-in">Log in</a>'
            await c.payment_scan("x.com", "https://x.com/", {"home": home}, record)
            await c.close()
            return record

        record = asyncio.run(run())
        stripe = record["payment_code"]["gateways"]["Stripe"]
        self.assertEqual(stripe["methods"], ["loadStripe() in the app code"])
        self.assertEqual(stripe["where"], "https://app.x.com/_next/static/chunks/app-9.js")
        self.assertLessEqual(len(requested), 5)


class StripeRule(unittest.TestCase):
    def _f2(self, record_extra=None, html=""):
        from enrich.checks.rules import extract
        home = ("<html><body><h1>X</h1><p>X is a B2B SaaS platform for invoices. Plans from $49 per month.</p>"
                + html + "<footer>X, Inc. 1 Main St, San Francisco, CA 94105</footer></body></html>")
        privacy = "<html><body><p>All payment data is stored by Stripe.</p></body></html>"
        record = {"domain": "x.com", "status": "live", "final_url": "https://x.com/", "headers": {},
                  "pages": {"home": "https://x.com/", "privacy": "https://x.com/privacy", "about": "https://x.com/about"},
                  **(record_extra or {})}
        pages = {"home": home, "privacy": privacy, "about": "<html><body><p>About X, a team of 10.</p></body></html>"}
        return extract("x.com", "X", "10", record, pages, [], "signals")["signals"]["F2"]

    def test_code_beats_text(self):
        f2 = self._f2(html='<script src="https://js.stripe.com/v3/"></script>')
        self.assertEqual(f2["value"], "yes")
        self.assertEqual(f2["note"], "found in code")
        self.assertIn("Stripe.js loaded", f2["evidence"])
        self.assertEqual(f2["url"], "https://x.com/")

    def test_crawl_scan_result_is_used(self):
        scan = {"gateways": {"Stripe": {"methods": ["loadStripe() in the app code"], "snippet": "loadStripe(",
                                        "where": "https://app.x.com/app.js"}}, "checked": ["https://x.com/"]}
        f2 = self._f2({"payment_code": scan})
        self.assertEqual(f2["note"], "found in code")
        self.assertEqual(f2["url"], "https://app.x.com/app.js")

    def test_text_only_says_so(self):
        f2 = self._f2({"payment_code": {"gateways": {}, "checked": ["a", "b", "c"]}})
        self.assertEqual(f2["value"], "yes")
        self.assertEqual(f2["note"], "stated on the site, not seen in code (3 pages and scripts checked)")


class QuickBooksJobPosts(unittest.TestCase):
    JOBS = [{"title": "Backend Engineer", "location": "New York", "description": "Build search. Python, Postgres.", "remote": False},
            {"title": "Founding Member", "location": "New York", "description": "Own customer relationships.", "remote": False}]

    def _run(self, jobs, record_extra=None):
        from enrich.checks.rules import extract
        record = {"domain": "x.com", "status": "live", "final_url": "https://x.com/", "headers": {},
                  "pages": {"home": "https://x.com/", "about": "https://x.com/about", "pricing": "https://x.com/pricing"},
                  "ats": "ashby", "ats_slug": "x", "jobs_count": len(jobs), **(record_extra or {})}
        pages = {"home": "<html><body><h1>X</h1><p>X is a B2B SaaS platform. Plans from $49 per month.</p>"
                         "<footer>X, Inc. 1 Main St, San Francisco, CA 94105</footer></body></html>",
                 "about": "<html><body><p>About X, a team of 10.</p></body></html>",
                 "pricing": "<html><body><p>Starter $49 per month.</p></body></html>"}
        return extract("x.com", "X", "10", record, pages, jobs, "signals")

    def test_nothing_found_says_which_job_posts_were_read(self):
        f4 = self._run(self.JOBS)["signals"]["F4"]
        self.assertEqual(f4["value"], "unknown")
        self.assertIn("no QuickBooks in 2 job posts on ashby (Backend Engineer; Founding Member)", f4["note"])
        self.assertEqual(f4["url"], "https://jobs.ashbyhq.com/x")

    def test_job_post_asking_for_quickbooks_is_yes(self):
        jobs = self.JOBS + [{"title": "Office Manager", "location": "NY", "remote": False,
                             "description": "Maintain our books in QuickBooks Online and run payroll."}]
        f4 = self._run(jobs)["signals"]["F4"]
        self.assertEqual(f4["value"], "yes")
        self.assertIn("Office Manager", f4["evidence"])

    def test_the_ai_reads_every_job_post(self):
        r = self._run(self.JOBS)
        self.assertIn("fit/uses_quickbooks_online", r["ai"]["prompts"])
        posts = r["ai_context"].get("job_posts", "")
        self.assertIn("Backend Engineer (New York): Build search.", posts)
        self.assertIn("Founding Member (New York): Own customer relationships.", posts)


class NoPricingPage(unittest.TestCase):
    def _f8(self, home_body, extra_pages=None, record_extra=None):
        from enrich.checks.rules import extract
        pages = {"home": f"<html><body><h1>X</h1>{home_body}<footer>X, Inc. 1 Main St, San Francisco, CA 94105</footer></body></html>",
                 "about": "<html><body><p>About X, AI software for pharma teams.</p></body></html>", **(extra_pages or {})}
        record = {"domain": "x.com", "status": "live", "final_url": "https://x.com/", "headers": {}, "robots_blocked": [],
                  "pages": {k: f"https://x.com/{'' if k == 'home' else k}" for k in pages}, **(record_extra or {})}
        return extract("x.com", "X", "10", record, pages, [], "signals")["signals"]["F8"]

    def test_contact_sales_only_and_no_pricing_page_is_no(self):
        f8 = self._f8("<p>AI workspace for pharma teams. Contact Sales</p>")
        self.assertEqual(f8["value"], "no")
        self.assertIn("no pricing page", f8["evidence"])
        self.assertIn("the only way to buy is 'Contact Sales'", f8["evidence"])

    def test_no_pricing_words_anywhere_is_no(self):
        f8 = self._f8("<p>AI workspace for pharma teams. Sign up today.</p>")
        self.assertEqual(f8["value"], "no")
        self.assertIn("no prices or pricing words on 2 pages", f8["evidence"])

    def test_pricing_words_without_prices_is_a_clue(self):
        f8 = self._f8("<p>AI workspace for pharma teams. Start your free trial.</p>")
        self.assertEqual(f8["value"], "check")

    def test_prices_elsewhere_are_a_clue_not_a_no(self):
        f8 = self._f8("<p>AI workspace. Team plan $49 per month. Contact Sales for Enterprise.</p>")
        self.assertEqual(f8["value"], "check")

    def test_pricing_page_blocked_by_robots_stays_open(self):
        f8 = self._f8("<p>AI workspace. Contact Sales</p>", record_extra={"robots_blocked": ["https://x.com/pricing"]})
        self.assertEqual(f8["value"], "unknown")


if __name__ == "__main__":
    unittest.main()
