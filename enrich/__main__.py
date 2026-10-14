"""Command line.

Two separate phases:
  python -m enrich icp leads.csv          phase 1: is it our ICP? (must-haves and exclusions only)
  python -m enrich signals leads          phase 2: fit, buying and weak signals, for companies that passed phase 1
  python -m enrich run leads.csv          both phases, one after the other
  python -m enrich ai leads --phase icp   answer phase 1's AI prompts with OpenAI (then --phase signals after phase 2)

Other commands:
  python -m enrich lists                  every list, its progress and disk use
  python -m enrich rules leads            re-apply rules to saved pages, no downloading (--phase icp|signals|both)
  python -m enrich explain acme.com       every answer and its proof for one company
  python -m enrich clean leads            delete raw pages that are no longer needed (asks first)
  python -m enrich prompts                list the AI prompts (one JSON file per data point in prompts/)
  python -m enrich directories            the free directories (YC, SEC, IRS): status, refresh, look up one company
  python -m enrich evaluate leads         compare results with the right answers (Expected columns or --labels)
  python -m enrich runs leads             the run log: when, what, counts and AI cost
  python -m enrich copy leads leads_cheap   clone a list without AI answers; compare leads leads_cheap shows differences
  python -m enrich trace acme.com         one company through every step, one at a time (--ai for the AI steps)
  python -m enrich waterfall acme.com     each must-have through every layer: rules, directories, AI, provider
  python -m enrich review leads           you check each data point's waterfall: right / wrong, saved for fixing and evaluate
  python -m enrich providers leads        Prospeo company data for data points still open (asks first)
  python -m enrich people leads           decision-makers at ICP companies from Prospeo, --emails for emails (asks first)
  python -m enrich costs leads            the actual AI cost per company in a list
  python -m enrich usage                  companies checked and AI cost this month, per list, and the budget left
  python -m enrich cache                  the shared company cache (--clean-older 90 offers to delete old entries)

Each list gets its own folder: lists/<list>/ with input.csv, raw/, cache.sqlite and output/.
Nothing is deleted without asking.
"""
import argparse
import json
import sys
from pathlib import Path

from rich.table import Table

from .pipeline import phase1_icp
from .pipeline import phase2_signals
from .ai import prompts as prompt_files
from .pipeline.common import LISTS, ai_prompts, console, human, list_dir, offer_cleanup, plain, rel
from .core.dashboard import ICON
from .core.inputs import normalize_domain
from .checks.rules import GATE_NAMES, SIGNALS, VALUE_NAMES
from .core.store import Store


def cmd_icp(args):
    phase1_icp.run(Path(args.csv), args.list, args.workers, args.max_requests, args.limit, args.refresh, args.keep, args.yes)


def cmd_signals(args):
    phase2_signals.run(list_dir(args.list), args.only_yes, args.workers, args.max_requests, args.refresh, args.keep, args.yes)


def cmd_run(args):
    folder = phase1_icp.run(Path(args.csv), args.list, args.workers, args.max_requests, args.limit, args.refresh, "all", False)
    console.rule()
    phase2_signals.run(folder, args.only_yes, args.workers, args.max_requests, args.refresh, args.keep, args.yes)


def cmd_ai(args):
    from .ai import runner as ai
    ai.run(list_dir(args.list), args.phase, args.limit, not args.no_web, args.redo, args.concurrency, args.yes,
           not args.no_search_signals, args.mode, args.all_layers)


def cmd_serve(args):
    import uvicorn
    from .core import settings
    key = settings.api_key()
    console.print(f"[bold]PuzzleLeadScoring API[/] on http://{args.host}:{args.port}  (interactive docs: /docs)\n"
                  f"Callers send the header  x-api-key: {key[:8]}...  (full key: python -m enrich settings)")
    uvicorn.run("enrich.integrations.api:app", host=args.host, port=args.port, log_level="info")


def cmd_mcp(args):
    from .integrations.mcp_server import main as mcp_main
    mcp_main()


def cmd_settings(args):
    import os
    from .core import settings
    for flag, key in ((args.openai_key, "OPENAI_API_KEY"), (args.clay_webhook, "CLAY_WEBHOOK_URL"),
                      (args.clay_webhook_token, "CLAY_WEBHOOK_TOKEN"), (args.clay_api_key, "CLAY_API_KEY"),
                      (args.anthropic_key, "ANTHROPIC_API_KEY")):
        if flag:
            settings.set_env(key, flag)
    for item in args.set or []:
        if "=" not in item:
            sys.exit(f"--set needs KEY=value, got '{item}'")
        k, v = item.split("=", 1)
        settings.set_env(k.strip(), v.strip())
    settings.api_key(rotate=args.rotate_api_key)
    settings.write_files()  # keep .env and .env.example listing every key
    t = Table(title=f"Settings in {rel(settings.ENV_FILE)} (blank copy for the team: {rel(settings.EXAMPLE_FILE)})",
              header_style="bold")
    for col in ("Group", "Key", "Status", "Used for"):
        t.add_column(col, overflow="fold")
    for sec, key, what, where, used in settings.ENV_KEYS:
        value = os.environ.get(key, "")
        status = ("[green]set[/] " + (value[:6] + "..." if key == "PUZZLE_API_KEY" and args.show_key is False else "")) if value \
            else ("[yellow]not set[/]" if used else "[dim]not used yet[/]")
        if key == "PUZZLE_API_KEY" and value and args.show_key:
            status = f"[green]set[/] {value}"
        t.add_row(sec, key, status, what)
    console.print(t)
    console.print("[dim]Show the full API key with --show-key. Set any key with --set KEY=value.[/]")


