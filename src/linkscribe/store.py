from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATUSES = ("queued", "processing", "completed", "failed", "cancelled")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    source_url: str
    status: str
    title: str | None
    source: str | None
    duration_seconds: float | None
    transcript: str | None
    error: str | None
    attempts: int
    created_at: str
    updated_at: str
    started_at: str | None
    completed_at: str | None
    expires_at: str


class JobStore:
    def __init__(self, path: Path, ttl_hours: int = 24) -> None:
        self.path = path
        self.ttl_hours = ttl_hours

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'processing', 'completed', 'failed', 'cancelled')
                    ),
                    title TEXT,
                    source TEXT,
                    duration_seconds REAL,
                    transcript TEXT,
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_queue
                    ON jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_expiry
                    ON jobs(expires_at);
                """
            )

    @staticmethod
    def _job(row: sqlite3.Row | None) -> Job | None:
        return Job(**dict(row)) if row else None

    def create(self, source_url: str) -> Job:
        now = utc_now()
        job_id = uuid.uuid4().hex
        expires_at = now + timedelta(hours=self.ttl_hours)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, source_url, status, created_at, updated_at, expires_at
                ) VALUES (?, ?, 'queued', ?, ?, ?)
                """,
                (job_id, source_url, iso(now), iso(now), iso(expires_at)),
            )
        job = self.get(job_id)
        assert job is not None
        return job

    def get(self, job_id: str) -> Job | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job(row)

    def queue_position(self, job: Job) -> int | None:
        if job.status != "queued":
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS ahead FROM jobs
                WHERE status = 'queued' AND created_at < ?
                """,
                (job.created_at,),
            ).fetchone()
        return int(row["ahead"]) + 1

    def claim_next(self) -> Job | None:
        now = iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE jobs
                SET status = 'processing', started_at = ?, updated_at = ?, attempts = attempts + 1
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, row["id"]),
            )
            connection.commit()
        return self.get(str(row["id"]))

    def complete(
        self,
        job_id: str,
        *,
        transcript: str,
        title: str | None,
        source: str | None,
        duration_seconds: float | None,
    ) -> None:
        now = iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET
                    status = 'completed', transcript = ?, title = ?, source = ?,
                    duration_seconds = ?, error = NULL, updated_at = ?, completed_at = ?
                WHERE id = ? AND status = 'processing'
                """,
                (transcript, title, source, duration_seconds, now, now, job_id),
            )

    def fail(self, job_id: str, error: str) -> None:
        now = iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET status = 'failed', error = ?, updated_at = ?, completed_at = ?
                WHERE id = ? AND status = 'processing'
                """,
                (error[:1000], now, now, job_id),
            )

    def cancel(self, job_id: str) -> bool:
        now = iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status = 'cancelled', updated_at = ?, completed_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, job_id),
            )
        return cursor.rowcount == 1

    def recover_interrupted(self) -> int:
        now = iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status = 'queued', started_at = NULL, updated_at = ?,
                    error = 'Worker restarted; job safely re-queued.'
                WHERE status = 'processing'
                """,
                (now,),
            )
        return cursor.rowcount

    def delete_expired(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM jobs WHERE expires_at < ?", (iso(),))
        return cursor.rowcount
