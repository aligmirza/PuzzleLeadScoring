"""The final list: every row of the original CSV, in order, with its original columns, plus the findings.

No row is ever dropped. Duplicates get the same findings as their first row, and rows without a usable
domain are kept with a note. Written after each phase to lists/<list>/output/<list>_enriched.csv (another pipeline: <list>_<pipeline>_enriched.csv).
"""
import csv
import re
from pathlib import Path

from .common import console, plain, rel
from ..core.inputs import DOMAIN_COLS, normalize_domain
from ..checks.rules import CELL_NAMES, SIGNALS, signal_column
from ..checks.scoring import segment, score
from ..core.store import Store

ICP_STATUS = {
    "Yes": ("YES", "All 4 must-haves confirmed, no exclusion found"),
    "Yes (some must-haves unconfirmed)": ("YES", "No must-have failed and no exclusion found; some must-haves couldn't be confirmed (that never counts against a company)"),
    "Needs AI check": ("PENDING", "A possible exclusion or must-have needs the AI check"),
    "No": ("NO", ""),
    "Unknown: site not reached": ("PENDING", "Website could not be reached; retry"),
    "Needs a call: no website": ("PENDING", "No working website and nothing proven yet: confirm the company on the call"),
}


def _why(icp: dict) -> str:
    if icp.get("exclusion_reason"):
        return plain(icp["exclusion_reason"])
    s = icp.get("signals", {})
    failed = "; ".join(f"{SIGNALS[k][1]}: {v['evidence']}" for k, v in s.items() if SIGNALS[k][0] == "must" and v["value"] == "no")
    if failed:
        return failed
    if icp.get("icp_checks"):
        return "To confirm: " + "; ".join(icp["icp_checks"])
    if icp.get("crawl", {}).get("status") in ("blocked", "unreachable", "error"):
        return f"Website not reached: {icp['crawl'].get('error') or icp['crawl']['status']}"
    return ""


STAGES = {"pre-seed": "Pre-Seed", "preseed": "Pre-Seed", "seed": "Seed", "series a": "Series A", "series b": "Series B",
          "series c or later": "Series C or later", "series c": "Series C or later", "series d": "Series C or later",
          "series e": "Series C or later", "bootstrapped": "Bootstrapped", "unknown": "Unknown"}


def funding_line(icp: dict, sig: dict | None) -> str:
    """The profile's callout, exactly: 'Funding stage: <value> (<basis>)'. Values and bases are the profile's lists."""
    if icp.get("signals", {}).get("E1", {}).get("value") == "yes":
        return "Funding stage: Series C or later (confirmed)"
    raw = (sig or {}).get("funding_stage") or icp.get("funding_stage") or ""
    m = re.match(r"\s*(.+?)\s*\((confirmed|estimated|not found)\)\s*$", raw, re.I)
    value = STAGES.get((m.group(1) if m else raw).strip().lower())
    basis = m.group(2).lower() if m else ""
    if not value or value == "Unknown" or not basis:
        return "Funding stage: Unknown (not found)"
    return f"Funding stage: {value} ({basis})"


def reasoning(icp: dict, sig: dict | None, row: dict, sc: dict | None) -> str:
    """Plain-language summary of why, built from the answers (no AI cost). Never says a company falls short because
    something couldn't be found."""
    s = icp.get("signals", {})
    lines = []
    if row["ICP"] == "NO":
        lines.append(f"Not ICP. {row['Why']}".rstrip(". ") + ".")
    elif row["ICP"] == "PENDING":
        lines.append(f"ICP pending: {row['ICP status'].rstrip('.')}." + (f" {row['Why']}" if row["Why"] else ""))
    else:
        confirmed = [SIGNALS[k][1] for k, v in s.items() if SIGNALS[k][0] == "must" and v["value"] == "yes"]
        lines.append(f"ICP: {len(confirmed)} of 4 must-haves confirmed" + (f" ({'; '.join(confirmed)})" if confirmed else "")
                     + ", and no exclusion found.")
    if sc:
        top = [n for n, p in sorted(sc["parts"], key=lambda x: -x[1]) if p > 0][:5]
        weak = [n for n, p in sc["parts"] if p < 0]
        lines.append(f"{sc['tier']} with {sc['score']} points" + (f", mainly: {', '.join(top)}" if top else "") + "."
                     + (f" Weak fit signals: {', '.join(weak)}." if weak else ""))
    if row.get("Found in directories"):
        lines.append(f"Directories: {row['Found in directories'].rstrip('.')}.")
    lines.append(funding_line(icp, sig))
    return "\n".join(lines)