def cmd_pull(args):
    import asyncio
    from .integrations import clay
    from .integrations import service
    if not args.table_id:
        t = Table(title="Clay tables", header_style="bold")
        for col in ("Table id", "Name", "Workbook"):
            t.add_column(col)
        for row in clay.list_tables():
            t.add_row(row["id"], row["name"], row["workbook"])
        console.print(t)
        return

    async def go():
        status = service.start_from_clay(args.table_id, args.list, args.limit, signals=not args.icp_only, use_ai=args.ai,
                                         web=True, push_to_clay=args.push)
        console.print(f"Pulled {status['rows']} rows from Clay into list [bold]{status['list']}[/]; processing...")
        await service._jobs[status["list"]]
        console.print(service.list_status(status["list"]))
    asyncio.run(go())


def cmd_push(args):
    import asyncio
    from .integrations import service
    res = asyncio.run(service.push_list(list_dir(args.list).name, args.webhook, args.only_icp))
    console.print(f"Sent {res['sent']} rows to Clay, {res['failed']} failed. {'; '.join(res['errors'])}")


def cmd_rules(args):
    folder = list_dir(args.list)
    if args.phase in ("icp", "both"):
        phase1_icp.rerun_rules(folder)
    if args.phase in ("signals", "both"):
        phase2_signals.rerun_rules(folder)


def cmd_bench(args):
    from .pipeline import bench
    if args.compare:
        bench.compare(*args.compare)
    else:
        if not args.csv or not args.label:
            raise SystemExit("Give a CSV and --label (or --compare A B).")
        bench.run(Path(args.csv), args.label, args.workers, args.limit)


def cmd_jobs(args):
    """Read open jobs again for a list already downloaded: the company's job board (13 boards) or its careers page,
    from the saved pages. Free (only the boards' public feeds are fetched); then the rules re-run."""
    import asyncio
    from .sources.crawl import Crawler
    folder = list_dir(args.list)
    store = Store(folder)

    async def run():
        c = Crawler()
        before = after = 0
        try:
            for d in store.all_domains():
                rec = store.get_site(d) or {}
                if rec.get("status") != "live":
                    continue
                before += bool(rec.get("jobs_count"))
                pages = {k: store.load_page(d, k) or "" for k in rec.get("pages", {})}
                for k in ("ats", "ats_slug", "jobs_count", "jobs_url", "board_unreadable"):
                    rec.pop(k, None)
                jobs = await c.find_jobs(rec.get("final_url") or f"https://{d}/", pages, rec)
                store.save_jobs(d, jobs)
                store.save_site(d, rec)
                after += bool(jobs)
        finally:
            await c.close()
        return before, after
    before, after = asyncio.run(run())
    console.print(f"Companies with open jobs read: {before} before, {after} now. Re-running the rules...")
    phase1_icp.rerun_rules(folder)
    phase2_signals.rerun_rules(folder)


def cmd_render(args):
    """Pages saved before that only show their content after JavaScript runs (a "Loading blog..." placeholder, or
    almost no text) are opened again in the real browser and saved rendered. Free; then the rules re-run."""
    import asyncio
    from .sources.crawl import Crawler, slim_html, text_chars, unrendered
    folder = list_dir(args.list)
    store = Store(folder)
    only = normalize_domain(args.domain) if args.domain else None

    async def run():
        c = Crawler()
        done = []
        try:
            for d in store.all_domains():
                rec = store.get_site(d) or {}
                if rec.get("status") != "live" or (only and d != only):
                    continue
                for kind, url in rec.get("pages", {}).items():
                    old = store.load_page(d, kind) or ""
                    if not unrendered(old):
                        continue
                    f, err = await c.browser.fetch(url)
                    if f and f.status < 400 and text_chars(f.text) > text_chars(old) + 150:
                        store.save_page(d, kind, slim_html(f.text))
                        rec.setdefault("rendered_pages", []).append(kind)
                        store.save_site(d, rec)
                        done.append((d, kind, text_chars(old), text_chars(f.text)))
        finally:
            await c.close()
        return done
    done = asyncio.run(run())
    for d, kind, a, b in done:
        console.print(f"  {d} {kind}: {a} -> {b} characters of text")
    console.print(f"Rendered {len(done)} pages in the real browser. Re-running the rules...")
    if done:
        phase1_icp.rerun_rules(folder)
        phase2_signals.rerun_rules(folder)


def cmd_directories(args):
    from .sources import directories
    if args.refresh:
        console.print("Downloading the YC directory and the IRS non-profit list...")
        directories.refresh()
    if args.domain:
        domain = normalize_domain(args.domain) or args.domain
        found = directories.lookup(domain, args.company or "", args.legal_name, [args.place] if args.place else [])
        for name in directories.ORDER:
            hit = found.get(name)
            console.print(f"[bold]{name}[/]: " + ("not found" if not hit else
                          ", ".join(f"{k}={v}" for k, v in hit.items() if v not in (None, "", [], {}))))
        for name, err in found["errors"].items():
            console.print(f"[yellow]{name} not reachable: {err}[/]")
        return
    t = Table(title="Free directories (shared by all lists, in cache/)", title_justify="left", header_style="bold")
    for col in ("Directory", "Records", "Updated", "Size"):
        t.add_column(col)
    for row in directories.status():
        t.add_row(row["name"], row.get("count", "not downloaded yet"), row.get("updated", ""),
                  human(row["size"]) if row.get("size") else "")
    console.print(t)
    console.print("[dim]Downloaded on first use and refreshed automatically (YC weekly, IRS monthly, SEC lookups after 30 days). "
                  "Turn off for a run with --no-directories.[/]")


