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
        self.db = sqlite3.connect(root / "cache.sqlite", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS sites (domain TEXT PRIMARY KEY, record TEXT, crawled_at REAL)")
        # one results table per phase: phase 1 (ICP check) and phase 2 (signals)
        for table in RESULT_TABLES.values():
            self.db.execute(f"CREATE TABLE IF NOT EXISTS {table} (domain TEXT PRIMARY KEY, result TEXT, updated_at REAL)")
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
