"""Helpers shared by phase 1 (ICP check) and phase 2 (signals)."""
import csv
import json
import re
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from ..ai import apply as ai_apply
from ..sources import directories
from ..ai import prompts as prompt_files
from ..core.dashboard import ICON
from ..core.inputs import load_leads
from ..checks.rules import CELL_NAMES, SIGNALS, VALUE_NAMES, extract, signal_column
from ..core.store import Store

console = Console()
ROOT = Path(__file__).resolve().parents[2]
LISTS = ROOT / "lists"


# lists and folders -------------------------------------------------------
def list_name(csv_path: Path) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", csv_path.stem.lower()).strip("_") or "list"


def list_dir(name: str) -> Path:
    """Find a list folder by name (or by its CSV path)."""
    folder = LISTS / list_name(Path(name)) if name.endswith(".csv") else LISTS / name
    if not folder.exists():
        known = ", ".join(sorted(d.name for d in LISTS.glob("*") if d.is_dir())) or "none yet"
        sys.exit(f"No list called '{folder.name}'. Lists: {known}")
    return folder


def prepare_list(src: Path, name: str | None) -> tuple[Path, list[dict], dict]:
    """Create lists/<name>/, copy the CSV in as input.csv, and read it."""
    folder = LISTS / (name or list_name(src))
    folder.mkdir(parents=True, exist_ok=True)
    if src.resolve() != (folder / "input.csv").resolve():
        shutil.copyfile(src, folder / "input.csv")
    rows, report = load_leads(folder / "input.csv")
    return folder, rows, report


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def plain(text: str) -> str:
    """Remove signal codes (like "E4 ") left in results saved by older versions."""
    return re.sub(r"\b[MEFBW]\d{1,2} (?=[A-Z])", "", text or "")


# rules -------------------------------------------------------------------
def run_rules(store: Store, row: dict, record: dict, phase: str) -> dict:
    pages = {k: store.load_page(row["domain"], k) for k in record.get("pages", {})}
    pages = {k: v for k, v in pages.items() if v}
    result = extract(row["domain"], row["company"], row["employees"], record, pages, store.load_jobs(row["domain"]), phase,
                     directory_finder(store, row))
    answers = store.get_ai(row["domain"], phase)
    if answers:  # keep earlier AI answers when the rules are re-run
        result = ai_apply.apply(result, answers, phase)
    store.save_result(row["domain"], result, phase)
    return result


def directory_finder(store: Store, row: dict):
    """Directory lookups for one company: reuse saved findings unless the legal name changed or a lookup failed."""
    if not directories.ENABLED:
        return None

    def find(legal_name, us_clues, skip_sec, history=None):
        found = store.get_directory(row["domain"])
        asked = found.get("history_asked", {}) if found else {}
        if (found and found.get("legal_name") == legal_name and found.get("complete")
                and found.get("version") == directories.VERSION
                and (skip_sec or not found.get("skipped_sec"))
                and all(asked.get(k) or not v for k, v in (history or {}).items())):
            return found
        found = directories.lookup(row["domain"], row["company"], legal_name, us_clues, skip_sec, history)
        found["skipped_sec"] = skip_sec
        store.save_directory(row["domain"], found)
        return found
    return find


def ai_prompts(r: dict) -> list[str]:
    return r.get("ai", {}).get("prompts", r.get("ai", {}).get("questions", []))


def signal_columns(r: dict, groups: tuple[str, ...]) -> dict:
    s = r["signals"]
    return {signal_column(sid): CELL_NAMES[s[sid]["value"]] for sid in SIGNALS if SIGNALS[sid][0] in groups and sid in s}


def ai_columns(r: dict) -> dict:
    return {
        "Data points for AI": "; ".join(prompt_files.name(q) for q in ai_prompts(r)),
        "AI calls": len(ai_prompts(r)),
        "Estimated AI input tokens": r["ai"]["tokens_est"],
        "Of which cached": r["ai"].get("cached_tokens_est", 0),
        "Estimated AI output tokens": r["ai"].get("output_tokens_est", 0),
    }


