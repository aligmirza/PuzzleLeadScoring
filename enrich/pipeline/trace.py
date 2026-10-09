"""Trace one company through the whole workflow, one step at a time.

    python -m enrich trace linear.app                 free steps only (website, rules, directories, score)
    python -m enrich trace linear.app --ai            also the AI steps (shows the cost and asks first)

Each step shows what it received, what it found (with proof) and what it decided, then waits for Enter.
Everything is also saved to lists/trace_<domain>/output/trace_<domain>.md, and the company gets its own list
folder, so `explain`, the API and the MCP tools work on it too.
"""
from __future__ import annotations

import asyncio
import csv
import re
import sys
import time
from pathlib import Path

from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from ..ai import prompts as P
from ..checks.rules import ALWAYS_PROMPTS, CHECK, SEARCH_SIGNALS, SIGNALS, UNKNOWN, VALUE_NAMES, decide, extract, signal_column
from ..core.inputs import normalize_domain
from ..core.store import Store
from ..sources.crawl import ICP_KINDS, SIGNAL_KINDS, Crawler, slim_html
from . import final
from .common import LISTS, console, directory_finder, rel, run_rules

GROUPS_P1 = ("must", "exclusion")
GROUPS_P2 = ("fit", "buying", "weak")


class Trace:
    def __init__(self, domain: str, pause: bool):
        self.domain, self.pause, self.md, self.n = domain, pause and sys.stdin.isatty(), [], 0
        self.stopped = False

    def step(self, title: str, intro: str, table: Table | None = None, md_rows: list[list[str]] | None = None,
             md_head: list[str] | None = None, notes: list[str] | None = None) -> None:
        self.n += 1
        heading = f"Step {self.n}. {title}"
        body = [intro] + ([""] + notes if notes else [])
        shown = re.sub(r"\*\*(.+?)\*\*", r"[bold]\1[/bold]", "\n".join(body).replace("[", "\\["))
        console.print(Panel(shown, title=f"[bold]{heading}[/]", title_align="left", border_style="blue"))
        if table is not None:
            console.print(table)
        self.md += [f"## {heading}", "", intro, ""]
        if md_rows:
            self.md += ["| " + " | ".join(md_head) + " |", "|" + "---|" * len(md_head)]
            self.md += ["| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in r) + " |" for r in md_rows]
            self.md.append("")
        if notes:
            self.md += [f"- {n}" for n in notes] + [""]
        if self.pause:
            try:
                if Prompt.ask("[dim]Enter: next step, q: stop[/]", default="", show_default=False).strip().lower() == "q":
                    self.stopped = True
            except (EOFError, KeyboardInterrupt):
                self.stopped = True


def _table(head: list[str], rows: list[list[str]], title: str = "") -> Table:
    t = Table(title=title or None, title_justify="left", header_style="bold", show_lines=False)
    for h in head:
        t.add_column(h, overflow="fold")
    for r in rows:
        t.add_row(*[str(c) for c in r])
    return t


def _answers(signals: dict, groups: tuple, only_set: bool = False) -> list[list[str]]:
    rows = []
    for sid, (group, label) in SIGNALS.items():
        if group not in groups or sid not in signals:
            continue
        v = signals[sid]
        if only_set and v["value"] == UNKNOWN:
            continue
        rows.append([signal_column(sid), VALUE_NAMES[v["value"]], (v["evidence"] or v["note"] or "")[:160],
                     v.get("url", "")[:70], v.get("method", "code")])
    return rows


def _diff(before: dict, after: dict, groups: tuple) -> list[list[str]]:
    rows = []
    for sid, v in after.items():
        if SIGNALS[sid][0] not in groups:
            continue
        old = before.get(sid, {"value": UNKNOWN})
        if old["value"] != v["value"] or old.get("evidence") != v.get("evidence"):
            if v.get("method") in ("directory", "ai") or old["value"] != v["value"]:
                rows.append([signal_column(sid), VALUE_NAMES[old["value"]], VALUE_NAMES[v["value"]], (v["evidence"] or "")[:140],
                             (v.get("note") or "")[:120]])
    return rows


def _why_asked(pid: str, result: dict) -> str:
    p = P.load()[1][pid]
    sid = p.get("signal_id")
    v = result["signals"].get(sid, {}) if sid else {}
    if sid in SEARCH_SIGNALS:
        return "only web search can answer it (news, registries, social posts)"
    if pid in ALWAYS_PROMPTS:
        return "a judgement call no rule can make; asked for every company in phase 2"
    if v.get("value") == CHECK:
        return f"a rule found a clue but couldn't confirm it: {(v.get('evidence') or '')[:90]}"
    if pid == "exclusions/series_c_or_public":
        return "funded (site, Form D or YC) but no early stage confirmed: could be Series C or later"
    if pid == "fit/venture_backed_stage":
        return "funding stage not found by the rules or directories"
    if pid.startswith("fields/"):
        return f"{P.name(pid)} not found by the rules"
    return "the rules found nothing for it"


def _ai_questions(result: dict) -> list[list[str]]:
    return [[P.name(pid), P.step_plan(pid), _why_asked(pid, result)] for pid in result["ai"]["prompts"]]


async def _ai_step(store: Store, domain: str, phase: str, mode: str | None, assume_yes: bool) -> dict | None:
    """Estimate, ask, run, and return the answers for this company."""
    from ..ai import runner as ai
    est = ai.estimate(store, [domain], phase, False, True, P.mode(mode))
    if not est["calls"]:
        return None
    worst = ai.worst_case(est)
    console.print(f"AI for {phase}: {est['calls']} questions, about {ai.money(est['step1_cost'])} from our pages, "
                  f"at most {ai.money(worst)} if every question also needs web search ({P.mode(mode)['name']} mode).")
    if not assume_yes:
        try:
            from rich.prompt import Confirm
            if not sys.stdin.isatty() or not Confirm.ask("Run the AI step for this company?", default=False):
                console.print("AI skipped. Nothing was sent.")
                return None
        except (EOFError, KeyboardInterrupt):
            return None
    return await ai.run_async(store, [domain], phase, True, True, mode)


def _ai_rows(store: Store, domain: str, phase: str, result: dict) -> list[list[str]]:
    rows = []
    for pid, steps in store.get_ai(domain, phase).items():
        sid = P.load()[1].get(pid, {}).get("signal_id")
        final_v = result["signals"].get(sid, {}) if sid else {}
        for step in sorted(steps):
            a = steps[step]["answer"]
            ans = a.get("answer", a.get("venture_backed", a.get("value", a.get("error", ""))))
            extra = f" | match: {a.get('entity_match')}" if step == 2 and a.get("entity_match") else ""
            rows.append([P.name(pid), "our pages" if step == 1 else "web search", str(ans), a.get("confidence", ""),
                         (a.get("evidence_quote") or a.get("reason") or "")[:120] + extra,
                         (VALUE_NAMES.get(final_v.get("value"), "") + (f" ({final_v.get('note', '')[:70]})" if final_v else ""))
                         if step == max(steps) else ""])
    return rows


def run(domain_in: str, company: str = "", employees: str = "", use_ai: bool = False, mode: str | None = None,
        pause: bool = True, refresh: bool = False, assume_yes: bool = False) -> Path | None:
    domain = normalize_domain(domain_in)
    if not domain:
        raise SystemExit(f"'{domain_in}' is not a valid domain")
    folder = LISTS / f"trace_{domain.replace('.', '_')}"
    (folder / "output").mkdir(parents=True, exist_ok=True)
    with open(folder / "input.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Company Name", "Domain", "Employees"])
        w.writeheader()
        w.writerow({"Company Name": company, "Domain": domain_in, "Employees": employees})
    store = Store(folder)
    row = {"domain": domain, "company": company, "employees": employees, "input": {}}
    tr = Trace(domain, pause)

    # 1. input
    tr.step("Input", f"You gave '{domain_in}'. Cleaned domain: **{domain}**. Company name: {company or '(none: the domain is used)'}. "
                     f"Employees: {employees or '(none: size comes from a directory or AI, else SMB)'}.\n"
                     f"Saved as its own list: {rel(folder)}")
    if tr.stopped:
        return None

    # 2. download (phase 1 pages)
    record = None if refresh else store.get_site(domain)
    t0 = time.time()
    dns_used, browser_pages = [], 0
    if record is None:
        async def crawl():
            c = Crawler()
            try:
                return await c.crawl(domain, ICP_KINDS), sorted(c.dns.used), c.browser.pages
            finally:
                await c.close()
        (record, pages, jobs), dns_used, browser_pages = asyncio.run(crawl())
        for kind, html in pages.items():
            store.save_page(domain, kind, slim_html(html))
        if jobs:
            store.save_jobs(domain, jobs)
        record["input"] = {"company": company, "employees": employees}
        store.save_site(domain, record)
        how = "downloaded now"
    else:
        pages = {k: "" for k in record.get("pages", {})}
        how = "saved earlier (use --refresh to download again)"
    page_rows = [[k, record["pages"].get(k, ""), f"{len(pages.get(k, '')):,}" if pages.get(k) else "", f"{len(store.load_page(domain, k) or ''):,}"]
                 for k in sorted(record.get("pages", {}))]
    jobs = store.load_jobs(domain)
    notes = [f"Status: **{record.get('status')}**" + (f" ({record.get('error')})" if record.get("error") else ""),
             f"Final address: {record.get('final_url') or '-'}",
             f"Requests: {record.get('requests', 0)}, time {time.time() - t0:.1f} s ({how})",
             f"Real browser used: {'yes' if record.get('via_browser') else 'no'}"
             + (f" ({browser_pages} pages)" if browser_pages else "")
             + (f"; public DNS fallback used for {', '.join(dns_used)}" if dns_used else ""),
             f"Job board: {record.get('ats') or 'none found'}" + (f", {len(jobs)} open roles: " + ", ".join(j['title'] for j in jobs[:6]) if jobs else "")]
    tr.step("Download the website (phase 1 pages)",
            "The homepage first, then only the pages must-haves and exclusions need, found from links, the sitemap or common "
            "addresses. Pages are saved slimmed (scripts and styles removed, key tags kept).",
            _table(["Page", "Address", "Downloaded (chars)", "Saved slim (chars)"], page_rows), page_rows,
            ["Page", "Address", "Downloaded (chars)", "Saved slim (chars)"], notes)
    if tr.stopped:
        return None

    # 3. rules (code only)
    page_html = {k: store.load_page(domain, k) for k in record.get("pages", {})}
    page_html = {k: v for k, v in page_html.items() if v}
    code_only = extract(domain, company, employees, record, page_html, jobs, "icp")
    f = code_only.get("facts", {})
    rows = _answers(code_only["signals"], GROUPS_P1)
    tr.step("Rules on the saved pages (phase 1, free)",
            "Pattern matching on the page text and source code. Every Yes and No comes with the quote and page it came from; "
            "Unknown means nothing was found and never counts against the company.",
            _table(["Data point", "Answer", "Proof", "Page", "Source"], rows), rows, ["Data point", "Answer", "Proof", "Page", "Source"],
            [f"Legal name: {f.get('legal_name') or 'not found'} ({f.get('legal_name_source') or '-'})",
             f"Entity type: {f.get('entity_type', 'Unknown')}" + (f" ({f.get('entity_basis')})" if f.get("entity_basis") else ""),
             f"Prices found: {', '.join(f.get('prices', [])[:3]) or 'none'}; checkout: {', '.join(f.get('checkout', [])) or 'none'}",
             "Fintech tools: " + (", ".join(f"{n} ({t['value']})" for n, t in f.get("tools", {}).items()) or "none"),
             f"US clues: {'; '.join(f.get('us_clues', [])[:2]) or 'none'} | foreign clues: {'; '.join(f.get('foreign_clues', [])[:2]) or 'none'}"])
    if tr.stopped:
        return None

    # 4. directories
    finder = directory_finder(store, row)
    if record.get("status") == "live" and finder:
        found = finder(f.get("legal_name"), f.get("us_clues", []), False)
        with_dirs = extract(domain, company, employees, record, page_html, jobs, "icp", finder)
        dir_rows = []
        for name in ("YC", "SEC", "IRS"):
            hit = found.get(name)
            if not hit:
                dir_rows.append([name, "not found", "", ""])
                continue
            label = {"YC": f"{hit.get('name')} {hit.get('batch')}, {hit.get('status')}, {hit.get('all_locations') or ''}",
                     "SEC": f"{hit.get('name')}, tickers {hit.get('tickers') or '-'}, Form D {', '.join(hit.get('form_d', [])[:3]) or '-'}, "
                            f"incorporated {hit.get('state_of_incorporation') or '-'}",
                     "IRS": f"{hit.get('name')}, {hit.get('state')}, EIN {hit.get('ein')}"}[name]
            dir_rows.append([name, label[:150], hit.get("match", ""), hit.get("basis", "")])
        changes = _diff(code_only["signals"], with_dirs["signals"], GROUPS_P1)
        tr.step("Free directories: YC, SEC, IRS",
                "Looked up by website domain (YC) and by the legal name from the company's own site (SEC, IRS). Only strong "
                "matches can make a company NO; a name-only match is ignored or becomes Needs check.",
                _table(["Directory", "Found", "Match", "Matched on"], dir_rows), dir_rows, ["Directory", "Found", "Match", "Matched on"],
                ["Answers changed by the directories: " + ("none" if not changes else
                 "; ".join(f"{c[0]}: {c[1]} -> {c[2]} ({c[3][:80]})" for c in changes))]
                + ([f"Not reachable: {found['errors']}"] if found.get("errors") else []))
        if tr.stopped:
            return None

    # 5. verdict (saved exactly as the pipeline does)
    result = run_rules(store, row, record, "icp")
    d = decide(result["signals"], record.get("status"))
    q = _ai_questions(result)
    tr.step("ICP verdict (phase 1)",
            f"Verdict: **{result.get('icp_verdict')}**. " + (f"Why: {result.get('exclusion_reason') or ''}" if d["excluded"] else "")
            + (f"Must-haves confirmed: {len(d['confirmed'])} of 4." if not d["excluded"] else ""),
            _table(["AI question still open", "How it would be answered", "Why it's asked"], q) if q else None, q,
            ["AI question still open", "How it would be answered", "Why it's asked"],
            ["Still to confirm: " + (", ".join(result.get("icp_checks", [])) or "nothing"),
             "AI is only asked what the rules and directories couldn't answer." if q else "No AI needed for phase 1."])
    if tr.stopped:
        return None

    # 6. AI phase 1
    if use_ai and q:
        usage = asyncio.run(_ai_step(store, domain, "icp", mode, assume_yes))
        if usage:
            result = run_rules(store, row, record, "icp")
            rows = _ai_rows(store, domain, "icp", result)
            tr.step("AI, phase 1", f"Answered in {usage['ai_calls']} calls, {usage['web_searches']} web searches, "
                                   f"${usage['cost_usd']:.4f}. Verdict now: **{result.get('icp_verdict')}**.",
                    _table(["Data point", "Step", "AI said", "Confidence", "Quote / reason", "Counted as"], rows), rows,
                    ["Data point", "Step", "AI said", "Confidence", "Quote / reason", "Counted as"],
                    ["An AI answer only counts with a quote, enough confidence, and (for web search) a match on the company's "
                     "domain. 'Not mentioned' is never a No; recent signals are judged by the date in the quote."])
            if tr.stopped:
                return None

    # 7-9. phase 2
    if result.get("icp_verdict") in ("No", "Unknown: site not reached"):
        tr.step("Phase 2 skipped", f"Phase 2 (signals and score) only runs for companies still in the running. "
                                   f"This one is **{result.get('icp_verdict')}**.")
    else:
        if not record.get("signals_crawled") or refresh:
            async def more():
                c = Crawler()
                try:
                    return await c.crawl_more(record, store.load_page(domain, "home") or "", SIGNAL_KINDS)
                finally:
                    await c.close()
            extra = asyncio.run(more())
            for kind, html in extra.items():
                store.save_page(domain, kind, slim_html(html))
            record["signals_crawled"] = True
            store.save_site(domain, record)
        else:
            extra = {}
        p2_pages = [[k, record["pages"].get(k, ""), "new" if k in extra else "saved"] for k in sorted(SIGNAL_KINDS & set(record["pages"]))]
        tr.step("Download the extra pages (phase 2)", "The pages fit, buying and weak signals need: team, blog, integrations, "
                "customers, locations and more.", _table(["Page", "Address", ""], p2_pages), p2_pages, ["Page", "Address", ""])
        if tr.stopped:
            return None

        page_html = {k: v for k, v in ((k, store.load_page(domain, k)) for k in record.get("pages", {})) if v}
        code2 = extract(domain, company, employees, record, page_html, jobs, "signals")
        result2 = run_rules(store, row, record, "signals")
        result2["icp_verdict"] = result.get("icp_verdict")
        store.save_result(domain, result2, "signals")
        rows = _answers(result2["signals"], GROUPS_P2, only_set=True)
        changes = _diff(code2["signals"], result2["signals"], GROUPS_P2)
        old = (store.get_directory(domain) or {}).get("Wayback") or {}
        tr.step("Signals: rules, directories and old copies of the site (phase 2, free)",
                "Fit, buying and weak signals. Only answers that were found are listed; everything else is empty and counts 0.",
                _table(["Data point", "Answer", "Proof", "Page", "Source"], rows), rows, ["Data point", "Answer", "Proof", "Page", "Source"],
                ["Changed by directories or old copies of the site: " + ("none" if not changes else
                 "; ".join(f"{c[0]}: {c[1]} -> {c[2]}" for c in changes)),
                 "Internet Archive: " + (", ".join(f"{k} {v}" for k, v in old.items()) if old else "not checked or no copy")])
        if tr.stopped:
            return None
        q2 = _ai_questions(result2)
        if q2:
            tr.step("AI questions for phase 2", f"{len(q2)} questions the free checks couldn't answer.",
                    _table(["AI question", "How it would be answered", "Why it's asked"], q2), q2,
                    ["AI question", "How it would be answered", "Why it's asked"],
                    ["Run with --ai to answer them (shows the cost and asks first)." if not use_ai else "Next: the AI step."])
            if tr.stopped:
                return None
        if use_ai and q2:
            usage = asyncio.run(_ai_step(store, domain, "signals", mode, assume_yes))
            if usage:
                result2 = run_rules(store, row, record, "signals")
                result2["icp_verdict"] = result.get("icp_verdict")
                store.save_result(domain, result2, "signals")
                rows = _ai_rows(store, domain, "signals", result2)
                tr.step("AI, phase 2", f"Answered in {usage['ai_calls']} calls, {usage['web_searches']} web searches, "
                                       f"${usage['cost_usd']:.4f}.",
                        _table(["Data point", "Step", "AI said", "Confidence", "Quote / reason", "Counted as"], rows), rows,
                        ["Data point", "Step", "AI said", "Confidence", "Quote / reason", "Counted as"])
                if tr.stopped:
                    return None

    # 10. score and final row
    out = final.write(folder, store)
    with open(out, newline="") as fh:
        frow = next(csv.DictReader(fh))
    breakdown = [[part.rsplit(" ", 1)[0], part.rsplit(" ", 1)[1]] for part in (frow.get("Score breakdown") or "").split("; ") if part]
    tr.step("Score and final row",
            f"ICP **{frow.get('ICP')}** ({frow.get('ICP status')}). Lead score **{frow.get('Lead score') or '-'}**, "
            f"tier **{frow.get('Lead tier')}**. Segment {frow.get('Segment')} ({frow.get('Segment based on')}).",
            _table(["Signal", "Points"], breakdown) if breakdown else None, breakdown, ["Signal", "Points"],
            ["Reasoning: " + (frow.get("Reasoning") or "").replace("\n", " / "),
             f"Final list: {rel(out)}"])

    report = folder / "output" / f"trace_{domain.replace('.', '_')}.md"
    report.write_text(f"# Trace: {domain}\n\n" + "\n".join(tr.md) + "\n")
    console.print(f"[green]Trace saved:[/] {rel(report)}")
    return report
