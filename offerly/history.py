"""Durable application history across task runs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel

from .job_links import application_job_url


class ApplicationRecord(BaseModel):
    key: str
    company: str
    job_title: str
    job_url: str
    state: str
    updated_at: str
    receipt: dict | None = None


def canonical_job_url(url: str) -> str:
    """Drop tracking parameters while preserving real job IDs and SPA routes."""
    parts = urlsplit(application_job_url(url))
    tracking = {
        "from",
        "source",
        "ref",
        "frompage",
        "userid",
        "sessionid",
        "referrer",
        "channel",
    }
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in tracking
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/") or "/",
            urlencode(sorted(query)),
            parts.fragment,
        )
    )


class ApplicationHistory:
    """Durable job outcomes. Existing submission receipts remain readable."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS applications (
                key TEXT PRIMARY KEY, company TEXT NOT NULL, job_title TEXT NOT NULL,
                job_url TEXT NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL, receipt TEXT)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def direct_key(self, job_url):
        return hashlib.sha256(
            json.dumps(
                ["job-v2", "", "", canonical_job_url(job_url)],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()

    def records(self, limit=100):
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM applications ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            ApplicationRecord(
                key=r[0],
                company=r[1],
                job_title=r[2],
                job_url=r[3],
                state=r[4],
                updated_at=r[5],
                receipt=json.loads(r[6]) if r[6] else None,
            ).model_dump()
            for r in rows
        ]

    def record_outcome(self, company, title, url, state, reason=""):
        """Persist lifecycle facts; human confirmation is distinct from an official receipt."""
        if state not in (
            "selected",
            "pending_review",
            "user_confirmed",
            "abandoned",
            "incomplete",
            "already_applied",
            "unavailable",
        ):
            raise ValueError("不支持的投递状态。")
        with self.connect() as db:
            db.execute(
                """INSERT INTO applications VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET company=excluded.company, job_title=excluded.job_title,
                job_url=excluded.job_url,state=excluded.state,updated_at=excluded.updated_at,receipt=excluded.receipt
                WHERE applications.state NOT IN ('submitted','user_confirmed','already_applied','unavailable')""",
                (
                    self.direct_key(url),
                    company,
                    title,
                    url,
                    state,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(
                        {
                            "source": "user"
                            if state == "user_confirmed"
                            else "workflow",
                            "reason": reason,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )

    def find_duplicate(self, company, url):
        name = "".join(company.split()).casefold()
        with self.connect() as db:
            for row in db.execute(
                "SELECT company,job_title,job_url,state FROM applications WHERE state IN ('submitted','user_confirmed','pending_review','submission_unknown','already_applied','unavailable')"
            ):
                if (
                    canonical_job_url(row[2]) == canonical_job_url(url)
                    or name
                    and "".join(row[0].split()).casefold() == name
                ):
                    return {
                        "company": row[0],
                        "title": row[1],
                        "url": row[2],
                        "state": row[3],
                    }
        return None
