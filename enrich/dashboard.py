"""Live terminal view: progress, running counts and the latest companies.

mode="icp"      phase 1: must-haves, exclusions and the ICP verdict
mode="signals"  phase 2: fit, buying and weak signals for companies that passed phase 1
"""
import time
from collections import Counter, deque

from rich.console import Group
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text

from .rules import SIGNALS

EXCLUSION_SHORT = {"E1": "Series C+", "E2": "No website", "E3": "Accounting firm", "E4": "Outside US",
                   "E5": "Non-profit", "E6": "Holds stock"}
MUST_SHORT = {"M1": "US", "M2": "Live", "M3": "Fintech", "M4": "Sales"}

ICON = {"yes": "[green]✓[/]", "no": "[red]✗[/]", "unknown": "[dim]·[/]", "check": "[yellow]?[/]"}
VERDICT_STYLE = {"Yes": "green", "Yes (some must-haves unconfirmed)": "cyan", "Needs AI check": "yellow",
                 "No": "red", "Unknown: site not reached": "yellow"}


class Dashboard:
    def __init__(self, title: str, total: int, mode: str = "icp"):
        self.title, self.total, self.start, self.mode = title, total, time.time(), mode
        self.progress = Progress(SpinnerColumn(), TextColumn("{task.description:<18}"), BarColumn(bar_width=36),
                                 MofNCompleteColumn(), TextColumn("{task.fields[info]}"), TimeElapsedColumn(),
                                 TimeRemainingColumn())
        self.t_crawl = self.progress.add_task("Download pages" if mode == "icp" else "Download extra pages", total=total, info="")
        self.t_rules = self.progress.add_task("ICP rules" if mode == "icp" else "Signal rules", total=total, info="")
        self.c = Counter()
        self.feed: deque = deque(maxlen=12)

    def crawled(self, record: dict, cached: bool):
        self.c["cached" if cached else "downloaded"] += 1
        self.c[f"site_{record.get('status')}"] += 1
        self.c["pages"] += len(record.get("pages", {}))
        self.c["requests"] += 0 if cached else record.get("requests_phase2" if self.mode == "signals" else "requests", 0)
        if record.get("ats"):
            self.c["ats"] += 1
            self.c["roles"] += record.get("jobs_count") or 0
        rate = self.c["downloaded"] / max(1, time.time() - self.start)
        self.progress.update(self.t_crawl, advance=1, info=f"{rate:5.1f} sites/s")

    def ruled(self, r: dict):
        s = r["signals"]
        self.c[f"verdict_{r.get('icp_verdict', '')}"] += 1
        for sid, v in s.items():
            self.c[f"{sid}_{v['value']}"] += 1
        for p in r.get("partners", []):
            self.c[f"partner_{p}"] += 1
        if r["ai"]["needed"]:
            self.c["ai_rows"] += 1
            self.c["ai_tokens"] += r["ai"]["tokens_est"]
            self.c["ai_cached"] += r["ai"].get("cached_tokens_est", 0)
            self.c["ai_calls"] += r["ai"].get("calls", 0)
        else:
            self.c["ai_skipped"] += 1
        self.progress.update(self.t_rules, advance=1, info=f"AI needed {self.c['ai_rows']}")
        self.feed.appendleft(r)

    # rendering ----------------------------------------------------------
    def _site_grid(self) -> Table:
        c = self.c
        t = Table.grid(padding=(0, 3))
        for style in ("bold", None, "bold", None):
            t.add_column(style=style)
        t.add_row("Sites live", f"[green]{c['site_live']}[/]", "Pages saved", str(c["pages"]))
        t.add_row("Dead", f"[red]{c['site_dead']}[/]", "Requests sent", str(c["requests"]))
        t.add_row("Blocked / unreachable", f"[yellow]{c['site_blocked']} / {c['site_unreachable'] + c['site_error']}[/]",
                  "From cache", str(c["cached"]))
        t.add_row("Job boards", str(c["ats"]), "Open roles", str(c["roles"]))
        t.add_row("Raw pages saved", f"{c['bytes_saved'] / 1048576:.1f} MB", "", "")
        return t

    def _ai_line(self) -> Text:
        c = self.c
        skipped = "dead, excluded or failed" if self.mode == "icp" else "nothing left to ask"
        return Text.from_markup(f"[bold]AI step[/]     {c['ai_rows']} companies need it, {c['ai_calls']} prompts, about "
                                f"{c['ai_tokens']:,} input tokens ({c['ai_cached']:,} cached); {c['ai_skipped']} skipped ({skipped})")

    def _stats(self) -> Table:
        c = self.c
        g = Table.grid()
        g.add_row(self._site_grid())
        if self.mode == "icp":
            verdicts = "  ".join(f"[{style}]{name} {c[f'verdict_{name}']}[/]" for name, style in VERDICT_STYLE.items())
            musts = "  ".join(f"{name} {ICON['yes']}{c[f'{sid}_yes']} {ICON['check']}{c[f'{sid}_check']} {ICON['no']}{c[f'{sid}_no']}"
                              for sid, name in MUST_SHORT.items())
            excl = "  ".join(f"{name} {c[f'{sid}_yes']}/{c[f'{sid}_check']}" for sid, name in EXCLUSION_SHORT.items())
            g.add_row(Text.from_markup(f"\n[bold]Is it ICP?[/]  {verdicts}"))
            g.add_row(Text.from_markup(f"[bold]Must-haves[/]  {musts}"))
            g.add_row(Text.from_markup(f"[bold]Exclusions[/]  {excl}   [dim](confirmed / needs check)[/]"))
        else:
            for group, title in (("fit", "Fit"), ("buying", "Buying"), ("weak", "Weak fit")):
                top = sorted(((c[f"{sid}_yes"], label) for sid, (g2, label) in SIGNALS.items() if g2 == group), reverse=True)
                shown = ", ".join(f"{label} {n}" for n, label in top if n)[:150] or "-"
                g.add_row(Text.from_markup(f"{'' if group != 'fit' else chr(10)}[bold]{title:<11}[/] {shown}"))
            partners = ", ".join(f"{k.removeprefix('partner_')} {v}" for k, v in c.most_common() if k.startswith("partner_"))[:150]
            g.add_row(Text.from_markup(f"[bold]Partners[/]    {partners or '-'}"))
        g.add_row(self._ai_line())
        return g

    def _feed(self) -> Table:
        t = Table(expand=True, show_edge=False, header_style="bold dim", pad_edge=False)
        if self.mode == "icp":
            cols = (("Domain", {"max_width": 26}), ("Is it ICP?", {}), ("US Live Fintech Sales", {}),
                    ("Entity", {"max_width": 18}), ("Jobs", {"justify": "right"}), ("Exclusion flags", {"max_width": 28}),
                    ("AI prompts", {"justify": "right"}))
        else:
            cols = (("Domain", {"max_width": 26}), ("Fit", {"justify": "right"}), ("Buying", {"justify": "right"}),
                    ("Weak", {"justify": "right"}), ("Partners", {"max_width": 26}), ("Funding", {"max_width": 22}),
                    ("Jobs", {"justify": "right"}), ("AI prompts", {"justify": "right"}))
        for col, kw in cols:
            t.add_column(col, no_wrap=True, overflow="ellipsis", **kw)
        for r in self.feed:
            s = r["signals"]
            jobs = r["crawl"].get("jobs_count")
            jobs = str(jobs) if jobs is not None else "[dim]-[/]"
            n_ai = str(len(r["ai"].get("prompts", [])))
            if self.mode == "icp":
                musts = "  ".join(ICON[s[k]["value"]] for k in MUST_SHORT)
                flags = ", ".join(f"[red]{name}[/]" if s[k]["value"] == "yes" else f"[yellow]{name}?[/]"
                                  for k, name in EXCLUSION_SHORT.items() if s[k]["value"] in ("yes", "check"))
                verdict = r.get("icp_verdict", "")
                t.add_row(r["domain"], f"[{VERDICT_STYLE.get(verdict, 'white')}]{verdict}[/]", musts,
                          r["entity_type"] if r["entity_type"] != "Unknown" else "[dim]-[/]", jobs, flags, n_ai)
            else:
                count = {g: sum(1 for sid, v in s.items() if SIGNALS[sid][0] == g and v["value"] == "yes")
                         for g in ("fit", "buying", "weak")}
                t.add_row(r["domain"], f"[green]{count['fit']}[/]", f"[cyan]{count['buying']}[/]", f"[red]{count['weak']}[/]",
                          ", ".join(r.get("partners", [])) or "[dim]-[/]", r.get("funding_stage", ""), jobs, n_ai)
        return t

    def __rich__(self):
        return Group(
            Panel(self.progress, title=f"[bold]{self.title}[/]", border_style="blue"),
            Panel(self._stats(), title="Running totals", border_style="dim"),
            Panel(self._feed(), title="Latest companies", border_style="dim"),
        )