ASK_HOW_BOOKS = ("Real Estate & Property", "Legal")  # their books can differ (per property, client trust accounts)


def services_columns(yes_no: str, icp: dict, s: dict, vertical: str = "") -> dict:
    """Route, arrival and value angle for the services pipeline, from proven facts only (the ICP page's routing
    table). Route says what to offer; arrival says why now; value angle is the opening line for outreach."""
    yes = {sid for sid, v in s.items() if v.get("value") == "yes"}
    if yes_no == "NO":
        excluded = icp.get("exclusion_reason") or ""
        route = ("Firm ICP (accounting firm), not services" if "E3" in yes or "Accounting or bookkeeping firm" in excluded
                 else "Partner referral (hard stop)" if icp.get("gate") == "excluded" else "Not ICP")
        return {"Route": route, "Arrival": "", "Value angle": ""}
    if (icp.get("icp_verdict") or "").startswith("Unknown: site not reached"):
        return {"Route": "Retry: website not reached", "Arrival": "", "Value angle": ""}
    if icp.get("icp_verdict") == "Needs a call: no website":
        return {"Route": "Qualify on the call: no website, confirm the business and how the books are handled",
                "Arrival": "", "Value angle": ""}
    if "S3" in yes:
        route = "Catch-Up, then Full Service"
    elif yes & {"S4", "B9"}:
        route = "Full Service"
    elif "S5" in yes:
        route = "Platform + MCP (does its own books, can use its own agent)"
    elif vertical in ASK_HOW_BOOKS:
        route = f"Qualify on the call ({vertical}): ask how and by whom the books are handled"
    else:
        route = "Qualify on the call: Full Service or Expert Review"
    reactive = yes & {"S3", "B9", "B10", "B14"}  # the books broke, a provider failed, a deadline or an audit
    proactive = yes & {"B1", "B7"}              # just raised or newly formed: delegating before it breaks
    arrival = "Reactive" if reactive else "Proactive" if proactive else ""
    if "B9" in yes:
        angle = "A reliable replacement: agreed close timing, who fixes mistakes, an orderly handoff"
    elif "S3" in yes:
        angle = "Get current fast (Catch-Up), then dependable monthly books"
    elif yes & {"B10", "B14"}:
        angle = "Books ready for the deadline: tax- and audit-ready, with a person accountable"
    elif yes & {"B13", "B1"}:
        angle = "Investor-ready numbers, with a person accountable for them"
    elif "S5" in yes:
        angle = "Keep doing it your way with your own agent through Puzzle's MCP, with a person to check the work"
    elif "S10" in yes or "S9" in yes:
        angle = "Clean monthly closes across processors, subscriptions, refunds and deferred revenue"
    elif "S11" in yes:
        angle = "Clean up the QuickBooks file, then dependable monthly books alongside your field software"
    else:
        angle = "Current, dependable books without owning the work; you see every step"
    return {"Route": route, "Arrival": arrival, "Value angle": angle}


