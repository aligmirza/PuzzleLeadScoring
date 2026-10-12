"""The waterfall for each data point of one company: every layer in order, what it found, and which one decided.

    python -m enrich waterfall linear.app                          the 4 must-haves, free layers
    python -m enrich waterfall linear.app --ai --providers         also AI and Prospeo (each asks before spending)
    python -m enrich waterfall linear.app --group exclusions       must, exclusions, fit, buying, weak or all

Layers, cheapest first; a layer only runs when the ones before it left the data point open:
    1 Rules on the scraped pages      free
    2 Free directories (YC, SEC, IRS) free
    3 AI reading our pages            about $0.0003 a question
    4 AI web search                   about $0.011 a search
    5 Provider (Prospeo)              1 credit per company (covers several data points at once)
Saved as lists/trace_<domain>/output/waterfall_<domain>.md
"""
from __future__ import annotations

import asyncio
import copy

from rich.panel import Panel
from rich.table import Table

from ..ai import prompts as P
from ..checks import datapoints
from ..checks.rules import SIGNALS, UNKNOWN, VALUE_NAMES, extract, signal_column
from ..core.store import Store
from ..providers import apply as provider_apply
from ..providers import prospeo
from ..engine import layers as L
from . import trace
from .common import LISTS, console, directory_finder, rel, run_rules

GROUPS = {"must": ("must",), "exclusions": ("exclusion",), "fit": ("fit",), "buying": ("buying",), "weak": ("weak",),
          "all": ("must", "exclusion", "fit", "buying", "weak")}
DIRECTORY_COVERS, PROVIDER_SIDS = L.DIRECTORY_COVERS, L.PROVIDER_COVERS
LAYERS = [layer.name for layer in L.ORDER]


def _cell(v: dict | None) -> tuple[str, str]:
    if not v:
        return "", ""
    return VALUE_NAMES[v["value"]], (v.get("evidence") or v.get("note") or "")[:150]


def _provider_view(sid: str, bought: dict, phase: str) -> dict | None:
    """What Prospeo alone says about this data point (applied to an empty answer sheet)."""
    if not bought.get("prospeo"):
        return None
    blank = {"signals": {k: {"value": UNKNOWN, "evidence": "", "url": "", "note": "", "method": "code"} for k in SIGNALS},
             "crawl": {}, "facts": {}}
    out = provider_apply.apply(copy.deepcopy(blank), bought, phase)["signals"][sid]
    return out if out["value"] != UNKNOWN else {"value": UNKNOWN, "evidence": "Prospeo has nothing for this", "note": ""}


