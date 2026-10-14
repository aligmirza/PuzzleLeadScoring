"""The parallel and faster paths: same answers, less waiting.

    .venv/bin/python -m unittest discover -s tests -v
"""
import asyncio
import unittest
from unittest import mock

from enrich.sources import jobboards, payments


class FastDetectors(unittest.TestCase):
    def test_payment_calls_without_the_word_stripe_still_count(self):
        found = payments.detect("x.confirmCardPayment(secret,{payment_method:e})", "app.js")
        self.assertEqual(found[0]["gateway"], "Stripe")

    def test_nothing_to_find_in_a_bundle_without_any_gateway(self):
        self.assertEqual(payments.detect("a" * 2_000_000 + "function(){return 1}", "big.js"), [])

    def test_board_found_inside_a_huge_page(self):
        html = "x" * 500_000 + '<a href="https://acme.bamboohr.com/careers">Jobs</a>' + "y9" * 200_000
        self.assertEqual(jobboards.detect(html), [("bamboohr", {"slug": "acme"})])

    def test_board_slug_is_read_before_the_domain(self):
        self.assertEqual(jobboards.detect('<a href="https://acme-inc.jobs.personio.de/">'),
                         [("personio", {"slug": "acme-inc", "tld": "de"})])


class CallGate(unittest.TestCase):
    def test_grows_while_calls_succeed_and_halves_on_a_rate_limit(self):
        from enrich.ai.runner import CallGate

        async def run():
            g = CallGate(4, 6)
            for _ in range(20):
                async with g:
                    pass
            grown = g.limit
            await g.slow_down()
            return grown, g.limit
        self.assertEqual(asyncio.run(run()), (5, 2))

    def test_never_more_at_once_than_the_limit(self):
        from enrich.ai.runner import CallGate
        busy = {"now": 0, "most": 0}

        async def run():
            g = CallGate(3, 3)

            async def call():
                async with g:
                    busy["now"] += 1
                    busy["most"] = max(busy["most"], busy["now"])
                    await asyncio.sleep(0.01)
                    busy["now"] -= 1
            await asyncio.gather(*(call() for _ in range(12)))
        asyncio.run(run())
        self.assertEqual(busy["most"], 3)


class BothPhases(unittest.TestCase):
    def test_phase_2_only_for_companies_that_pass_and_right_away(self):
        from enrich.integrations import service
        order = []

        def p1_factory(*a):
            async def h(row):
                order.append(("p1", row["domain"]))
                await asyncio.sleep(0.05 if row["domain"] == "slow.com" else 0)
                return {"domain": row["domain"], "icp_verdict": "No" if row["domain"] == "no.com" else "Yes"}
            return h

        def p2_factory(*a):
            async def h(row):
                order.append(("p2", row["domain"]))
                return {}
            return h

        class FakeCrawler:
            def __init__(self, **k):
                pass

            async def close(self):
                pass
        rows = [{"domain": d, "company": "", "employees": ""} for d in ("slow.com", "fast.com", "no.com")]
        with mock.patch.object(service.phase1_icp, "handler", p1_factory), \
                mock.patch.object(service.phase2_signals, "handler", p2_factory), \
                mock.patch("enrich.sources.crawl.Crawler", FakeCrawler):
            asyncio.run(service.both_phases(mock.MagicMock(), rows, True, 3, False))
        self.assertNotIn(("p2", "no.com"), order)
        self.assertLess(order.index(("p2", "fast.com")), order.index(("p2", "slow.com")))  # no waiting for slow.com


class CheckMany(unittest.TestCase):
    def test_limits_and_bad_domains(self):
        from enrich.integrations import service
        with self.assertRaises(ValueError):
            asyncio.run(service.check_many([f"c{i}.com" for i in range(service.MAX_BATCH + 1)]))

        async def fake_rows(*a, **k):
            return None
        with mock.patch.object(service, "run_rows", fake_rows), \
                mock.patch.object(service, "findings", lambda store, d, c="", e="": {"domain": d}):
            out = asyncio.run(service.check_many(["acme.com", "acme.com", "not a domain"]))
        self.assertEqual([c["domain"] for c in out["companies"]], ["acme.com"])
        self.assertEqual(out["bad_domains"], ["not a domain"])


if __name__ == "__main__":
    unittest.main()