def cmd_evaluate(args):
    from .pipeline import evaluate
    if args.template:
        path = evaluate.write_template(Path(args.template))
        console.print(f"Labels template written: {path}. Fill one row per company (Expected ICP: YES/NO; "
                      f"Expected tier: Strong fit/Weak fit; Expected: <data point>: Yes/No), then run "
                      f"python -m enrich evaluate <list> --labels {path}")
        return
    if not args.list:
        raise SystemExit("Name a list: python -m enrich evaluate <list> [--labels labels.csv]")
    folder = list_dir(args.list)
    summary = evaluate.run(folder, Path(args.labels) if args.labels else None)
    from .core.runlog import log
    log(folder, "Evaluate", labels=args.labels or "columns in the list", **summary)


def cmd_trace(args):
    from .pipeline import trace
    trace.run(args.domain, args.company or "", args.employees or "", args.ai, args.mode, not args.no_pause, args.refresh, args.yes)


def cmd_datapoints(args):
    from .checks import datapoints
    if args.init:
        n = datapoints.write_defaults(overwrite=False)
        console.print(f"Wrote {n} missing data point files to {rel(datapoints.DIR)} (existing files kept).")
        return
    if args.show:
        sid = args.show.upper()
        cfg = datapoints.load().get(sid)
        if not cfg:
            raise SystemExit(f"No data point {args.show}. Use an id like M1, E3, F12, B4, W10.")
        p = datapoints.path(sid, cfg)
        console.print(f"[bold]{sid} {cfg['name']}[/]  ({rel(p)})")
        console.print_json(json.dumps(cfg["steps"]))
        return
    t = Table(title="Each data point's waterfall (edit the file to change it)", title_justify="left", header_style="bold")
    for c in ("Id", "Data point", "Rules", "Directories", "AI pages", "AI web", "Provider", "File"):
        t.add_column(c, overflow="fold")
    for row in datapoints.summary_rows():
        if args.group and not row[0].startswith({"must": "M", "exclusions": "E", "fit": "F", "buying": "B", "weak": "W"}[args.group]):
            continue
        t.add_row(*row)
    console.print(t)
    console.print("[dim]AI pages: if_open = when empty or Needs check, if_clue = only on a rule's clue, always, off. "
                  "AI web: after_pages = when the page answer found nothing, if_open = whenever still open, first = web only, off. "
                  "n/a = that layer can't answer this data point.[/]")


def cmd_waterfall(args):
    from .pipeline import waterfall
    waterfall.run(normalize_domain(args.domain) or args.domain, args.company or "", args.group, args.ai, args.providers,
                  args.yes, args.refresh)


def cmd_review(args):
    from .pipeline import review
    review.run(args.target, args.group, args.redo, args.refresh, args.only.split(",") if args.only else None)


def cmd_providers(args):
    from .providers import run
    run.company_step(list_dir(args.list), args.phase, args.limit, args.yes)


def cmd_people(args):
    from .providers import run
    run.people_step(list_dir(args.list), args.emails, args.per_company, args.strong_only, args.limit, args.yes)


def cmd_costs(args):
    folder = list_dir(args.list)
    store = Store(folder)
    since = 0.0
    from datetime import datetime
    if args.since:
        since = datetime.fromisoformat(args.since).timestamp()
    elif args.last_runs:
        from .core.runlog import read
        ai_runs = [r for r in read(folder) if r["step"].startswith("AI") and "seconds" in r][-args.last_runs:]
        if ai_runs:  # the log stores when a run ended and how long it took
            since = min(datetime.fromisoformat(r["time"]).timestamp() - r["seconds"] for r in ai_runs) - 1
            args.since = datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M:%S") + f", the last {len(ai_runs)} AI run(s)"
    costs = store.ai_costs(since)
    rows, _ = __import__("enrich.core.inputs", fromlist=["load_leads"]).load_leads(folder / "input.csv")
    names = {r["domain"]: r["company"] for r in rows}
    t = Table(title=f"Actual AI cost per company: {folder.name}" + (f" (answers since {args.since})" if args.since else ""),
              title_justify="left", header_style="bold")
    for c in ("Company", "ICP", "Phase 1", "Phase 2", "Total", "AI answers", "Web searches", "Reused (free)", "Tokens in / out"):
        t.add_column(c, justify="left" if c in ("Company", "ICP") else "right")
    total = 0.0
    for d, c in sorted(costs.items(), key=lambda x: -x[1]["total_usd"]):
        icp = (store.get_result(d, "icp") or {}).get("icp_verdict", "")
        t.add_row(names.get(d) or d, icp[:22], f"${c['phase1_usd']:.4f}", f"${c['phase2_usd']:.4f}", f"[bold]${c['total_usd']:.4f}[/]",
                  str(c["answers"]), str(c["web_searches"]), str(c["reused"]), f"{c['tokens_in']:,} / {c['tokens_out']:,}")
        total += c["total_usd"]
    console.print(t)
    n = len(costs)
    console.print(f"Total AI cost for {n} companies: [bold]${total:.4f}[/]" + (f", average ${total / n:.4f} per company" if n else "")
                  + ". Every AI answer's cost is saved with it; reused answers cost $0.")