def findings(domain: str, employees: str, icp: dict | None, sig: dict | None, contacts: list | None = None) -> dict:
    if not icp:
        return {"ICP": "PENDING", "ICP status": "Not checked yet (run phase 1)"}
    verdict = icp.get("icp_verdict", "")
    yes_no, status = ICP_STATUS.get(verdict, ("PENDING", verdict))
    if yes_no == "NO":
        status = {"excluded": "Excluded: proof of an exclusion was found",
                  "failed must-have": "Failed a must-have: proof it doesn't meet one"}.get(icp.get("gate"), "Not ICP")
    if yes_no == "NO":
        sig = None  # phase 2 answers saved before the company was ruled out are no longer relevant
    late = (sig or {}).get("funding_stage", "").startswith("Series C or later (confirmed)")
    if yes_no == "YES" and late and icp.get("signals", {}).get("E1", {}).get("value") != "yes":
        # phase 2 found a late round that phase 1 didn't: don't keep a YES that may be an exclusion
        yes_no, status = "PENDING", ("Phase 2 found a Series C or later round; confirm with the phase 1 AI check "
                                     "(ai_check.py <list> --phase icp)")
    # size, from proof: the list's count, a Prospeo headcount, the YC team size (all worked out by the rules), then
    # the AI's estimate, then the profile default (SMB).
    seg, seg_basis = segment(employees)
    facts = (sig or icp).get("facts") or {}
    ai_seg = (sig or {}).get("segment_ai")
    if not re.search(r"\d", employees or ""):
        if facts.get("segment"):
            seg, seg_basis = facts["segment"], facts.get("segment_basis", "")
        elif ai_seg:
            seg = ai_seg["value"]
            seg_basis = f"AI estimate from the website ({ai_seg.get('headcount') or 'unknown'} people)"
    row = {
        "ICP": yes_no,
        "ICP status": status or verdict,
        "Why": _why(icp),
        "Segment": seg,
        "Segment based on": seg_basis,
        "Lead score": "",
        "Lead tier": "Not ICP" if yes_no == "NO" else "",
        "Score breakdown": "",
    }
    sc = None
    if sig and yes_no != "NO":
        sc = score(sig["signals"], seg)
        row.update({"Lead score": sc["score"], "Lead tier": sc["tier"] + (" (pending AI check)" if yes_no == "PENDING" else ""),
                    "Score breakdown": sc["breakdown"]})
    elif verdict == "Unknown: site not reached":
        row["Lead tier"] = "Not scored: website not reached (retry)"
    elif verdict == "Needs a call: no website":
        row["Lead tier"] = "Not scored: no website (confirm on the call)"
    elif yes_no != "NO":
        row["Lead tier"] = "Scored after phase 2"
    src = sig or icp
    s_all = {**(sig or {}).get("signals", {}), **icp.get("signals", {})}
    roles = src.get("crawl", {}).get("jobs_count")
    row.update({
        "Fit signals found": "; ".join(SIGNALS[k][1] for k, v in s_all.items() if SIGNALS[k][0] == "fit" and v["value"] == "yes"),
        "Buying signals found": "; ".join(SIGNALS[k][1] for k, v in s_all.items() if SIGNALS[k][0] == "buying" and v["value"] == "yes"),
        "Weak fit signals found": "; ".join(SIGNALS[k][1] for k, v in s_all.items() if SIGNALS[k][0] == "weak" and v["value"] == "yes"),
        "Needs checking": "; ".join(SIGNALS[k][1] for k, v in s_all.items() if v["value"] == "check"),
        "Website": icp.get("crawl", {}).get("final_url") or "",
        "Website status": (icp.get("crawl", {}).get("status") or "").capitalize(),
        "Vertical": (sig or {}).get("vertical", ""),
        "Legal entity type": src.get("entity_type", ""),
        "Legal name": src.get("legal_name") or "",
        "Funding stage": sig.get("funding_stage", "") if sig else "",
        "Found in directories": (sig or {}).get("directories") or icp.get("directories", ""),
        "Website technologies": (sig or {}).get("technologies") or icp.get("technologies", ""),
        "Partners found": ", ".join(src.get("partners", [])),  # also for companies ruled out: free facts
        "Competitor tools used": ", ".join(src.get("competitors", [])),
        "Job board": (src.get("crawl", {}).get("ats") or "").capitalize(),
        "Open roles": roles if roles is not None else "",
        "Prices found": " | ".join(src.get("facts", {}).get("prices", [])[:3]),
    })
    for sid in SIGNALS:
        if sid in s_all:
            row[signal_column(sid)] = CELL_NAMES[s_all[sid]["value"]]
    row["Found by providers"] = (sig or {}).get("providers") or icp.get("providers", "")
    if contacts:
        top = contacts[0]
        row.update({"Key contact": top.get("name") or "", "Key contact title": top.get("title") or "",
                    "Key contact email": top.get("email") or "", "Key contact LinkedIn": top.get("linkedin") or "",
                    "Contacts found": len(contacts)})
    from ..core import icp as pipelines
    if pipelines.name() == "services":
        row.update(services_columns(yes_no, icp, s_all, (sig or {}).get("vertical", "")))
    row["Reasoning"] = reasoning(icp, sig, row, sc)
    row["Funding stage"] = funding_line(icp, sig).removeprefix("Funding stage: ")
    return row