# outputs -----------------------------------------------------------------
def write_outputs(results: list[dict], rows: list[dict], out: Path, names: dict[str, str]) -> dict[str, Path]:
    """Write the CSV (rows), the evidence file and the AI queue."""
    out.mkdir(parents=True, exist_ok=True)
    paths = {k: out / v for k, v in names.items()}
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with open(paths["csv"], "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    with open(paths["evidence"], "w") as f, open(paths["ai_queue"], "w") as q:
        for r in results:
            f.write(json.dumps({k: v for k, v in r.items() if k != "ai_context"}) + "\n")
            if r.get("ai", {}).get("needed"):
                q.write(json.dumps({
                    "domain": r["domain"], "company": r["company"], "phase": r.get("phase"),
                    "prompts": [{"id": k, "data_point": prompt_files.name(k), "web_search": prompt_files.web_mode(k),
                                 "plan": prompt_files.step_plan(k)} for k in ai_prompts(r)],
                    "legal_name": r.get("legal_name"),
                    "known_from_code": {SIGNALS[sid][1]: {"value": v["value"], "evidence": v["evidence"]}
                                        for sid, v in r["signals"].items() if v["value"] in ("yes", "no")},
                    "context": r["ai_context"], "tokens_est": r["ai"]["tokens_est"],
                }) + "\n")
    return paths


def signal_summary(results: list[dict], groups: tuple[str, ...], title: str) -> None:
    ok = [r for r in results if "signals" in r]
    t = Table(title=title, header_style="bold")
    t.add_column("Check")
    for v in ("yes", "check", "no", "unknown"):
        t.add_column(f"{ICON[v]} {VALUE_NAMES[v]}", justify="right")
    for sid, (group, _) in SIGNALS.items():
        if group not in groups:
            continue
        counts = [sum(1 for r in ok if r["signals"].get(sid, {}).get("value") == v) for v in ("yes", "check", "no", "unknown")]
        t.add_row(signal_column(sid), *map(str, counts))
    console.print(t)


def ai_summary(results: list[dict]) -> None:
    ok = [r for r in results if "signals" in r]
    ai = [r for r in ok if r["ai"]["needed"]]
    console.print(f"AI step: {len(ai)} of {len(ok)} companies, {sum(len(ai_prompts(r)) for r in ai)} prompts (one per data point). "
                  f"About {sum(r['ai']['tokens_est'] for r in ai):,} input tokens, of which "
                  f"{sum(r['ai'].get('cached_tokens_est', 0) for r in ai):,} read from cache; "
                  f"about {sum(r['ai'].get('output_tokens_est', 0) for r in ai):,} output tokens.")
    if directories.ENABLED:
        found = {k: sum(1 for r in ok if f"{k}" in (r.get("directories") or "")) for k in directories.ORDER}
        settled = sum(1 for r in ok for v in r["signals"].values() if v.get("method") == "directory" and v["value"] in ("yes", "no"))
        down = sorted({k for r in ok for k in (r.get("facts") or {}).get("directory_errors", {})})
        console.print("Free directories: found in " + ", ".join(f"{k} {n}" for k, n in found.items())
                      + f"; {settled} answers settled without AI" + (f". [yellow]Not reachable: {', '.join(down)}[/]" if down else ""))
    errs = [r for r in results if "signals" not in r]
    if errs:
        console.print(f"[red]{len(errs)} rule errors[/], first: {errs[0]['domain']}: {errs[0].get('error')}")


def files_and_disk(paths: dict[str, Path], store: Store) -> None:
    for name, p in paths.items():
        console.print(f"  {name:<9} {rel(p)}  ({human(p.stat().st_size)})")
    store.checkpoint()
    u = store.disk_usage()
    kept = sum(1 for d in store.all_domains() if store.pages_size(d))
    console.print(f"Disk: raw pages {human(u['pages'])} for {kept} companies in {rel(store.pages_dir)}, "
                  f"database {human(u['database'])}, total {human(u['total'])}")


# clean-up ----------------------------------------------------------------
def should_delete(icp_result: dict, keep: str) -> bool:
    """keep=all: never. keep=useful: companies that are not ICP (or whose site is dead). keep=none: everything."""
    if keep == "all":
        return False
    if keep == "none":
        return True
    return icp_result.get("icp_verdict") == "No" or icp_result.get("crawl", {}).get("status") == "dead"


def offer_cleanup(store: Store, keep: str, assume_yes: bool) -> None:
    """Show what could be deleted and ask before deleting anything."""
    if keep == "all":
        return
    targets = []
    for d in store.all_domains():
        size = store.pages_size(d)
        result = store.get_result(d, "icp")
        if size and result and should_delete(result, keep):
            reason = "not ICP: " + (result.get("gate") or "") if keep == "useful" else "all companies (--keep none)"
            targets.append((d, size, reason))
    if not targets:
        console.print("Nothing to clean up.")
        return
    total = sum(s for _, s, _ in targets)
    t = Table(title="Raw pages that are no longer needed", title_justify="left", header_style="bold dim")
    t.add_column("Reason")
    t.add_column("Companies", justify="right")
    t.add_column("Size", justify="right")
    reasons: dict[str, list] = {}
    for _, size, reason in targets:
        reasons.setdefault(reason, []).append(size)
    for reason, sizes in reasons.items():
        t.add_row(reason, str(len(sizes)), human(sum(sizes)))
    console.print(t)
    console.print("[dim]Results, proof and AI context stay in the database. Deleted pages can only be "
                  "re-checked by downloading again (--refresh).[/]")
    if assume_yes:
        ok = True
    elif not sys.stdin.isatty():
        console.print(f"[yellow]Not deleting: no terminal to ask. To delete, run: "
                      f"python -m enrich clean {store.root.name}{' --keep none' if keep == 'none' else ''}[/]")
        return
    else:
        try:
            ok = Confirm.ask(f"Delete raw pages for {len(targets)} companies ({human(total)})?", default=False)
        except (EOFError, KeyboardInterrupt):
            ok = False  # no answer means no
    if not ok:
        console.print("Kept everything.")
        return
    freed = sum(store.delete_pages(d) for d, _, _ in targets)
    store.vacuum()
    console.print(f"[green]Deleted {human(freed)}.[/] Disk now {human(store.disk_usage()['total'])}.")