def cmd_usage(args):
    from .core import usage
    r = usage.report(args.month)
    t = Table(title=f"Usage {r['month']}", title_justify="left", header_style="bold")
    for c in ("List", "Companies", "AI runs", "AI calls", "Web searches", "AI cost", "Provider credits"):
        t.add_column(c, justify="left" if c == "List" else "right")
    for row in r["lists"]:
        t.add_row(row["list"], str(row["companies_checked"]), str(row["ai_runs"]), str(row["ai_calls"]),
                  str(row["web_searches"]), f"${row['ai_cost_usd']:.4f}", f"{row['provider_credits']:g}")
    console.print(t)
    cap = r["budget_usd"]
    console.print(f"Total: {r['companies_checked']} companies checked, AI ${r['ai_cost_usd']:.4f}"
                  + (f"; monthly budget ${cap:.2f}, left ${r['budget_left_usd']:.2f}" if cap is not None
                     else "; no monthly budget set (AI_MONTHLY_BUDGET_USD in .env)"))


def cmd_cache(args):
    from .core import shared
    if args.import_lists:
        n = shared.import_lists(LISTS)
        console.print(f"Added {n} companies' pages from existing lists to the shared cache (linked, no extra disk).")
    if args.clean_older:
        old, answers = shared.stale(args.clean_older)
        console.print(f"Older than {args.clean_older:g} days: {len(old)} companies' pages and {answers} AI answers. "
                      "Lists keep their own copies, so finished lists aren't affected.")
        if not (old or answers):
            return
        ok = args.yes
        if not ok and sys.stdin.isatty():
            try:
                from rich.prompt import Confirm
                ok = Confirm.ask("Delete them from the shared cache?", default=False)
            except (EOFError, KeyboardInterrupt):
                ok = False
        if not ok:
            console.print("Kept everything.")
            return
        n, a = shared.delete_stale(args.clean_older)
        console.print(f"[green]Deleted {n} companies' pages and {a} AI answers from the shared cache.[/]")
        return
    s = shared.status()
    console.print(f"Shared company cache ({rel(shared.COMPANIES.parent)}): {s['companies']} companies, pages {human(s['pages_bytes'])} "
                  f"(shared with lists by links, not copies); {s['ai_answers']} AI answers ({human(s['ai_bytes'])}). "
                  f"{'On' if shared.enabled() else 'Off (SHARED_CACHE=off)'}.")


def cmd_copy(args):
    """Clone a list (pages, rules, directory findings) without its AI answers, e.g. to test the cheap mode."""
    import shutil
    src, dst = list_dir(args.list), LISTS / args.new_name
    if dst.exists():
        raise SystemExit(f"A list called '{dst.name}' already exists.")
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("runs.jsonl", "ai_batch_*", "evaluation.csv"))
    store = Store(dst)
    if not args.keep_ai:
        store.db.execute("DELETE FROM ai_answers")
        store.db.commit()
        phase1_icp.rerun_rules(dst)
        phase2_signals.rerun_rules(dst)
    console.print(f"Copied {src.name} to {dst.name}" + ("" if args.keep_ai else " without AI answers") + ".")


def cmd_compare(args):
    """Every answer that differs between two lists of the same companies (e.g. accurate vs cheap mode)."""
    import csv
    from .checks.rules import signal_column
    a, b = list_dir(args.list_a), list_dir(args.list_b)
    def rows(folder):
        with open(folder / "output" / f"{folder.name}_enriched.csv", newline="") as f:
            r = list(csv.DictReader(f))
        dcol = next(c for c in r[0] if c.strip().lower() in ("domain", "website", "url", "company domain"))
        return {normalize_domain(x[dcol]): x for x in r}
    ra, rb = rows(a), rows(b)
    cols = ["ICP", "Lead tier", "Lead score", "Funding stage"] + [signal_column(s) for s in SIGNALS]
    diffs, same = [], 0
    for d in ra.keys() & rb.keys():
        for c in cols:
            va, vb = ra[d].get(c, ""), rb[d].get(c, "")
            if va == vb:
                same += 1
            else:
                diffs.append((d, c, va or "(empty)", vb or "(empty)"))
    t = Table(title=f"{a.name} vs {b.name}: {len(diffs)} answers differ, {same} the same", title_justify="left",
              header_style="bold")
    for c in ("Company", "Answer", a.name, b.name):
        t.add_column(c, overflow="fold")
    for row in sorted(diffs):
        t.add_row(*row)
    console.print(t)


def cmd_runs(args):
    from .core.runlog import read
    folder = list_dir(args.list)
    rows = read(folder)
    if not rows:
        console.print("No runs logged yet for this list.")
        return
    t = Table(title=f"Runs: {folder.name}", title_justify="left", header_style="bold")
    for c in ("When", "Step", "Companies", "Result", "AI cost", "Time"):
        t.add_column(c)
    for r in rows:
        result = ""
        if r.get("verdicts"):
            result = ", ".join(f"{k} {v}" for k, v in r["verdicts"].items())
        elif "strong_fit" in r:
            result = f"Strong {r['strong_fit']}, Weak {r['weak_fit']}"
        elif "ai_calls" in r:
            result = f"{r['ai_calls']} calls, {r['web_searches']} searches" + (f", {r['errors']} errors" if r.get("errors") else "")
        elif "icp" in r:
            result = f"ICP right {r['icp']['right']}, wrong {r['icp']['wrong']} (wrongly NO {r['icp']['wrong_no']})"
        t.add_row(r["time"].replace("T", " "), r["step"], str(r.get("companies", "")), result,
                  f"${r['cost_usd']:.4f}" if "cost_usd" in r else "", f"{r['seconds']} s" if "seconds" in r else "")
    console.print(t)
    total = sum(r.get("cost_usd", 0) for r in rows)
    console.print(f"Total AI cost for this list: ${total:.4f}")