def run(domain: str, company: str = "", group: str = "must", use_ai: bool = False, use_providers: bool = False,
        assume_yes: bool = False, refresh: bool = False) -> None:
    groups = GROUPS[group]
    phase = "icp" if set(groups) <= {"must", "exclusion"} else "signals"
    console.print(f"[dim]Preparing {domain}: website, rules and directories (free)...[/]")
    console.quiet = True
    try:
        trace.run(domain, company, "", False, None, pause=False, refresh=refresh, assume_yes=assume_yes)
    finally:
        console.quiet = False
    folder = LISTS / f"trace_{domain.replace('.', '_')}"
    store = Store(folder)
    record = store.get_site(domain)
    row = {"domain": domain, "company": company, "employees": "", "input": {}}
    if not record or record.get("status") != "live":
        console.print(f"[red]{domain}: website not reached ({(record or {}).get('error')}). Only providers could help.[/]")
    phases = ["icp"] if phase == "icp" else ["icp", "signals"]

    if use_ai:
        for ph in phases:
            if store.get_result(domain, ph):
                if asyncio.run(trace._ai_step(store, domain, ph, None, assume_yes)):
                    run_rules(store, row, record, ph)
    if use_providers:
        if not prospeo.enabled():
            console.print("[yellow]Provider layer skipped: set PROSPEO_API_KEY (python -m enrich settings --set PROSPEO_API_KEY=...).[/]")
        else:
            from ..providers.run import _balance, _confirm, fetch_one, has_prospeo, save_one, write_contacts
            if not has_prospeo(store, domain) and _confirm(
                    f"[bold]Prospeo[/] for {domain}: 1 credit at most (one team search gives the company record and its "
                    f"people; free if nothing is found). {_balance()}.", assume_yes):
                from ..providers.run import size_of
                got = fetch_one(domain, size_of(store, domain))
                save_one(store, domain, got)
                write_contacts(folder, store)
                from ..core.runlog import log
                log(folder, "Provider: Prospeo company data and people (waterfall)", companies=1,
                    matched=bool(got["company"]), provider_credits=got["credits"])
        for ph in phases:
            if store.get_result(domain, ph) and record:
                run_rules(store, row, record, ph)

    ctx = context(store, domain, company)
    md = [f"# Waterfall: {domain}", ""]
    for sid, (g, _) in SIGNALS.items():
        if g not in groups:
            continue
        dp = data_point(ctx, sid)
        if not dp:
            continue
        for part in render(dp):
            console.print(part)
        md += markdown(dp)
    icp = store.get_result(domain, "icp") or {}
    console.print(f"ICP verdict: [bold]{icp.get('icp_verdict')}[/]" + (f" ({icp.get('exclusion_reason')[:120]})" if icp.get("exclusion_reason") else ""))
    from ..providers.run import show_people
    people = show_people(store, domain)
    if people:
        console.print("[bold]Contacts (Prospeo)[/]")
        console.print(people)
    out = folder / "output" / f"waterfall_{domain.replace('.', '_')}.md"
    out.write_text("\n".join(md + [f"ICP verdict: **{icp.get('icp_verdict')}**", ""]))
    console.print(f"[green]Waterfall saved:[/] {rel(out)}")


DECIDERS = [layer.short for layer in L.ORDER]


def context(store: Store, domain: str, company: str = "") -> dict:
    """Everything the per-data-point waterfall needs for one company, loaded once."""
    record = store.get_site(domain)
    pages = {k: v for k, v in ((k, store.load_page(domain, k)) for k in (record or {}).get("pages", {})) if v}
    row = {"domain": domain, "company": company or ((record or {}).get("input") or {}).get("company", ""), "employees": "", "input": {}}
    live = bool(record) and record.get("status") == "live" and bool(pages)
    ctx = {"store": store, "domain": domain, "company": row["company"], "record": record, "row": row, "live": live,
           "bought": store.get_provider(domain), "by_signal": P.by_signal(), "extract": {}}
    if live:
        jobs = store.load_jobs(domain)
        for ph in ("icp", "signals"):
            if store.get_result(domain, ph):
                ctx["extract"][ph] = (extract(domain, row["company"], "", record, pages, jobs, ph)["signals"],
                                      extract(domain, row["company"], "", record, pages, jobs, ph, directory_finder(store, row))["signals"])
    return ctx


