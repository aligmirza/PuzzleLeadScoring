"""AI step: answer each company's queued prompts with OpenAI, store the answers, and update the results.

  step 1  our saved data   prompts.build()          model: providers.openai.small in _shared.json
  step 2  web search       prompts.build_search()   model: providers.openai.web, only when step 1 found
                                                    nothing (or straight away for web-first data points)

Answers are saved in cache.sqlite (table ai_answers) and applied on top of the rule results by
ai_apply.apply(), so re-running the rules keeps them. Companies and prompts already answered are skipped,
so a stopped run picks up where it left off.
"""
import asyncio
import copy
import json
import os
import re
import sys
import time
from collections import Counter, deque
from pathlib import Path

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm
from rich.table import Table

from . import ai_apply
from . import prompts as P
from .common import ai_prompts, console, rel
from .inputs import load_leads
from .rules import SIGNALS, VALUE_NAMES
from .store import Store


# ---------------------------------------------------------------------------
def to_strict(schema: dict) -> dict:
    """OpenAI strict JSON mode: every object lists all its fields as required and allows no extras,
    and every enum has a type."""
    s = copy.deepcopy(schema)

    def fix(node):
        if not isinstance(node, dict):
            return
        if "enum" in node and "type" not in node:
            node["type"] = "string"
        if node.get("type") == "object" or "properties" in node:
            node["type"] = "object"
            props = node.setdefault("properties", {})
            node["required"] = list(props)
            node["additionalProperties"] = False
            for v in props.values():
                fix(v)
        if "items" in node:
            fix(node["items"])
    fix(s)
    return s


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    a, b = text.find("{"), text.rfind("}")
    return json.loads(text[a:b + 1] if a >= 0 and b > a else text)


def known_facts(result: dict) -> dict:
    return {SIGNALS[k][1]: f"{VALUE_NAMES[v['value']]} ({v['evidence'][:120]})"
            for k, v in result.get("signals", {}).items() if v["value"] in ("yes", "no")}