def cmd_clean(args):
    offer_cleanup(Store(list_dir(args.list)), args.keep, args.yes)


def cmd_lists(args):
    t = Table(title="Lists", header_style="bold")
    for col in ("List", "Companies", "ICP: yes", "Needs AI check", "Not ICP", "Not reached", "Signals done",
                "Raw pages kept", "Disk", "Folder"):
        t.add_column(col, justify="left" if col in ("List", "Folder") else "right")
    for folder in sorted(d for d in LISTS.glob("*") if (d / "cache.sqlite").exists()):
        store = Store(folder)
        domains = store.all_domains()
        verdicts = [(store.get_result(d, "icp") or {}).get("icp_verdict", "") for d in domains]
        t.add_row(folder.name, str(len(domains)),
                  str(sum(v.startswith("Yes") for v in verdicts)), str(verdicts.count("Needs AI check")),
                  str(verdicts.count("No")), str(verdicts.count("Unknown: site not reached")),
                  str(sum(1 for d in domains if store.get_result(d, "signals"))),
                  str(sum(1 for d in domains if store.pages_size(d))), human(store.disk_usage()["total"]), rel(folder) + "/")
    console.print(t)


def _signal_table(title: str, r: dict, groups: tuple[str, ...], show_all: bool) -> None:
    t = Table(title=title, title_justify="left", expand=True, header_style="bold dim")
    t.add_column("", width=2)
    t.add_column("Signal", width=36)
    t.add_column("Proof / note", ratio=1)
    for sid, (grp, label) in SIGNALS.items():
        s = r["signals"].get(sid)
        if grp not in groups or not s or (s["value"] == "unknown" and not show_all):
            continue
        proof = s["evidence"] + (f"  [dim]{plain(s['note'])}[/]" if s["note"] else "") + (f"\n[blue]{s['url']}[/]" if s["url"] else "")
        t.add_row(ICON[s["value"]], label, proof)
    console.print(t)


def cmd_explain(args):
    d = normalize_domain(args.domain) or args.domain
    folders = [list_dir(args.list)] if args.list else sorted(
        (f for f in LISTS.glob("*") if (f / "cache.sqlite").exists()), key=lambda f: f.stat().st_mtime, reverse=True)
    found = [(f, Store(f)) for f in folders]
    found = [(f, st) for f, st in found if st.get_result(d, "icp")]
    if not found:
        sys.exit(f"No saved result for {d}. Run phase 1 first.")
    folder, store = found[0]
    if len(found) > 1:
        console.print(f"[dim]{d} is in {len(found)} lists ({', '.join(f.name for f, _ in found)}); "
                      f"showing {folder.name}. Use --list to pick.[/]")
    icp = store.get_result(d, "icp")
    sig = store.get_result(d, "signals")
    console.rule(f"[bold]{d}[/]  {icp['company']}   [dim]list: {folder.name}[/]")
    c = icp["crawl"]
    console.print(f"Site: {c['status']} {c.get('error') or ''}  {c.get('final_url') or ''}")
    console.print(f"[bold]Is it ICP?[/] {icp.get('icp_verdict', GATE_NAMES.get(icp['gate'], icp['gate']))}  "
                  f"{plain(icp['exclusion_reason'])}")
    console.print(f"Entity: {icp['entity_type']}  ({icp['legal_name'] or 'no legal name found'})")
    _signal_table("Phase 1: must-haves", icp, ("must",), args.all)
    _signal_table("Phase 1: exclusions", icp, ("exclusion",), args.all)
    console.print(f"Phase 1 data points for AI: {'; '.join(prompt_files.name(q) for q in ai_prompts(icp)) or 'none'}")
    if not sig:
        console.print("[dim]Phase 2 (signals) hasn't run for this company yet.[/]")
        return
    console.print(f"\nFunding: {sig['funding_stage']}   Partners: {', '.join(sig['partners']) or '-'}   "
                  f"Competitors: {', '.join(sig['competitors']) or '-'}   Open roles: {sig['crawl'].get('jobs_count')}")
    _signal_table("Phase 2: fit signals", sig, ("fit",), args.all)
    _signal_table("Phase 2: buying signals", sig, ("buying",), args.all)
    _signal_table("Phase 2: weak fit signals", sig, ("weak",), args.all)
    console.print(f"Phase 2 data points for AI ({len(ai_prompts(sig))} prompts): "
                  f"{'; '.join(prompt_files.name(q) for q in ai_prompts(sig)) or 'none'}")


