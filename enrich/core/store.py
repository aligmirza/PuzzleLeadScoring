"""One list's storage: raw pages in <list>/raw (gzipped), crawl records and results in <list>/cache.sqlite.

Everything downloaded once is reused, so re-running the rules costs nothing.
"""
import gzip
import json
import shutil
import sqlite3
import threading
import time
import zlib
from pathlib import Path


RESULT_TABLES = {"icp": "results", "signals": "signal_results"}  # the product pipeline's tables (the first pipeline)


def result_table(phase: str) -> str:
    """Results are kept per ICP pipeline: product uses the original tables, so existing lists keep working; another
    pipeline gets its own (results_services, ...). Pages, directories, AI answers and provider data are shared."""
    from . import icp
    pipeline = icp.name()
    return RESULT_TABLES[phase] if pipeline == "product" else f"{RESULT_TABLES[phase]}_{pipeline}"


class _Rows(list):
    """A statement's rows, already fetched."""

    def fetchone(self):
        return self[0] if self else None

    def fetchall(self):
        return list(self)


class _LockedDB:
    """The list's one SQLite connection, shared by every worker thread. Each statement runs and fetches all its rows
    under one lock: without it, threads under load read each other's rows (another company's record, or none)."""

    def __init__(self, path: Path):
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)  # autocommit
        self._lock = threading.Lock()

    def execute(self, sql: str, params=()) -> _Rows:
        with self._lock:
            return _Rows(self._db.execute(sql, params).fetchall())

    def commit(self) -> None:
        """Every statement commits on its own (autocommit); kept so callers can still call it."""

    def close(self) -> None:
        with self._lock:
            self._db.close()


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.pages_dir = root / "raw"
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        self.db = _LockedDB(root / "cache.sqlite")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS sites (domain TEXT PRIMARY KEY, record TEXT, crawled_at REAL)")
        # one results table per phase: phase 1 (ICP check) and phase 2 (signals), per ICP pipeline (result_table)
        self._tables: set[str] = set()
        for table in RESULT_TABLES.values():
            self.db.execute(f"CREATE TABLE IF NOT EXISTS {table} (domain TEXT PRIMARY KEY, result TEXT, updated_at REAL)")
            self._tables.add(table)
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
    def _results(self, phase: str) -> str:
        table = result_table(phase)
        if table not in self._tables:
            self.db.execute(f"CREATE TABLE IF NOT EXISTS {table} (domain TEXT PRIMARY KEY, result TEXT, updated_at REAL)")
            self._tables.add(table)
        return table

    def save_result(self, domain: str, result: dict, phase: str = "icp") -> None:
        blob = zlib.compress(json.dumps(result).encode(), 6)
        self.db.execute(f"INSERT OR REPLACE INTO {self._results(phase)} VALUES (?, ?, ?)", (domain, blob, time.time()))
        self.db.commit()

    def get_result(self, domain: str, phase: str = "icp") -> dict | None:
        row = self.db.execute(f"SELECT result FROM {self._results(phase)} WHERE domain = ?", (domain,)).fetchone()
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
        """{prompt_id: {step: {"answer": ..., "usage": ...}}}. Answers are facts, shared by the ICP pipelines: a
        question the running pipeline asks in this phase also gets its answers saved under the other phase (asked
        there by another pipeline, e.g. F17 is phase 2 for product and a phase 1 hard stop for services)."""
        out: dict[str, dict[int, dict]] = {}
        other: dict[str, dict[int, dict]] = {}
        for pid, step, ph, ans, usage in self.db.execute(
                "SELECT prompt_id, step, phase, answer, usage FROM ai_answers WHERE domain = ?", (domain,)):
            (out if ph == phase else other).setdefault(pid, {})[step] = {"answer": json.loads(ans), "usage": json.loads(usage)}
        if other:
            from ..ai import prompts as P
            from ..checks.rules import phase_of
            known = P.load()[1]
            for pid, steps in other.items():
                sid = known.get(pid, {}).get("signal_id")
                if pid not in out and sid and phase_of(sid) == phase:
                    out[pid] = steps
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