# ---------------------------------------------------------------------------
class Runner:
    def __init__(self, store: Store, phase: str, web: bool, redo: bool, concurrency: int):
        import openai  # imported here so the rest of the tool works without the package
        self.openai = openai
        pv = P.provider()
        if pv["name"] != "openai":
            sys.exit(f"The AI runner supports OpenAI only for now; _shared.json says provider '{pv['name']}'.")
        key = os.environ.get(pv["api_key_env"])
        if not key:
            sys.exit(f"Set your OpenAI key first, in the terminal you run this from:\n  export {pv['api_key_env']}=sk-...\n"
                     f"It is read from the environment only and never saved to files or git.")
        self.client = openai.AsyncOpenAI(api_key=key, max_retries=4)
        self.pv, self.store, self.phase, self.web, self.redo = pv, store, phase, web, redo
        self.sem = asyncio.Semaphore(concurrency)
        self.c = Counter()
        self.cost = 0.0
        self.feed: deque = deque(maxlen=10)
        self.web_disabled_reason = ""

    def _cost(self, usage: dict) -> float:
        price = self.pv.get("price_per_million", {})
        fresh = usage.get("input", 0) - usage.get("cached", 0)
        return (fresh * price.get("input", 0) + usage.get("cached", 0) * price.get("cached_input", 0)
                + usage.get("output", 0) * price.get("output", 0)) / 1e6 + usage.get("web_searches", 0) * self.pv.get("price_per_web_search", 0)

    async def call(self, model: str, system: str, parts: list[str], schema: dict, tools=None, cache_key=None):
        kwargs = dict(model=model, instructions=system, max_output_tokens=900, store=False,
                      input=[{"role": "user", "content": [{"type": "input_text", "text": t} for t in parts if t]}])
        if cache_key:
            kwargs["prompt_cache_key"] = cache_key
        if tools:
            kwargs["tools"] = tools
        fmt = {"format": {"type": "json_schema", "name": "answer", "schema": to_strict(schema), "strict": True}}
        async with self.sem:
            try:
                resp = await self.client.responses.create(text=fmt, **kwargs)
            except self.openai.BadRequestError:
                if not tools:
                    raise
                # some models can't combine web search with strict JSON: ask for the JSON in plain text instead
                kwargs["input"][0]["content"].append({"type": "input_text", "text": "Reply with only the JSON object, no other text."})
                resp = await self.client.responses.create(**kwargs)
        u = resp.usage
        usage = {"model": model, "input": u.input_tokens if u else 0, "output": u.output_tokens if u else 0,
                 "cached": getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0 if u else 0,
                 "web_searches": sum(1 for item in resp.output if getattr(item, "type", "") == "web_search_call")}
        sources = []
        for item in resp.output:
            for c in getattr(item, "content", None) or []:
                for a in getattr(c, "annotations", None) or []:
                    if getattr(a, "type", "") == "url_citation":
                        sources.append({"url": a.url, "title": getattr(a, "title", "")})
        usage["cost"] = round(self._cost(usage), 6)
        self.cost += usage["cost"]
        self.c["calls"] += 1
        self.c["input"] += usage["input"]
        self.c["cached"] += usage["cached"]
        self.c["output"] += usage["output"]
        self.c["web_searches"] += usage["web_searches"]
        return parse_json(resp.output_text), usage, sources

    async def company(self, domain: str):
        result = self.store.get_result(domain, self.phase)
        if not result or not result.get("ai", {}).get("needed"):
            return
        done = {} if self.redo else self.store.get_ai(domain, self.phase)
        facts = known_facts(result)
        for pid in ai_prompts(result):
            if pid in done:
                self.c["skipped"] += 1
                continue
            step1 = None
            try:
                if P.web_mode(pid) != "primary":
                    msg = P.build(pid, result["company"], domain, facts, result.get("ai_context", {}))
                    step1, usage, _ = await self.call(msg["model"], msg["system"], [msg["context"], msg["instruction"]],
                                                      msg["schema"], cache_key=f"{self.phase}:{domain}")
                    self.store.save_ai(domain, self.phase, pid, 1, step1, usage)
                    self._log(domain, pid, 1, step1)
                if self.web and not self.web_disabled_reason and P.needs_web(pid, step1):
                    msg = P.build_search(pid, result["company"], domain, facts, result.get("legal_name"), step1)
                    data, usage, sources = await self.call(msg["model"], msg["system"], [msg["instruction"]],
                                                           msg["schema"], tools=msg["tools"])
                    data["_sources_found"] = sources[:8]
                    self.store.save_ai(domain, self.phase, pid, 2, data, usage)
                    self._log(domain, pid, 2, data)
            except self.openai.AuthenticationError:
                sys.exit("OpenAI rejected the API key. Check OPENAI_API_KEY.")
            except self.openai.BadRequestError as e:
                if "web_search" in str(e) or "tool" in str(e).lower():
                    self.web_disabled_reason = f"web search not available for {self.pv['web']}: {str(e)[:160]}"
                self.c["errors"] += 1
                self.store.save_ai(domain, self.phase, pid, 2 if step1 else 1, {"error": str(e)[:300]}, {})
            except (self.openai.APIError, ValueError) as e:
                self.c["errors"] += 1
                self.store.save_ai(domain, self.phase, pid, 2 if step1 else 1, {"error": f"{type(e).__name__}: {str(e)[:250]}"}, {})
        # apply everything answered so far and save
        updated = ai_apply.apply(result, self.store.get_ai(domain, self.phase), self.phase)
        self.store.save_result(domain, updated, self.phase)
        self.c["companies"] += 1
        if self.phase == "icp":
            self.c[f"verdict_{updated.get('icp_verdict')}"] += 1

    def _log(self, domain, pid, step, data):
        ans = data.get("answer", data.get("value", ""))
        self.feed.appendleft((domain, P.name(pid)[:44], "web" if step == 2 else "site", str(ans), data.get("confidence", "")))

    def view(self, progress) -> Group:
        c = self.c
        t = Table.grid(padding=(0, 3))
        for _ in range(4):
            t.add_column()
        t.add_row("[bold]AI calls[/]", str(c["calls"]), "[bold]Web searches[/]", str(c["web_searches"]))
        t.add_row("[bold]Input tokens[/]", f"{c['input']:,} ({c['cached']:,} cached)", "[bold]Output tokens[/]", f"{c['output']:,}")
        t.add_row("[bold]Cost so far[/]", f"${self.cost:,.4f}", "[bold]Errors[/]", f"[red]{c['errors']}[/]" if c["errors"] else "0")
        if self.web_disabled_reason:
            t.add_row("[yellow]Web search off[/]", self.web_disabled_reason[:90], "", "")
        f = Table(expand=True, header_style="bold dim", show_edge=False)
        for col in ("Company", "Data point", "Step", "Answer", "Confidence"):
            f.add_column(col, no_wrap=True, overflow="ellipsis")
        for row in self.feed:
            f.add_row(*row)
        return Group(Panel(progress, title=f"[bold]AI step ({'phase 1, ICP' if self.phase == 'icp' else 'phase 2, signals'})[/]",
                           border_style="blue"),
                     Panel(t, title="Running totals", border_style="dim"), Panel(f, title="Latest answers", border_style="dim"))


