"""Command line.

Two separate phases:
  python -m enrich icp leads.csv          phase 1: is it our ICP? (must-haves and exclusions only)
  python -m enrich signals leads          phase 2: fit, buying and weak signals, for companies that passed phase 1
  python -m enrich run leads.csv          both phases, one after the other

Other commands:
  python -m enrich lists                  every list, its progress and disk use
  python -m enrich rules leads            re-apply rules to saved pages, no downloading (--phase icp|signals|both)
  python -m enrich explain acme.com       every answer and its proof for one company
  python -m enrich clean leads            delete raw pages that are no longer needed (asks first)
  python -m enrich prompts                list the AI prompts (one JSON file per data point in prompts/)

Each list gets its own folder: lists/<list>/ with input.csv, raw/, cache.sqlite and output/.
Nothing is deleted without asking.
"""
import argparse
import sys
from pathlib import Path

from rich.table import Table

from . import phase1_icp, phase2_signals
from . import prompts as prompt_files
from .common import LISTS, ai_prompts, console, human, list_dir, offer_cleanup, plain, rel
from .dashboard import ICON
from .inputs import normalize_domain
from .rules import GATE_NAMES, SIGNALS, VALUE_NAMES
from .store import Store


def cmd_icp(args):
    phase1_icp.run(Path(args.csv), args.list, args.workers, args.max_requests, args.limit, args.refresh, args.keep, args.yes)


def cmd_signals(args):
    phase2_signals.run(list_dir(args.list), args.only_yes, args.workers, args.max_requests, args.refresh, args.keep, args.yes)


def cmd_run(args):
    folder = phase1_icp.run(Path(args.csv), args.list, args.workers, args.max_requests, args.limit, args.refresh, "all", False)
    console.rule()
    phase2_signals.run(folder, args.only_yes, args.workers, args.max_requests, args.refresh, args.keep, args.yes)


def cmd_rules(args):
    folder = list_dir(args.list)
    if args.phase in ("icp", "both"):
        phase1_icp.rerun_rules(folder)
    if args.phase in ("signals", "both"):
        phase2_signals.rerun_rules(folder)


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
        console.print(f"Shared rules for all prompts: {rel(prompt_files.PROMPTS_DIR / '_shared.json')}  "
                      f"(models: {', '.join(f'{k} = {v}' for k, v in shared['models'].items())})")
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

    p = sub.add_parser("lists", help="every list, its progress and disk use")
    p.set_defaults(fn=cmd_lists)

    p = sub.add_parser("rules", help="re-apply rules to saved pages, no downloading")
    p.add_argument("list", help="list name (or its CSV file)")
    p.add_argument("--phase", choices=["icp", "signals", "both"], default="both")
    p.set_defaults(fn=cmd_rules)

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
    args.fn(args)


if __name__ == "__main__":
    main()
