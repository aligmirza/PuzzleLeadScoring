"""MCP: the attached model is the only AI; questions it can't answer stay open, OpenAI is never used.

    .venv/bin/python -m unittest discover -s tests -v

Offline: a synthetic company in a temporary lists folder; the OpenAI runner is replaced by a fake that records calls.
"""
import asyncio
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from enrich.core.store import Store
from enrich.integrations import client_ai
from enrich.pipeline.common import run_rules
from enrich.sources import directories

from test_dp_test import ABOUT, DOMAIN, HOME, PRICING


class FakeRunner:
    """Stands in for ai.runner.Runner: records which questions reach 'OpenAI' and saves a plain unknown answer."""
    calls: list = []

    def __init__(self, store, phase, **_):
        self.store, self.phase, self.c, self.cost = store, phase, Counter(), 0.0

    async def step1(self, domain, result, facts, pids):
        self._save(domain, pids, 1)

    async def step2(self, domain, result, facts, pids, step1):
        self._save(domain, pids, 2)
        self.c["web_searches"] += len(pids)

    def _save(self, domain, pids, step):
        for pid in pids:
            FakeRunner.calls.append((pid, step))
            self.store.save_ai(domain, self.phase, pid, step, {"answer": "unknown", "confidence": "low",
                                                               "evidence_quote": "", "reason": "fake"},
                               {"model": "fake-openai", "cost": 0.001})
            self.c["calls"] += 1
            self.cost += 0.001


class OpenAIFallback(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        lists = self.tmp / "lists"
        FakeRunner.calls = []
        for p in (mock.patch.object(client_ai, "LISTS", lists), mock.patch.object(directories, "ENABLED", False),
                  mock.patch.object(client_ai.shared, "put_ai"), mock.patch("enrich.core.runlog.log"),
                  mock.patch("enrich.ai.runner.Runner", FakeRunner)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        folder = lists / "prod"
        store = Store(folder)
        (folder / "input.csv").write_text(f"Company Name,Domain,Employees\nExampleSaaS,{DOMAIN},20\n")
        for kind, html in (("home", HOME), ("about", ABOUT), ("pricing", PRICING)):
            store.save_page(DOMAIN, kind, html)
        record = {"domain": DOMAIN, "status": "live", "error": None, "final_url": f"https://{DOMAIN}/",
                  "pages": {k: f"https://{DOMAIN}/{k}" for k in ("home", "about", "pricing")}, "headers": {},
                  "robots_blocked": [], "input": {"company": "ExampleSaaS", "employees": "20"}}
        store.save_site(DOMAIN, record)
        run_rules(store, {"domain": DOMAIN, "company": "ExampleSaaS", "employees": "20", "input": {}}, record, "icp")
        self.store = store
        q = client_ai.open_questions(DOMAIN, "prod", "icp")
        self.assertTrue(q["questions"], "the synthetic company should leave questions open")
        self.q = q["questions"][0]

    def test_questions_use_the_prompt_folder(self):
        from enrich.ai import prompts as P
        q = client_ai.open_questions(DOMAIN, "prod", "icp")
        first = q["questions"][0]
        self.assertTrue(Path(first["prompt_file"]).exists())
        result = self.store.get_result(DOMAIN, "icp")
        same = (P.build(first["id"], "ExampleSaaS", DOMAIN, client_ai.known_facts(result), result.get("ai_context", {}))
                if first["answer_from"] == "pages" else
                P.build_search(first["id"], "ExampleSaaS", DOMAIN, client_ai.known_facts(result), result.get("legal_name")))
        self.assertEqual(q["system_prompt"], same["system"])  # role, ICP, ground rules, confidence from _shared.json
        self.assertEqual(first["instructions"], same["instruction"])
        if first["answer_from"] == "pages":
            self.assertEqual(q["company_page_text"], same["context"])

    def _cannot(self):
        return [{"id": self.q["id"], "answer_from": self.q["answer_from"], "cannot_answer": "no web search tool"}]

    def test_cannot_answer_stays_open_and_nothing_else_is_used(self):
        out = client_ai.submit_answers(DOMAIN, self._cannot(), "prod", "icp")
        self.assertEqual(FakeRunner.calls, [])
        self.assertEqual(out["not_answered"]["questions"][0]["id"], self.q["id"])
        self.assertNotIn(self.q["id"], self.store.get_ai(DOMAIN, "icp"))

    def test_mcp_has_no_openai_route(self):
        import inspect
        from enrich.integrations import mcp_server
        self.assertFalse(hasattr(client_ai, "openai_fallback"))
        tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
        self.assertNotIn("allow_openai_fallback", tools["submit_answers"].input_schema["properties"])
        for name in ("check_company", "enrich_list", "pull_clay_table"):  # free layers only: use_ai is never true
            src = inspect.getsource(getattr(mcp_server, name))
            self.assertNotIn("use_ai=True", src)

    def test_answered_questions_are_saved_as_the_attached_model(self):
        ans = {"id": self.q["id"], "answer_from": self.q["answer_from"], "answer": "unknown", "confidence": "low",
               "evidence_quote": "", "source": "", "reason": "not stated"}
        out = client_ai.submit_answers(DOMAIN, [ans], "prod", "icp")
        self.assertNotIn("not_answered", out)
        self.assertEqual(FakeRunner.calls, [])
        self.assertEqual(self.store.get_ai(DOMAIN, "icp")[self.q["id"]][2 if ans["answer_from"] == "web" else 1]
                         ["usage"]["model"], "attached MCP model")

if __name__ == "__main__":
    unittest.main()
