"""Read the lead list and clean up domains."""
import csv
import re
from pathlib import Path

DOMAIN_COLS = ["domain", "website", "url", "company domain", "company website", "company_domain", "website url"]
NAME_COLS = ["company name", "company", "name", "company_name", "account name", "organization"]
EMPLOYEE_COLS = ["employee size", "employees", "employee count", "employee_count", "headcount", "size", "company size"]


def normalize_domain(value: str) -> str | None:
    d = (value or "").strip().lower()
    d = re.sub(r"^[a-z]+://", "", d)
    d = d.split("/")[0].split("?")[0].split("#")[0].split(":")[0]
    d = d.removeprefix("www.").strip(".")
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", d):
        return None
    return d


def _pick(headers: list[str], options: list[str]) -> str | None:
    lowered = {h.strip().lower(): h for h in headers}
    for opt in options:
        if opt in lowered:
            return lowered[opt]
    return None


def load_leads(path: Path) -> tuple[list[dict], dict]:
    """Return (rows, report). Each row has domain, company, employees and the original columns."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        dcol, ncol, ecol = _pick(headers, DOMAIN_COLS), _pick(headers, NAME_COLS), _pick(headers, EMPLOYEE_COLS)
        if not dcol:
            raise SystemExit(f"No domain column found. Columns are: {headers}")
        rows, seen = [], set()
        report = {"total": 0, "bad_domain": 0, "duplicates": 0, "columns": {"domain": dcol, "name": ncol, "employees": ecol}}
        for raw in reader:
            report["total"] += 1
            domain = normalize_domain(raw.get(dcol, ""))
            if not domain:
                report["bad_domain"] += 1
                continue
            if domain in seen:
                report["duplicates"] += 1
                continue
            seen.add(domain)
            rows.append({
                "domain": domain,
                "company": (raw.get(ncol) or "").strip() if ncol else "",
                "employees": (raw.get(ecol) or "").strip() if ecol else "",
                "input": raw,
            })
    return rows, report
