"""One list's storage: raw pages in <list>/raw (gzipped), crawl records and results in <list>/cache.sqlite.

Everything downloaded once is reused, so re-running the rules costs nothing.
"""
import gzip
import json
import shutil
import sqlite3
import time
import zlib
from pathlib import Path


RESULT_TABLES = {"icp": "results", "signals": "signal_results"}


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.pages_dir = root / "raw"
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "cache.sqlite", check_same_thread=False, isolation_level=None)  # autocommit
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS sites (domain TEXT PRIMARY KEY, record TEXT, crawled_at REAL)")
        # one results table per phase: phase 1 (ICP check) and phase 2 (signals)
        for table in RESULT_TABLES.values():
            self.db.execute(f"CREATE TABLE IF NOT EXISTS {table} (domain TEXT PRIMARY KEY, result TEXT, updated_at REAL)")
        # AI answers are kept apart from rule results, so re-running the rules never loses them
        self.db.execute("CREATE TABLE IF NOT EXISTS ai_answers (domain TEXT, phase TEXT, prompt_id TEXT, step INTEGER, "
                        "answer TEXT, usage TEXT, created REAL, PRIMARY KEY (domain, phase, prompt_id, step))")
        # data bought from providers (Prospeo company data, people), kept per list
        self.db.execute("CREATE TABLE IF NOT EXISTS providers (domain TEXT, provider TEXT, kind TEXT, data TEXT, "
                        "created REAL, PRIMARY KEY (domain, provider, kind))")
        # findings from the free directories (YC, SEC, IRS), looked up once per company
        self.db.execute("CREATE TABLE IF NOT EXISTS directories (domain TEXT PRIMARY KEY, found TEXT, updated_at REAL)")
        self.db.commit()

    # pages -------------------------------------------------------------
    def _page_path(self, domain: str, kind: str) -> Path:
        return self.pages_dir / domain / f"{kind}.html.gz"

    def save_page(self, domain: str, kind: str, html: str) -> None:
        path = self._page_path(domain, kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(html.encode("utf-8", "replace"), compresslevel=6))

    def load_page(self, domain: str, kind: str) -> str | None:
        path = self._page_path(domain, kind)
        if not path.exists():
            return None
        return gzip.decompress(path.read_bytes()).decode("utf-8", "replace")

    def save_jobs(self, domain: str, jobs: list) -> None:
        path = self.pages_dir / domain / "jobs.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(jobs).encode()))

    def load_jobs(self, domain: str) -> list:
        path = self.pages_dir / domain / "jobs.json.gz"
        if not path.exists():
            return []
        return json.loads(gzip.decompress(path.read_bytes()))

    # crawl records -----------------------------------------------------
    def save_site(self, domain: str, record: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO sites VALUES (?, ?, ?)", (domain, json.dumps(record), time.time())
        )
        self.db.commit()

    def get_site(self, domain: str) -> dict | None:
        row = self.db.execute("SELECT record FROM sites WHERE domain = ?", (domain,)).fetchone()
        return json.loads(row[0]) if row else None

    def all_domains(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT domain FROM sites ORDER BY domain")]

    # rule results ------------------------------------------------------
    def save_result(self, domain: str, result: dict, phase: str = "icp") -> None:
        blob = zlib.compress(json.dumps(result).encode(), 6)
        self.db.execute(f"INSERT OR REPLACE INTO {RESULT_TABLES[phase]} VALUES (?, ?, ?)", (domain, blob, time.time()))
        self.db.commit()

    def get_result(self, domain: str, phase: str = "icp") -> dict | None:
        row = self.db.execute(f"SELECT result FROM {RESULT_TABLES[phase]} WHERE domain = ?", (domain,)).fetchone()
        if not row:
            return None
        data = row[0]
        return json.loads(zlib.decompress(data) if isinstance(data, bytes) else data)

    # AI answers --------------------------------------------------------
    def save_ai(self, domain: str, phase: str, prompt_id: str, step: int, answer: dict, usage: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO ai_answers VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (domain, phase, prompt_id, step, json.dumps(answer), json.dumps(usage), time.time()))
        self.db.commit()

    def get_ai(self, domain: str, phase: str) -> dict[str, dict[int, dict]]:
        """{prompt_id: {step: {"answer": ..., "usage": ...}}}"""
        out: dict[str, dict[int, dict]] = {}
        for pid, step, ans, usage in self.db.execute(
                "SELECT prompt_id, step, answer, usage FROM ai_answers WHERE domain = ? AND phase = ?", (domain, phase)):
            out.setdefault(pid, {})[step] = {"answer": json.loads(ans), "usage": json.loads(usage)}
        return out

    def ai_costs(self, since: float = 0) -> dict[str, dict]:
        """Actual AI spend per company, from the cost saved with every answer (reused answers cost 0).
        since: only answers saved after this time (a Unix timestamp), e.g. one test run."""
        out: dict[str, dict] = {}
        for domain, phase, step, usage in self.db.execute(
                "SELECT domain, phase, step, usage FROM ai_answers WHERE created >= ?", (since,)):
            u = json.loads(usage or "{}")
            row = out.setdefault(domain, {"phase1_usd": 0.0, "phase2_usd": 0.0, "answers": 0, "web_searches": 0,
                                          "reused": 0, "tokens_in": 0, "tokens_out": 0})
            cost = float(u.get("cost") or 0)
            row["phase1_usd" if phase == "icp" else "phase2_usd"] += cost
            row["answers"] += 1
            row["web_searches"] += u.get("web_searches", 0) or 0
            row["reused"] += 1 if u.get("reused_from_cache") else 0
            row["tokens_in"] += u.get("input", 0) or 0
            row["tokens_out"] += u.get("output", 0) or 0
        for row in out.values():
            row["total_usd"] = round(row["phase1_usd"] + row["phase2_usd"], 4)
            row["phase1_usd"], row["phase2_usd"] = round(row["phase1_usd"], 4), round(row["phase2_usd"], 4)
        return out

    def delete_ai(self, domain: str, phase: str) -> None:
        self.db.execute("DELETE FROM ai_answers WHERE domain = ? AND phase = ?", (domain, phase))
        self.db.commit()

    # provider data -----------------------------------------------------
    def save_provider(self, domain: str, provider: str, kind: str, data: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO providers VALUES (?, ?, ?, ?, ?)",
                        (domain, provider, kind, json.dumps(data), time.time()))
        self.db.commit()

    def get_provider(self, domain: str) -> dict[str, dict[str, dict]]:
        """{provider: {kind: data}}"""
        out: dict[str, dict[str, dict]] = {}
        for prov, kind, data in self.db.execute("SELECT provider, kind, data FROM providers WHERE domain = ?", (domain,)):
            out.setdefault(prov, {})[kind] = json.loads(data)
        return out

    # directory findings ------------------------------------------------
    def save_directory(self, domain: str, found: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO directories VALUES (?, ?, ?)", (domain, json.dumps(found), time.time()))
        self.db.commit()

    def get_directory(self, domain: str) -> dict | None:
        row = self.db.execute("SELECT found FROM directories WHERE domain = ?", (domain,)).fetchone()
        return json.loads(row[0]) if row else None

    # space -------------------------------------------------------------
    def pages_size(self, domain: str) -> int:
        folder = self.pages_dir / domain
        return sum(f.stat().st_size for f in folder.glob("*")) if folder.exists() else 0

    def delete_pages(self, domain: str) -> int:
        """Delete a company's saved pages and jobs. The crawl record and results stay."""
        freed = self.pages_size(domain)
        shutil.rmtree(self.pages_dir / domain, ignore_errors=True)
        record = self.get_site(domain)
        if record is not None and not record.get("pages_deleted"):
            record["pages_deleted"] = True
            self.save_site(domain, record)
        return freed

    def disk_usage(self) -> dict:
        pages = sum(f.stat().st_size for f in self.pages_dir.rglob("*") if f.is_file())
        db = sum(f.stat().st_size for f in self.root.glob("cache.sqlite*"))
        return {"pages": pages, "database": db, "total": pages + db}

    def checkpoint(self) -> None:
        """Fold the write-ahead log back into the main file so it doesn't hold extra space."""
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def vacuum(self) -> None:
        self.db.execute("VACUUM")
        self.checkpoint()