def write(folder: Path, store: Store) -> Path:
    src = folder / "input.csv"
    from ..core import icp
    out = icp.enriched_csv(folder)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(src, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        original_cols = list(reader.fieldnames or [])
        rows = list(reader)
    lowered = {c.strip().lower(): c for c in original_cols}
    dcol = next((lowered[c] for c in DOMAIN_COLS if c in lowered), None)
    ecol = next((lowered[c] for c in ("employee size", "employees", "employee count", "employee_count", "headcount",
                                      "size", "company size") if c in lowered), None)

    costs = store.ai_costs()
    first_seen: dict[str, int] = {}
    cache: dict[str, dict] = {}
    added_cols: list[str] = []
    out_rows = []
    for i, raw in enumerate(rows, start=2):  # row 1 is the header
        domain = normalize_domain(raw.get(dcol, "")) if dcol else None
        if not domain:
            extra = {"Pipeline": icp.name(), "ICP": "NO", "ICP status": "No usable domain in this row",
                     "Why": "Domain is empty or not a valid domain", "Lead tier": "Not ICP"}
        else:
            if domain not in cache:
                people = (store.get_provider(domain).get("prospeo", {}).get("people") or {}).get("list")
                cache[domain] = findings(domain, raw.get(ecol, "") if ecol else "",
                                         store.get_result(domain, "icp"), store.get_result(domain, "signals"), people)
                cache[domain]["AI cost (USD)"] = costs.get(domain, {}).get("total_usd", 0)
            extra = {"Pipeline": icp.name(), **cache[domain]}
            if domain in first_seen:
                extra["Notes"] = f"Duplicate of row {first_seen[domain]}"
            else:
                first_seen[domain] = i
        for k in extra:
            if k not in added_cols and k not in original_cols:
                added_cols.append(k)
        out_rows.append({**raw, **{k: v for k, v in extra.items() if k not in original_cols}})

    # keep a stable, readable column order for the added columns
    order = ["Pipeline", "ICP", "ICP status", "Why", "Route", "Arrival", "Value angle", "Lead score", "Lead tier", "Reasoning", "Score breakdown", "Segment", "Segment based on",
             "Fit signals found", "Buying signals found", "Weak fit signals found", "Needs checking", "Notes"]
    added_cols = [c for c in order if c in added_cols] + [c for c in added_cols if c not in order]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=original_cols + added_cols)
        w.writeheader()
        w.writerows(out_rows)
    yes = sum(1 for r in out_rows if r.get("ICP") == "YES")
    no = sum(1 for r in out_rows if r.get("ICP") == "NO")
    pending = len(out_rows) - yes - no
    scored = sum(1 for r in out_rows if r.get("Lead score") not in ("", None))
    console.print(f"[bold]Final list[/] {rel(out)}: all {len(out_rows)} rows kept, your {len(original_cols)} columns + "
                  f"{len(added_cols)} new. ICP YES {yes}, NO {no}, PENDING {pending}; {scored} rows scored.")
    return out


def read_rows(folder: Path) -> list[dict]:
    """The final list's rows, each with _domain and _company added for lookups."""
    from ..core import icp
    path = icp.enriched_csv(folder)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        rows = list(reader)
    lowered = {c.strip().lower(): c for c in cols}
    dcol = next((lowered[c] for c in DOMAIN_COLS if c in lowered), None)
    ncol = next((lowered[c] for c in ("company name", "company", "name") if c in lowered), None)
    for r in rows:
        r["_domain"] = normalize_domain(r.get(dcol, "")) if dcol else None
        r["_company"] = r.get(ncol, "") if ncol else ""
    return rows