def cmd_prompts(args):
    shared, prompts = prompt_files.load()
    if not args.show:
        t = Table(title=f"AI prompts ({len(prompts)} files in {rel(prompt_files.PROMPTS_DIR)}/)", header_style="bold")
        for col in ("Prompt file", "Data point", "Phase", "Step 1 reads", "Step 2 web search", "Runs when"):
            t.add_column(col, overflow="fold")
        stage_names = {"site_text": "website", "job_posts": "job posts", "search_results": "web search", "final": "final output"}
        for pid, p in prompts.items():
            phase = "1 ICP check" if pid.startswith(("must_haves/", "exclusions/")) else "2 signals"
            web = {"fallback": "if step 1 finds nothing", "primary": "always (web first)", "off": "never"}[p["web_search"]["mode"]]
            t.add_row(f"{pid}.json", p["data_point"], phase, stage_names.get(p["stage"], p["stage"]), web, p["run_when"])
        console.print(t)
        pv = prompt_files.provider()
        console.print(f"Shared rules for all prompts: {rel(prompt_files.PROMPTS_DIR / '_shared.json')}  "
                      f"(provider: {pv['name']}; step 1: {pv['small']}, step 2 web search: {pv['web']})")
        return
    pid = args.show.removesuffix(".json")
    if pid not in prompts:
        sys.exit(f"No prompt '{pid}'. Run 'python -m enrich prompts' to list them.")
    company, domain, facts, context = "Example Co", "example.com", {}, {"home": "(page text goes here)"}
    if args.domain:
        d = normalize_domain(args.domain) or args.domain
        phase = "icp" if pid.startswith(("must_haves/", "exclusions/")) else "signals"
        folders = [list_dir(args.list)] if args.list else sorted(LISTS.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
        r = next((x for f in folders if (f / "cache.sqlite").exists() and (x := Store(f).get_result(d, phase))), None)
        if not r:
            sys.exit(f"No saved {'phase 1' if phase == 'icp' else 'phase 2'} result for {d}.")
        company, domain, context = r["company"], d, r.get("ai_context", {})
        facts = {SIGNALS[k][1]: f"{VALUE_NAMES[v['value']]} ({v['evidence'][:120]})" for k, v in r["signals"].items() if v["value"] in ("yes", "no")}
    if args.step == 2:
        legal = r.get("legal_name") if args.domain else None
        msg = prompt_files.build_search(pid, company, domain, facts, legal)
        console.rule(f"[bold]{prompt_files.name(pid)}[/]  step 2: web search   model: {msg['model']}")
        console.print(f"[bold blue]--- tools ---[/]\n{msg['tools']}", markup=True, highlight=False)
        for part in ("system", "instruction"):
            console.print(f"[bold blue]--- {part} ---[/]")
            console.print(msg[part], markup=False, highlight=False)
        return
    msg = prompt_files.build(pid, company, domain, facts, context)
    console.rule(f"[bold]{prompt_files.name(pid)}[/]  step 1: our data   model: {msg['model']}")
    for part in ("system", "context", "instruction"):
        console.print(f"[bold blue]--- {part} ---[/]")
        console.print(msg[part], markup=False, highlight=False)


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="python -m enrich")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def speed(p):
        p.add_argument("--workers", type=int, default=50, help="companies processed at the same time")
        p.add_argument("--max-requests", type=int, default=200, help="web requests in flight at the same time")
        p.add_argument("--refresh", action="store_true", help="download again even if cached")
        p.add_argument("--keep", choices=["useful", "all", "none"], default="useful",
                       help="after the run, offer to delete raw pages: useful (default: companies that are not ICP), "
                            "none (all raw pages), all (don't offer). Always asks first.")
        p.add_argument("--yes", action="store_true", help="delete without asking (for scheduled runs)")
        p.add_argument("--no-directories", action="store_true", help="skip the free directories (YC, SEC, IRS)")
        p.add_argument("--no-browser", action="store_true",
                       help="don't use the real browser (Chrome) for sites that need JavaScript or check for bots")

    p = sub.add_parser("icp", help="phase 1: is it our ICP? must-haves and exclusions only")
    p.add_argument("csv")
    p.add_argument("--list", help="list folder name (default: the CSV file name)")
    p.add_argument("--limit", type=int, default=0, help="only the first N rows")
    speed(p)
    p.set_defaults(fn=cmd_icp)

    p = sub.add_parser("signals", help="phase 2: fit, buying and weak signals for companies that passed phase 1")
    p.add_argument("list", help="list name (or its CSV file)")
    p.add_argument("--only-yes", action="store_true", help="leave out companies still marked 'Needs AI check'")
    speed(p)
    p.set_defaults(fn=cmd_signals)

    p = sub.add_parser("run", help="both phases, one after the other")
    p.add_argument("csv")
    p.add_argument("--list", help="list folder name (default: the CSV file name)")
    p.add_argument("--limit", type=int, default=0, help="only the first N rows")
    p.add_argument("--only-yes", action="store_true", help="phase 2 leaves out 'Needs AI check' companies")
    speed(p)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("ai", help="answer the queued AI prompts with OpenAI (asks before spending)")
    p.add_argument("list", help="list name (or its CSV file)")
    p.add_argument("--phase", choices=["icp", "signals"], default="icp",
                   help="icp: settle must-haves and exclusions (run after phase 1); signals: run after phase 2")
    p.add_argument("--limit", type=int, default=0, help="only the first N companies that need AI")
    p.add_argument("--no-web", action="store_true", help="skip step 2 (web search)")
    p.add_argument("--mode", choices=["accurate", "cheap"],
                   help="accurate: one AI call per data point (default, set in _shared.json). cheap: a company's data "
                        "points grouped, up to 5 per call, and its web questions share one web search call")
    p.add_argument("--all-layers", action="store_true",
                   help="test mode: ask every data point every AI layer it has, even when an earlier layer decided it")
    p.add_argument("--no-search-signals", action="store_true",
                   help="phase 2: skip the signals only web search can answer (recent funding, new investors, "
                        "QuickBooks complaints...), the most expensive part")
    p.add_argument("--redo", action="store_true", help="ask again even where an answer is saved")
    p.add_argument("--concurrency", type=int, default=24,
                   help="most AI calls at the same time (starts at 8, grows while OpenAI allows, halves on a rate limit)")
    p.add_argument("--yes", action="store_true", help="don't ask before spending")
    p.set_defaults(fn=cmd_ai)

    p = sub.add_parser("serve", help="run the HTTP API for Clay and other tools")
    p.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this PC only; 0.0.0.0 = reachable on the network")
    p.add_argument("--port", type=int, default=8787)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("mcp", help="run the MCP server (stdio) for Claude Code / Claude Desktop")
    p.set_defaults(fn=cmd_mcp)

    p = sub.add_parser("settings", help="show the API key and set keys once (saved in .env)")
    p.add_argument("--openai-key", help="save the OpenAI API key")
    p.add_argument("--clay-webhook", help="save the default Clay table webhook URL")
    p.add_argument("--clay-webhook-token", help="save the Clay webhook auth token, if the webhook has one")
    p.add_argument("--clay-api-key", help="save the Clay Public API key (from `clay api-keys create`)")
    p.add_argument("--anthropic-key", help="save the Anthropic API key (alternative AI provider)")
    p.add_argument("--set", action="append", metavar="KEY=value", help="save any key, e.g. --set APOLLO_API_KEY=...")
    p.add_argument("--rotate-api-key", action="store_true", help="replace the API key (callers need the new one)")
    p.add_argument("--show-key", action="store_true", help="print the full API key")
    p.set_defaults(fn=cmd_settings)

    p = sub.add_parser("pull", help="read a Clay table and run it as a new list (no table id: list Clay tables)")
    p.add_argument("table_id", nargs="?")
    p.add_argument("--list", help="list name (default clay_<table id>)")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--icp-only", action="store_true", help="phase 1 only")
    p.add_argument("--ai", action="store_true", help="also run the AI step (costs money)")
    p.add_argument("--push", action="store_true", help="push results to the Clay webhook when done")
    p.set_defaults(fn=cmd_pull)

    p = sub.add_parser("push", help="send a list's results to a Clay table webhook")
    p.add_argument("list")
    p.add_argument("--webhook", help="Clay webhook URL (default: saved setting)")
    p.add_argument("--only-icp", action="store_true", help="only rows where ICP = YES")
    p.set_defaults(fn=cmd_push)

    p = sub.add_parser("lists", help="every list, its progress and disk use")
    p.set_defaults(fn=cmd_lists)

    p = sub.add_parser("rules", help="re-apply rules to saved pages, no downloading")
    p.add_argument("list", help="list name (or its CSV file)")
    p.add_argument("--phase", choices=["icp", "signals", "both"], default="both")
    p.add_argument("--no-directories", action="store_true", help="skip the free directories (YC, SEC, IRS)")
    p.set_defaults(fn=cmd_rules)

    p = sub.add_parser("render", help="open pages that need JavaScript (e.g. a 'Loading blog...' page) in the real browser; free")
    p.add_argument("list", help="list name (or its CSV file)")
    p.add_argument("--domain", help="only this company")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("bench", help="time the free steps on a CSV (fresh list bench_<label>), or compare two runs; free")
    p.add_argument("csv", nargs="?")
    p.add_argument("--label", help="name of this run, e.g. before / after")
    p.add_argument("--workers", type=int, default=50)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--compare", nargs=2, metavar=("A", "B"), help="compare two saved runs")
    p.set_defaults(fn=cmd_bench)

    p = sub.add_parser("jobs", help="read open jobs again (13 job boards or the careers page) for a downloaded list; free")
    p.add_argument("list", help="list name (or its CSV file)")
    p.set_defaults(fn=cmd_jobs)

    p = sub.add_parser("evaluate", help="compare results with the right answers (accuracy, and every disagreement)")
    p.add_argument("list", nargs="?", help="list name")
    p.add_argument("--labels", help="CSV with Domain and Expected columns (default: Expected columns in the list itself)")
    p.add_argument("--template", metavar="FILE", help="write a blank labels file to fill in")
    p.set_defaults(fn=cmd_evaluate)

    p = sub.add_parser("trace", help="one company through every step of the workflow, one step at a time")
    p.add_argument("domain")
    p.add_argument("--company", help="company name")
    p.add_argument("--employees", help="employee count")
    p.add_argument("--ai", action="store_true", help="also run the AI steps (shows the cost and asks first)")
    p.add_argument("--mode", choices=["accurate", "cheap"], help="AI mode (default from _shared.json)")
    p.add_argument("--no-pause", action="store_true", help="print every step without waiting for Enter")
    p.add_argument("--refresh", action="store_true", help="download the website again")
    p.add_argument("--yes", action="store_true", help="run the AI steps without asking about the cost")
    p.set_defaults(fn=cmd_trace)

    p = sub.add_parser("datapoints", help="each data point's waterfall settings (config/datapoints/), or one in full")
    p.add_argument("--show", metavar="ID", help="one data point's settings, e.g. M3")
    p.add_argument("--group", choices=["must", "exclusions", "fit", "buying", "weak"])
    p.add_argument("--init", action="store_true", help="write any missing data point files with today's defaults")
    p.set_defaults(fn=cmd_datapoints)

    p = sub.add_parser("waterfall", help="one company, each data point through every layer: rules, directories, AI, provider")
    p.add_argument("domain")
    p.add_argument("--company", help="company name")
    p.add_argument("--group", choices=["must", "exclusions", "fit", "buying", "weak", "all"], default="must")
    p.add_argument("--ai", action="store_true", help="run the AI layers (shows the cost and asks first)")
    p.add_argument("--providers", action="store_true", help="run the provider layer: Prospeo (1 credit, asks first)")
    p.add_argument("--refresh", action="store_true", help="download the website again")
    p.add_argument("--yes", action="store_true", help="don't ask before spending")
    p.set_defaults(fn=cmd_waterfall)

    p = sub.add_parser("review", help="check each data point's waterfall yourself and mark it right or wrong")
    p.add_argument("target", help="a list name, or one company's domain")
    p.add_argument("--group", choices=["must", "exclusions", "fit", "buying", "weak", "all"], default="must")
    p.add_argument("--redo", action="store_true", help="show data points you already reviewed again")
    p.add_argument("--only", metavar="IDS", help="only these data points, e.g. M1 or M1,M2 (every company in the list)")
    p.add_argument("--refresh", action="store_true", help="download the website again (one company only)")
    p.set_defaults(fn=cmd_review)

    p = sub.add_parser("providers", help="Prospeo company data for open data points (asks before spending credits)")
    p.add_argument("list")
    p.add_argument("--phase", choices=["icp", "signals"], default="icp")
    p.add_argument("--limit", type=int, default=0, help="only the first N companies")
    p.add_argument("--yes", action="store_true", help="don't ask before spending")
    p.set_defaults(fn=cmd_providers)

    p = sub.add_parser("people", help="decision-makers at ICP companies from Prospeo (asks before spending credits)")
    p.add_argument("list")
    p.add_argument("--emails", action="store_true", help="also reveal verified emails (1 credit each)")
    p.add_argument("--per-company", type=int, default=3, help="contacts kept per company (default 3)")
    p.add_argument("--strong-only", action="store_true", help="only Strong fit companies")
    p.add_argument("--limit", type=int, default=0, help="only the first N companies")
    p.add_argument("--yes", action="store_true", help="don't ask before spending")
    p.set_defaults(fn=cmd_people)

    p = sub.add_parser("costs", help="the actual AI cost per company in a list (phase 1, phase 2, web searches)")
    p.add_argument("list")
    p.add_argument("--since", metavar="TIME", help="only answers saved since then, e.g. '2026-10-10 14:00'")
    p.add_argument("--last-runs", type=int, metavar="N", help="only the last N AI runs, e.g. 2 for phase 1 + phase 2")
    p.set_defaults(fn=cmd_costs)

    p = sub.add_parser("usage", help="companies checked and AI cost per list for a month, and the monthly budget")
    p.add_argument("--month", help="e.g. 2026-10 (default: this month)")
    p.set_defaults(fn=cmd_usage)

    p = sub.add_parser("cache", help="the shared company cache: size, or delete old entries (asks first)")
    p.add_argument("--clean-older", type=float, metavar="DAYS", help="offer to delete entries older than DAYS")
    p.add_argument("--import", dest="import_lists", action="store_true", help="add pages your existing lists already downloaded")
    p.add_argument("--yes", action="store_true", help="delete without asking")
    p.set_defaults(fn=cmd_cache)

    p = sub.add_parser("copy", help="clone a list without its AI answers (to compare AI modes)")
    p.add_argument("list")
    p.add_argument("new_name")
    p.add_argument("--keep-ai", action="store_true", help="keep the AI answers too")
    p.set_defaults(fn=cmd_copy)

    p = sub.add_parser("compare", help="every answer that differs between two lists of the same companies")
    p.add_argument("list_a")
    p.add_argument("list_b")
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("runs", help="the run log of a list: when, what, counts and AI cost")
    p.add_argument("list")
    p.set_defaults(fn=cmd_runs)

    p = sub.add_parser("directories", help="free directories (YC, SEC, IRS): status, refresh, look up one company")
    p.add_argument("domain", nargs="?", help="look up one company, e.g. puzzle.io")
    p.add_argument("--company", help="company name, for the SEC and IRS lookup")
    p.add_argument("--legal-name", help="legal name as on the company's site, e.g. 'Puzzle Financial Inc.'")
    p.add_argument("--place", help="address text from the site, e.g. 'San Francisco, CA 94107'")
    p.add_argument("--refresh", action="store_true", help="download the YC directory and IRS list again now")
    p.set_defaults(fn=cmd_directories)

    p = sub.add_parser("clean", help="delete raw pages that are no longer needed (asks first)")
    p.add_argument("list", help="list name (or its CSV file)")
    p.add_argument("--keep", choices=["useful", "none"], default="useful",
                   help="useful: companies that are not ICP; none: all raw pages")
    p.add_argument("--yes", action="store_true", help="delete without asking")
    p.set_defaults(fn=cmd_clean)

    p = sub.add_parser("explain", help="every answer and its proof for one company")
    p.add_argument("domain")
    p.add_argument("--list", help="which list to look in (default: newest list that has it)")
    p.add_argument("--all", action="store_true", help="include unknown signals")
    p.set_defaults(fn=cmd_explain)

    p = sub.add_parser("prompts", help="list the AI prompts, or show the full message for one")
    p.add_argument("--show", help="prompt file to show, e.g. exclusions/nonprofit_or_government")
    p.add_argument("--domain", help="fill the message with this company's saved page text")
    p.add_argument("--list", help="which list to look in for --domain")
    p.add_argument("--step", type=int, choices=[1, 2], default=1, help="1: our saved data, 2: web search")
    p.set_defaults(fn=cmd_prompts)

    args = ap.parse_args(argv)
    if getattr(args, "no_browser", False):
        from .sources import crawl
        crawl.BROWSER_ENABLED = False
    if getattr(args, "no_directories", False):
        from .sources import directories
        directories.ENABLED = False
    args.fn(args)


if __name__ == "__main__":
    main()