def data_point(ctx: dict, sid: str) -> dict | None:
    """One data point through the five layers: what each found, which decided, and the final answer."""
    g = SIGNALS[sid][0]
    ph = "icp" if g in ("must", "exclusion") else "signals"
    result = ctx["store"].get_result(ctx["domain"], ph)
    if not result or sid not in result["signals"]:
        return None
    code_all, dirs_all = ctx["extract"].get(ph, ({}, {}))
    code, with_dirs = code_all.get(sid), dirs_all.get(sid)
    pid = ctx["by_signal"].get(sid)
    answers = ctx["store"].get_ai(ctx["domain"], ph).get(pid, {}) if pid else {}
    final_v = result["signals"][sid]
    method, note = final_v.get("method", "code"), final_v.get("note", "")
    decided = None
    if final_v["value"] not in (UNKNOWN, "check") and (final_v["value"] != UNKNOWN or method != "code"):
        decided = {"code": 0, "directory": 1, "provider": 4}.get(method, 3 if "web search" in note else 2)
    layer_vals = [code, with_dirs if (with_dirs or {}).get("method") == "directory" else None,
                  (answers.get(1) or {}).get("answer"), (answers.get(2) or {}).get("answer"),
                  _provider_view(sid, ctx["bought"], ph) if sid in PROVIDER_SIDS else None]
    cfg = datapoints.load()[sid]["steps"]
    covers = [ctx["live"], sid in DIRECTORY_COVERS, bool(pid), bool(pid) and cfg["ai_web"].get("run") != "off", sid in PROVIDER_SIDS]
    switched_off = [not cfg["rules"].get("on", True), not cfg["directories"].get("on", True),
                    cfg["ai_pages"].get("run") == "off" or cfg["ai_web"].get("run") == "first",
                    cfg["ai_web"].get("run") == "off", cfg["provider"].get("run") == "off"]
    rows = []
    for i, name in enumerate(LAYERS):
        v = layer_vals[i]
        if i in (2, 3) and v:
            value = str(v.get("answer", v.get("venture_backed", v.get("value", ""))))
            proof = (v.get("evidence_quote") or v.get("reason") or "")[:150]
        else:
            value, proof = _cell(v) if isinstance(v, dict) and "value" in v else ("", "")
        if not covers[i]:
            status = "doesn't cover this" if i else "site not read"
        elif switched_off[i] and v is None:
            status = "skipped (web first)" if i == 2 and cfg["ai_web"].get("run") == "first" else "off in its file"
        elif decided is not None and i == decided:
            status = "DECIDED"
        elif decided is not None and i > decided:
            status = "not needed"
        elif v is None:
            status = {2: "not run (--ai)", 3: "not run" if answers.get(1) else "not run (--ai)", 4: "not run (--providers)"}.get(i, "nothing found")
        else:
            status = ("clue only, passed on" if value == "Needs check" else
                      "nothing found, passed on" if value in ("Unknown", "unknown", "") else "not counted, passed on")
        rows.append([f"{i + 1}. {name}", value, proof, status])
    final_txt = VALUE_NAMES[final_v["value"]] + (f" (by {DECIDERS[decided]})" if decided is not None else
                                                 " (still open after every layer)" if final_v["value"] in (UNKNOWN, "check") else "")
    settings = (f"rules {'on' if cfg['rules'].get('on', True) else 'off'} · directories "
                f"{('on' if cfg['directories'].get('on') else 'off') if sid in DIRECTORY_COVERS else 'n/a'} · AI pages "
                f"{cfg['ai_pages'].get('run', 'off')} · AI web {cfg['ai_web'].get('run', 'off')} · provider "
                f"{cfg['provider'].get('run', 'off') if sid in PROVIDER_SIDS else 'n/a'}")
    return {"sid": sid, "name": signal_column(sid), "phase": ph, "final": final_v, "final_txt": final_txt,
            "decided_by": DECIDERS[decided] if decided is not None else "", "rows": rows, "settings": settings,
            "file": str(datapoints.path(sid, datapoints.load()[sid]).relative_to(datapoints.CONFIG.parent))}


def render(dp: dict) -> list:
    t = Table(header_style="bold", show_lines=False)
    for c in ("Layer", "Found", "Proof", "Status"):
        t.add_column(c, overflow="fold")
    for r in dp["rows"]:
        style = "bold green" if r[3] == "DECIDED" else "dim" if r[3] in ("not needed", "doesn't cover this") else None
        t.add_row(*r, style=style)
    f = dp["final"]
    return [Panel(f"Final: [bold]{dp['final_txt']}[/]\nProof: {(f.get('evidence') or f.get('note') or '-')[:260]}"
                  + (f"\nPage: {f['url']}" if f.get("url", "").startswith("http") else "")
                  + f"\n[dim]Waterfall: {dp['settings']} ({dp['file']})[/]",
                  title=f"[bold]{dp['name']}[/]", title_align="left", border_style="blue"), t]


def markdown(dp: dict) -> list[str]:
    out = [f"## {dp['name']}", "", f"Final: **{dp['final_txt']}**. Proof: {(dp['final'].get('evidence') or '-')[:220]}", "",
           "| Layer | Found | Proof | Status |", "|---|---|---|---|"]
    return out + ["| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in r) + " |" for r in dp["rows"]] + [""]
