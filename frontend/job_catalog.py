"""Persistent public job catalog, separate from profiles and model credentials."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, Field, JsonValue

from offerly.job_links import application_job_url

from .job_discovery import DiscoveredSource


class CatalogRecord(BaseModel):
    source: str
    company: str
    raw: dict[str, JsonValue]
    checkedAt: float


class CatalogSummary(BaseModel):
    jobs: int = 0
    companies: int = 0
    sources: int = 0
    updatedAt: float = 0


class CatalogResult(BaseModel):
    jobs: list[dict[str, JsonValue]] = Field(default_factory=list)
    catalog: CatalogSummary
    closedJobIds: list[str] = Field(default_factory=list)


class JobCatalog:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "catalog.sqlite"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS postings (
                    source TEXT NOT NULL, external_id TEXT NOT NULL, company TEXT NOT NULL,
                    record TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, checked REAL NOT NULL,
                    PRIMARY KEY(source, external_id)
                );
                CREATE INDEX IF NOT EXISTS postings_active ON postings(active, checked);
                CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, definition TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS source_checks (source TEXT PRIMARY KEY, checked REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS discovery_leads (
                    url TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL,
                    discovered REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
                );
            """)
        self.repair_job_links()

    def repair_job_links(self) -> int:
        """Migrate obsolete URLs in place, preserving posting ids and timestamps."""
        changed = []
        with self.connect() as db:
            rows = db.execute(
                "SELECT source,external_id,record FROM postings WHERE record LIKE '%job.xiaohongshu.com/%'"
            ).fetchall()
            for source, external_id, encoded in rows:
                record = CatalogRecord.model_validate_json(encoded)
                old = record.raw.get("url")
                if not isinstance(old, str):
                    continue
                corrected = application_job_url(old)
                if corrected != old:
                    record.raw["url"] = corrected
                    changed.append((record.model_dump_json(), source, external_id))
            db.executemany(
                "UPDATE postings SET record=? WHERE source=? AND external_id=?", changed
            )
        return len(changed)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def remember(self, sources: list[DiscoveredSource]):
        with self.connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO sources(id,definition) VALUES (?,?)",
                [(s.id, s.model_dump_json()) for s in sources],
            )

    def remember_leads(self, hits):
        from offerly.history import canonical_job_url

        with self.connect() as db:
            db.executemany(
                """INSERT INTO discovery_leads(url,title,summary,discovered) VALUES(?,?,?,?)
                ON CONFLICT(url) DO UPDATE SET title=excluded.title,summary=excluded.summary,
                discovered=excluded.discovered""",
                [
                    (canonical_job_url(h.url), h.title, h.snippet, time.time())
                    for h in hits
                ],
            )

    def store_verified(self, company, title, url, jd, city="", nature=None, updated=""):
        """Store public job facts independently of a user's filters or resume data."""
        from offerly.history import canonical_job_url
        from .job_discovery import public_url
        import re
        from urllib.parse import urlsplit

        if (
            not public_url(url)
            or not company.strip()
            or not title.strip()
            or len(jd.strip()) < 40
        ):
            return False
        if re.search(
            r"/(?:form|apply|resumes)(?:[/?#]|$)",
            urlsplit(url).path + "#" + urlsplit(url).fragment,
            re.I,
        ):
            return False
        url = canonical_job_url(url)
        source, external_id = (
            "web_verified",
            hashlib.sha256(url.encode()).hexdigest()[:24],
        )
        baseline = {}
        # Reuse a pre-existing collector record when its application URL is the same.
        with self.connect() as db:
            rows = db.execute(
                "SELECT source,external_id,record FROM postings WHERE record LIKE ?",
                ("%" + urlsplit(url).hostname + "%",),
            ).fetchall()
        for old_source, old_id, encoded in rows:
            old = CatalogRecord.model_validate_json(encoded)
            if canonical_job_url(str(old.raw.get("url", ""))) == url:
                source, external_id = old_source, old_id
                baseline = dict(old.raw)
                break
        body = title + " " + jd
        nature = (
            nature
            or baseline.get("nature")
            or (
                "intern"
                if re.search(r"实习|intern", title, re.I)
                else "campus"
                if re.search(r"校招|校园|应届|毕业生|20\d{2}\s*届|graduate", body, re.I)
                else "social"
            )
        )
        self.ingest(
            source,
            company,
            [
                {
                    **baseline,
                    "id": external_id,
                    "title": title,
                    "url": url,
                    "description": jd,
                    "city": city or baseline.get("city", ""),
                    "nature": nature,
                    "natureEvidence": "已核实岗位详情",
                    "updatedAt": updated or baseline.get("updatedAt", ""),
                }
            ],
        )
        with self.connect() as db:
            db.execute(
                "UPDATE discovery_leads SET status='verified' WHERE url=?", (url,)
            )
        return True

    def sources(self) -> list[DiscoveredSource]:
        with self.connect() as db:
            return [
                DiscoveredSource.model_validate_json(row[0])
                for row in db.execute("SELECT definition FROM sources ORDER BY id")
            ]

    def record_scan(self, source: str):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO source_checks(source,checked) VALUES (?,?)",
                (source, time.time()),
            )

    def recent_sources(self, seconds: int = 3600) -> set[str]:
        """Avoid re-requesting recently scanned or already persisted sources in a bulk sweep."""
        cutoff = time.time() - seconds
        with self.connect() as db:
            return {
                row[0]
                for row in db.execute(
                    "SELECT source FROM source_checks WHERE checked>? UNION SELECT source FROM postings WHERE checked>?",
                    (cutoff, cutoff),
                )
            }

    def ingest(
        self, source: str, company: str, jobs: list[dict], closed_ids: list[str] = ()
    ) -> list[str]:
        """Only explicit closure evidence removes a job; absence from a page never does."""
        now = time.time()
        records = []
        for raw in jobs:
            if not raw.get("id") or not raw.get("title") or not raw.get("url"):
                continue
            record = CatalogRecord(
                source=source, company=company, raw=raw, checkedAt=now
            )
            record.raw["url"] = application_job_url(str(record.raw["url"]))
            records.append(
                (source, str(raw["id"]), company, record.model_dump_json(), now)
            )
        removed = []
        with self.connect() as db:
            db.executemany(
                """INSERT INTO postings(source,external_id,company,record,active,checked) VALUES (?,?,?,?,1,?)
                              ON CONFLICT(source,external_id) DO UPDATE SET company=excluded.company,
                              record=excluded.record,active=1,checked=excluded.checked""",
                records,
            )
            for external_id in closed_ids:
                row = db.execute(
                    "SELECT record FROM postings WHERE source=? AND external_id=?",
                    (source, str(external_id)),
                ).fetchone()
                if row:
                    raw = CatalogRecord.model_validate_json(row[0]).raw
                    removed.append(
                        "search_"
                        + hashlib.sha256(
                            f"{source}\0{raw.get('nature')}\0{external_id}".encode()
                        ).hexdigest()[:24]
                    )
                db.execute(
                    "UPDATE postings SET active=0,checked=? WHERE source=? AND external_id=?",
                    (now, source, str(external_id)),
                )
        return removed

    def summary(self) -> CatalogSummary:
        with self.connect() as db:
            jobs, companies, updated = db.execute(
                "SELECT COUNT(*),COUNT(DISTINCT company),MAX(checked) FROM postings WHERE active=1"
            ).fetchone()
            sources = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        return CatalogSummary(
            jobs=jobs, companies=companies, sources=sources, updatedAt=updated or 0
        )

    def search(self, query) -> CatalogResult:
        from .job_search import normalize_job

        with self.connect() as db:
            rows = db.execute(
                "SELECT record FROM postings WHERE active=1 ORDER BY checked DESC LIMIT 50000"
            ).fetchall()
        jobs, seen = [], set()
        for row in rows:
            record = CatalogRecord.model_validate_json(row[0])
            job = normalize_job(
                record.raw, record.source, record.company, query, record.checkedAt
            )
            if job and job.url not in seen:
                seen.add(job.url)
                job.searchMode = "catalog"
                jobs.append(job)
        jobs.sort(key=lambda j: (-j.score, j.company, j.title))
        with self.connect() as db:
            closed = db.execute("SELECT record FROM postings WHERE active=0").fetchall()
        removed = []
        for row in closed:
            record = CatalogRecord.model_validate_json(row[0])
            raw = record.raw
            removed.append(
                "search_"
                + hashlib.sha256(
                    f"{record.source}\0{raw.get('nature')}\0{raw.get('id')}".encode()
                ).hexdigest()[:24]
            )
        return CatalogResult(
            jobs=[j.model_dump() for j in jobs[:200]],
            catalog=self.summary(),
            closedJobIds=removed,
        )
