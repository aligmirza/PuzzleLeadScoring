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

from . import apply as ai_apply
from . import prompts as P
from ..pipeline.common import ai_prompts, console, rel
from ..core.inputs import load_leads
from ..checks.rules import SIGNALS, VALUE_NAMES
from ..core.store import Store


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


def chunks(items: list[str], size: int, by_model: bool = True) -> list[list[str]]:
    """Split a company's data points into calls of at most `size`; step 1 only groups data points on the same model."""
    groups: dict[str, list[str]] = {}
    for pid in items:
        groups.setdefault(P.load()[1][pid]["model"] if by_model else "all", []).append(pid)
    return [ids[i:i + max(1, size)] for ids in groups.values() for i in range(0, len(ids), max(1, size))]


# ---------------------------------------------------------------------------
class Runner:
    def __init__(self, store: Store, phase: str, web: bool, redo: bool, concurrency: int, search_signals: bool = True,
                 mode: dict | None = None):
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
        self.search_signals = search_signals
        self.mode = mode or P.mode()
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

    async def call(self, model: str, system: str, parts: list[str], schema: dict, tools=None, cache_key=None,
                   max_tokens: int = 900):
        kwargs = dict(model=model, instructions=system, max_output_tokens=max_tokens, store=False,
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
        todo = [pid for pid in ai_prompts(result) if wanted(pid, self.search_signals)]
        self.c["skipped"] += len(ai_prompts(result)) - len(todo)
        step1: dict[str, dict | None] = {pid: (done.get(pid, {}).get(1) or {}).get("answer") for pid in todo}
        try:
            # step 1: our saved data, for data points that aren't web-first and haven't been answered yet
            ask = [pid for pid in todo if P.web_mode(pid) != "primary" and pid not in done]
            for chunk in chunks(ask, self.mode["site_group"]):
                step1.update(await self.step1(domain, result, facts, chunk))
            # step 2: web search where step 1 found nothing (or the data point is web-first)
            web = []
            for pid in todo:
                if 2 in done.get(pid, {}) or not (self.web and not self.web_disabled_reason):
                    continue
                if pid in done and 1 not in done[pid] and P.web_mode(pid) != "primary":
                    continue
                if P.needs_web(pid, step1.get(pid)):
                    web.append(pid)
            for chunk in chunks(web, self.mode["web_group"], by_model=False):
                await self.step2(domain, result, facts, chunk, step1)
        except self.openai.AuthenticationError:
            sys.exit("OpenAI rejected the API key. Check OPENAI_API_KEY.")
        except self.openai.BadRequestError as e:
            if "web_search" in str(e) or "tool" in str(e).lower():
                self.web_disabled_reason = f"web search not available for {self.pv['web']}: {str(e)[:160]}"
            self.c["errors"] += 1
        except (self.openai.APIError, ValueError) as e:
            self.c["errors"] += 1
            self.feed.appendleft((domain, f"error: {type(e).__name__}", "", str(e)[:40], ""))
        # apply everything answered so far and save
        updated = ai_apply.apply(result, self.store.get_ai(domain, self.phase), self.phase)
        self.store.save_result(domain, updated, self.phase)
        self.c["companies"] += 1
        if self.phase == "icp":
            self.c[f"verdict_{updated.get('icp_verdict')}"] += 1

    async def step1(self, domain: str, result: dict, facts: dict, pids: list[str]) -> dict[str, dict]:
        """Step 1 from our saved pages: one data point per call (accurate mode) or several (cheap mode)."""
        if len(pids) == 1:
            m = P.build(pids[0], result["company"], domain, facts, result.get("ai_context", {}))
            data, usage, _ = await self.call(m["model"], m["system"], [m["context"], m["instruction"]], m["schema"],
                                             cache_key=f"{self.phase}:{domain}")
            return self._save(domain, pids, 1, {"data_point_1": data}, usage, [])
        m = P.build_group(pids, result["company"], domain, facts, result.get("ai_context", {}))
        data, usage, _ = await self.call(m["model"], m["system"], [m["context"], m["instruction"]], m["schema"],
                                         cache_key=f"{self.phase}:{domain}", max_tokens=350 * len(pids) + 300)
        return self._save(domain, pids, 1, data, usage, [])

    async def step2(self, domain: str, result: dict, facts: dict, pids: list[str], step1: dict) -> None:
        """Step 2, web search: one data point per call (accurate mode) or several sharing the searches (cheap mode)."""
        if len(pids) == 1:
            m = P.build_search(pids[0], result["company"], domain, facts, result.get("legal_name"), step1.get(pids[0]))
            data, usage, sources = await self.call(m["model"], m["system"], [m["instruction"]], m["schema"], tools=m["tools"])
            self._save(domain, pids, 2, {"data_point_1": data}, usage, sources)
            return
        m = P.build_search_group(pids, result["company"], domain, facts, result.get("legal_name"), step1)
        data, usage, sources = await self.call(m["model"], m["system"], [m["instruction"]], m["schema"], tools=m["tools"],
                                               max_tokens=450 * len(pids) + 300)
        self._save(domain, pids, 2, data, usage, sources)

    def _save(self, domain: str, pids: list[str], step: int, data: dict, usage: dict, sources: list) -> dict[str, dict]:
        """Store each data point's answer; a grouped call's usage and cost are split evenly between its data points."""
        n = len(pids)
        share = usage if n == 1 else {k: (round(v / n, 6) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                                      for k, v in usage.items()}
        share = {**share, "mode": self.mode["name"], **({"grouped_with": n} if n > 1 else {})}
        out = {}
        for k, pid in enumerate(pids, start=1):
            ans = data.get(f"data_point_{k}")
            if not isinstance(ans, dict):
                ans = {"error": "missing from the grouped answer"}
            if step == 2:
                ans["_sources_found"] = sources[:8]
            self.store.save_ai(domain, self.phase, pid, step, ans, share)
            self._log(domain, pid, step, ans)
            out[pid] = ans
        return out

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
def wanted(pid: str, search_signals: bool) -> bool:
    return search_signals or P.load()[1][pid]["stage"] != "search_results"


def estimate(store: Store, domains: list[str], phase: str, redo: bool, search_signals: bool = True,
             mode: dict | None = None) -> dict:
    """Cost estimate for a mode. Step 1: the shared part (instructions + page text) is sent once per call, so grouping
    cuts it; a web search call costs about $0.01 per search plus ~8,000 tokens of results, up to 3 searches a call."""
    mode = mode or P.mode()
    pv = P.provider()
    price = pv.get("price_per_million", {})
    per_search = pv.get("price_per_web_search", 0) + 8000 * price.get("input", 0) / 1e6  # ~8,000 tokens per search (measured)
    calls = data_points = tokens = cached = search_first = web_calls = first_calls = 0
    companies = 0
    for d in domains:
        r = store.get_result(d, phase) or {}
        if not r.get("ai", {}).get("needed"):
            continue
        done = {} if redo else store.get_ai(d, phase)
        todo = [p for p in ai_prompts(r) if p not in done and wanted(p, search_signals)]
        if not todo:
            continue
        companies += 1
        data_points += len(todo)
        every = ai_prompts(r)
        instr = {p: len(P.instruction_text(p)) // 4 for p in every}
        shared = max(0, (r["ai"].get("tokens_est", 0) - sum(instr.values())) // max(1, len(every)))
        site = [p for p in todo if P.web_mode(p) != "primary"]
        first = [p for p in todo if P.web_mode(p) == "primary"]
        site_calls = len(chunks(site, mode["site_group"]))
        calls += site_calls
        tokens += shared * site_calls + sum(instr[p] for p in site)
        cached += shared * max(0, site_calls - 1)
        search_first += len(first)
        first_calls += len(chunks(first, mode["web_group"], by_model=False))
        web_calls += len(chunks([p for p in todo if P.web_mode(p) != "off"], mode["web_group"], by_model=False))
    output = 150 * data_points
    step1 = ((tokens - cached) * price.get("input", 0) + cached * price.get("cached_input", 0) + output * price.get("output", 0)) / 1e6
    return {"mode": mode["name"], "companies": companies, "calls": data_points, "site_calls": calls, "tokens": tokens,
            "step1_cost": step1, "search_first": search_first, "search_first_calls": first_calls,
            "search_first_cost": (first_calls * per_search, first_calls * 3 * per_search),
            "web_cost_max": web_calls * 3 * per_search}


def money(x: float) -> str:
    return f"${x:.2f}" if x >= 1 else f"${x:.4f}"


def pick_domains(store: Store, domains: list[str], phase: str, limit: int = 0) -> list[str]:
    """Companies with open AI questions in this phase; phase 2 only those phase 1 (with its AI answers) kept."""
    out = [d for d in dict.fromkeys(domains) if (store.get_result(d, phase) or {}).get("ai", {}).get("needed")]
    if phase == "signals":
        out = [d for d in out if (store.get_result(d, "icp") or {}).get("icp_verdict") not in ("No", "Unknown: site not reached")]
    return out[:limit] if limit else out


def worst_case(est: dict) -> float:
    """Highest the estimate could reach: step 1, every web-only question at 3 searches, every fallback searching."""
    return est["step1_cost"] + est["search_first_cost"][1] + est["web_cost_max"]


async def run_async(store: Store, domains: list[str], phase: str, web: bool = True, search_signals: bool = True,
                    mode_name: str | None = None, concurrency: int = 8) -> dict:
    """The AI step without the terminal (for the API): answers, applies and saves. Returns usage and cost."""
    try:
        runner = Runner(store, phase, web, False, concurrency, search_signals and web, P.mode(mode_name))
    except SystemExit as e:  # e.g. OPENAI_API_KEY missing
        raise RuntimeError(str(e))
    start = time.time()
    try:
        await asyncio.gather(*(runner.company(d) for d in domains))
    except SystemExit as e:
        raise RuntimeError(str(e))
    finally:
        await runner.client.close()
    c = runner.c
    return {"companies": len(domains), "ai_calls": c["calls"], "web_searches": c["web_searches"], "errors": c["errors"],
            "cost_usd": round(runner.cost, 4), "seconds": round(time.time() - start), "mode": runner.mode["name"]}


def run(folder: Path, phase: str, limit: int = 0, web: bool = True, redo: bool = False, concurrency: int = 8,
        assume_yes: bool = False, search_signals: bool = True, mode_name: str | None = None) -> None:
    store = Store(folder)
    rows, _ = load_leads(folder / "input.csv")
    domains = pick_domains(store, [r["domain"] for r in rows], phase, limit)
    mode = P.mode(mode_name)
    est = estimate(store, domains, phase, redo, search_signals and web, mode)
    pv = P.provider()
    if not est["calls"]:
        console.print("Nothing left for AI in this phase (all answered, or no company needs it).")
        return
    other = estimate(store, domains, phase, redo, search_signals and web, P.mode("cheap" if mode["name"] == "accurate" else "accurate"))

    def summary(e):
        s = f"step 1 about {money(e['step1_cost'])} ({e['site_calls']} calls)"
        if web and e["search_first"]:
            lo, hi = e["search_first_cost"]
            s += f", web-only questions {money(lo)} to {money(hi)} ({e['search_first_calls']} calls)"
        if web:
            s += f", other web search at most {money(e['web_cost_max'])}"
        return s
    console.print(f"[bold]AI step, {'phase 1 (ICP check)' if phase == 'icp' else 'phase 2 (signals)'}[/]  list {folder.name}  "
                  f"mode [bold]{mode['name']}[/]\n"
                  f"  {est['companies']} companies, {est['calls']} data points to answer\n"
                  f"  models: step 1 {pv['small']}" + (f", step 2 web search {pv['web']}" if web else ", web search off") + "\n"
                  f"  estimated cost: {summary(est)}\n"
                  f"  [dim]{other['mode']} mode would be: {summary(other)}[/]"
                  + ("\n  --no-search-signals skips the web-only questions" if web and est["search_first"] else ""))
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

    runner = Runner(store, phase, web, redo, concurrency, search_signals and web, mode)
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
    from ..core.runlog import log
    log(folder, f"AI, {'phase 1' if phase == 'icp' else 'phase 2'}", companies=len(domains), ai_calls=c["calls"],
        web_searches=c["web_searches"], input_tokens=c["input"], output_tokens=c["output"], errors=c["errors"],
        cost_usd=round(runner.cost, 4), seconds=round(time.time() - start), web=web, search_signals=search_signals and web,
        mode=mode["name"])
    if runner.web_disabled_reason:
        console.print(f"[yellow]Web search was turned off during the run: {runner.web_disabled_reason}[/]\n"
                      f"Set a model that supports web search as 'web' in {rel(P.PROMPTS_DIR / '_shared.json')}.")

    # rewrite this phase's outputs and the final list with the new answers
    results = [store.get_result(d, phase) for d in dict.fromkeys(r["domain"] for r in rows)]
    results = [r for r in results if r]
    if phase == "icp":
        from ..pipeline import phase1_icp
        phase1_icp.report(results, folder, store, None)
    else:
        from ..pipeline import phase2_signals
        phase2_signals.report(results, folder, store)
