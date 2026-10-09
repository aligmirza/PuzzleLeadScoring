"""The final list: every row of the original CSV, in order, with its original columns, plus the findings.

No row is ever dropped. Duplicates get the same findings as their first row, and rows without a usable
domain are kept with a note. Written after each phase to lists/<list>/output/<list>_enriched.csv.
"""
import csv
from pathlib import Path

from .common import console, plain, rel
from .inputs import DOMAIN_COLS, normalize_domain
from .rules import SIGNALS, VALUE_NAMES, signal_column
from .scoring import segment, score
from .store import Store

ICP_STATUS = {
    "Yes": ("YES", "All 4 must-haves confirmed, no exclusion found"),
    "Yes (some must-haves unconfirmed)": ("YES", "No must-have failed and no exclusion found; some must-haves couldn't be confirmed (that never counts against a company)"),
    "Needs AI check": ("PENDING", "A possible exclusion or must-have needs the AI check"),
    "No": ("NO", ""),
    "Unknown: site not reached": ("PENDING", "Website could not be reached; retry"),
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


def findings(domain: str, employees: str, icp: dict | None, sig: dict | None) -> dict:
    if not icp:
        return {"ICP": "PENDING", "ICP status": "Not checked yet (run phase 1)"}
    verdict = icp.get("icp_verdict", "")
    yes_no, status = ICP_STATUS.get(verdict, ("PENDING", verdict))
    if yes_no == "NO":
        status = {"excluded": "Excluded: proof of an exclusion was found",
                  "failed must-have": "Failed a must-have: proof it doesn't meet one"}.get(icp.get("gate"), "Not ICP")
    seg, seg_basis = segment(employees)
    ai_seg = (sig or {}).get("segment_ai")
    if not (employees or "").strip() and ai_seg:
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
    if sig and yes_no != "NO":
        sc = score(sig["signals"], seg)
        row.update({"Lead score": sc["score"], "Lead tier": sc["tier"] + (" (pending AI check)" if yes_no == "PENDING" else ""),
                    "Score breakdown": sc["breakdown"]})
    elif verdict == "Unknown: site not reached":
        row["Lead tier"] = "Not scored: website not reached (retry)"
    elif yes_no != "NO":
        row["Lead tier"] = "Scored after phase 2"
    src = sig or icp
    s_all = {**icp.get("signals", {}), **(sig or {}).get("signals", {})}
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
        "Partners found": ", ".join(sig.get("partners", [])) if sig else "",
        "Competitor tools used": ", ".join(sig.get("competitors", [])) if sig else "",
        "Job board": (src.get("crawl", {}).get("ats") or "").capitalize(),
        "Open roles": roles if roles is not None else "",
        "Prices found": " | ".join(src.get("facts", {}).get("prices", [])[:3]),
    })
    for sid in SIGNALS:
        if sid in s_all:
            row[signal_column(sid)] = VALUE_NAMES[s_all[sid]["value"]]
    return row


def write(folder: Path, store: Store) -> Path:
    src = folder / "input.csv"
    out = folder / "output" / f"{folder.name}_enriched.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(src, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        original_cols = list(reader.fieldnames or [])
        rows = list(reader)
    lowered = {c.strip().lower(): c for c in original_cols}
    dcol = next((lowered[c] for c in DOMAIN_COLS if c in lowered), None)
    ecol = next((lowered[c] for c in ("employee size", "employees", "employee count", "employee_count", "headcount",
                                      "size", "company size") if c in lowered), None)

    first_seen: dict[str, int] = {}
    cache: dict[str, dict] = {}
    added_cols: list[str] = []
    out_rows = []
    for i, raw in enumerate(rows, start=2):  # row 1 is the header
        domain = normalize_domain(raw.get(dcol, "")) if dcol else None
        if not domain:
            extra = {"ICP": "NO", "ICP status": "No usable domain in this row", "Why": "Domain is empty or not a valid domain",
                     "Lead tier": "Not ICP"}
        else:
            if domain not in cache:
                cache[domain] = findings(domain, raw.get(ecol, "") if ecol else "",
                                         store.get_result(domain, "icp"), store.get_result(domain, "signals"))
            extra = dict(cache[domain])
            if domain in first_seen:
                extra["Notes"] = f"Duplicate of row {first_seen[domain]}"
            else:
                first_seen[domain] = i
        for k in extra:
            if k not in added_cols and k not in original_cols:
                added_cols.append(k)
        out_rows.append({**raw, **{k: v for k, v in extra.items() if k not in original_cols}})

    # keep a stable, readable column order for the added columns
    order = ["ICP", "ICP status", "Why", "Lead score", "Lead tier", "Score breakdown", "Segment", "Segment based on",
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