# ---------------------------------------------------------------------------
def estimate(store: Store, domains: list[str], phase: str, redo: bool) -> dict:
    pv = P.provider()
    price = pv.get("price_per_million", {})
    calls = tokens = cached = 0
    companies = 0
    for d in domains:
        r = store.get_result(d, phase) or {}
        if not r.get("ai", {}).get("needed"):
            continue
        done = {} if redo else store.get_ai(d, phase)
        todo = [p for p in ai_prompts(r) if p not in done]
        if not todo:
            continue
        companies += 1
        calls += len(todo)
        share = len(todo) / max(1, len(ai_prompts(r)))
        tokens += int(r["ai"].get("tokens_est", 0) * share)
        cached += int(r["ai"].get("cached_tokens_est", 0) * share)
    output = 150 * calls
    step1 = ((tokens - cached) * price.get("input", 0) + cached * price.get("cached_input", 0) + output * price.get("output", 0)) / 1e6
    return {"companies": companies, "calls": calls, "tokens": tokens, "step1_cost": step1,
            "web_cost_max": calls * (3 * pv.get("price_per_web_search", 0) + 4000 * price.get("input", 0) / 1e6)}


def money(x: float) -> str:
    return f"${x:.2f}" if x >= 1 else f"${x:.4f}"


def run(folder: Path, phase: str, limit: int = 0, web: bool = True, redo: bool = False, concurrency: int = 8,
        assume_yes: bool = False) -> None:
    store = Store(folder)
    rows, _ = load_leads(folder / "input.csv")
    domains = list(dict.fromkeys(r["domain"] for r in rows))
    domains = [d for d in domains if (store.get_result(d, phase) or {}).get("ai", {}).get("needed")]
    if limit:
        domains = domains[:limit]
    est = estimate(store, domains, phase, redo)
    pv = P.provider()
    if not est["calls"]:
        console.print("Nothing left for AI in this phase (all answered, or no company needs it).")
        return
    console.print(f"[bold]AI step, {'phase 1 (ICP check)' if phase == 'icp' else 'phase 2 (signals)'}[/]  list {folder.name}\n"
                  f"  {est['companies']} companies, {est['calls']} prompts, about {est['tokens']:,} input tokens\n"
                  f"  models: step 1 {pv['small']}" + (f", step 2 web search {pv['web']}" if web else ", web search off") + "\n"
                  f"  estimated cost: about {money(est['step1_cost'])} for step 1"
                  + (f", plus up to {money(est['web_cost_max'])} if every prompt also needs web search" if web else ""))
    if not assume_yes:
        if not sys.stdin.isatty():
            console.print("[yellow]Not running: no terminal to confirm the cost. Add --yes to run without asking.[/]")
            return
        try:
            if not Confirm.ask("Run the AI step?", default=False):
                console.print("Cancelled. Nothing was sent.")
                return
        except (EOFError, KeyboardInterrupt):
            console.print("Cancelled. Nothing was sent.")
            return

    runner = Runner(store, phase, web, redo, concurrency)
    progress = Progress(SpinnerColumn(), TextColumn("{task.description:<14}"), BarColumn(bar_width=36),
                        MofNCompleteColumn(), TimeElapsedColumn())
    task = progress.add_task("Companies", total=len(domains))

    async def main():
        async def one(d):
            await runner.company(d)
            progress.update(task, advance=1)
        try:
            await asyncio.gather(*(one(d) for d in domains))
        finally:
            await runner.client.close()

    start = time.time()
    with Live(runner.view(progress), console=console, refresh_per_second=4) as live:
        async def refresher():
            while not progress.finished:
                live.update(runner.view(progress))
                await asyncio.sleep(0.25)

        async def both():
            await asyncio.gather(main(), refresher())
        asyncio.run(both())
        live.update(runner.view(progress))

    c = runner.c
    console.print(f"Done in {time.time() - start:.0f} s: {c['calls']} AI calls, {c['web_searches']} web searches, "
                  f"{c['input']:,} input tokens ({c['cached']:,} cached), {c['output']:,} output tokens, "
                  f"cost about [bold]${runner.cost:.4f}[/]" + (f", [red]{c['errors']} errors[/]" if c["errors"] else ""))
    if runner.web_disabled_reason:
        console.print(f"[yellow]Web search was turned off during the run: {runner.web_disabled_reason}[/]\n"
                      f"Set a model that supports web search as 'web' in {rel(P.PROMPTS_DIR / '_shared.json')}.")

    # rewrite this phase's outputs and the final list with the new answers
    results = [store.get_result(d, phase) for d in dict.fromkeys(r["domain"] for r in rows)]
    results = [r for r in results if r]
    if phase == "icp":
        from . import phase1_icp
        phase1_icp.report(results, folder, store, None)
    else:
        from . import phase2_signals
        phase2_signals.report(results, folder, store)
