"""SQLite persistence for remediation jobs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.models import (
    ACTIVE_STATUSES,
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    Job,
    JobStatus,
    utc_now,
)

_MUTABLE_COLUMNS = frozenset(
    {
        "devin_id",
        "devin_url",
        "pr_url",
        "pr_created_at",
        "session_requested_at",
        "structured_output",
        "last_message",
        "error",
        "attempts",
    }
)


class InvalidTransitionError(ValueError):
    """Raised when a job state transition is not allowed."""


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value)


class JobRepository:
    """Persist and query jobs using short-lived SQLite connections."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create the database directory and schema if needed."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    delivery_id TEXT NOT NULL UNIQUE,
                    issue_number INTEGER NOT NULL,
                    issue_title TEXT NOT NULL,
                    issue_body TEXT NOT NULL,
                    issue_url TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    simulated INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    queued_at TEXT,
                    session_requested_at TEXT,
                    session_created_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    devin_id TEXT,
                    devin_url TEXT,
                    pr_url TEXT,
                    pr_created_at TEXT,
                    structured_output TEXT,
                    last_message TEXT,
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {
                str(row["name"]) for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "session_requested_at" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN session_requested_at TEXT")
            connection.execute(
                """
                UPDATE jobs
                SET session_requested_at = updated_at
                WHERE attempts > 0 AND session_requested_at IS NULL
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            connection.commit()

    def create_or_get(
        self,
        *,
        delivery_id: str,
        issue_number: int,
        issue_title: str,
        issue_body: str,
        issue_url: str,
        repository: str,
        simulated: bool,
    ) -> tuple[Job, bool]:
        """Create a received job or return the existing delivery."""
        return self._create_or_get(
            delivery_id=delivery_id,
            issue_number=issue_number,
            issue_title=issue_title,
            issue_body=issue_body,
            issue_url=issue_url,
            repository=repository,
            simulated=simulated,
            initial_status=JobStatus.RECEIVED,
        )

    def create_or_get_queued(
        self,
        *,
        delivery_id: str,
        issue_number: int,
        issue_title: str,
        issue_body: str,
        issue_url: str,
        repository: str,
        simulated: bool,
    ) -> tuple[Job, bool]:
        """Atomically create a queued job or return the existing delivery."""
        return self._create_or_get(
            delivery_id=delivery_id,
            issue_number=issue_number,
            issue_title=issue_title,
            issue_body=issue_body,
            issue_url=issue_url,
            repository=repository,
            simulated=simulated,
            initial_status=JobStatus.QUEUED,
        )

    def _create_or_get(
        self,
        *,
        delivery_id: str,
        issue_number: int,
        issue_title: str,
        issue_body: str,
        issue_url: str,
        repository: str,
        simulated: bool,
        initial_status: JobStatus,
    ) -> tuple[Job, bool]:
        now = utc_now()
        job_id = str(uuid4())
        queued_at = now if initial_status is JobStatus.QUEUED else None
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO jobs (
                        id, delivery_id, issue_number, issue_title, issue_body,
                        issue_url, repository, simulated, status, received_at,
                        updated_at, queued_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        delivery_id,
                        issue_number,
                        issue_title,
                        issue_body,
                        issue_url,
                        repository,
                        int(simulated),
                        initial_status.value,
                        _timestamp(now),
                        _timestamp(now),
                        _timestamp(queued_at),
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError:
            existing = self.get_by_delivery_id(delivery_id)
            if existing is None:
                raise
            return existing, False

        created = self.get(job_id)
        if created is None:
            raise RuntimeError("created job could not be loaded")
        return created, True

    def get(self, job_id: str) -> Job | None:
        """Load a job by ID."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._to_job(row) if row else None

    def get_by_delivery_id(self, delivery_id: str) -> Job | None:
        """Load a job by GitHub delivery ID."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
        return self._to_job(row) if row else None

    def list_jobs(self, *, include_simulated: bool = True) -> list[Job]:
        """List jobs newest first."""
        where = "" if include_simulated else "WHERE simulated = 0"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs {where} ORDER BY received_at DESC"  # noqa: S608
            ).fetchall()
        return [self._to_job(row) for row in rows]

    def list_by_statuses(
        self, statuses: frozenset[JobStatus], *, include_simulated: bool = False
    ) -> list[Job]:
        """List jobs in any requested state."""
        placeholders = ", ".join("?" for _ in statuses)
        simulated_clause = "" if include_simulated else "AND simulated = 0"
        values = tuple(status.value for status in statuses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM jobs
                WHERE status IN ({placeholders}) {simulated_clause}
                ORDER BY received_at
                """,
                values,
            ).fetchall()
        return [self._to_job(row) for row in rows]

    def transition(
        self,
        job_id: str,
        target: JobStatus,
        fields: Mapping[str, object] | None = None,
    ) -> Job:
        """Apply a guarded state transition and optional runtime fields."""
        updates = dict(fields or {})
        unsupported = set(updates) - _MUTABLE_COLUMNS
        if unsupported:
            raise ValueError(f"unsupported job fields: {sorted(unsupported)}")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)

            current = JobStatus(str(row["status"]))
            if target not in ALLOWED_TRANSITIONS[current]:
                raise InvalidTransitionError(
                    f"cannot transition job {job_id} from {current.value} to {target.value}"
                )

            now = utc_now()
            updates["status"] = target.value
            updates["updated_at"] = _timestamp(now)
            if target is JobStatus.QUEUED:
                updates["queued_at"] = _timestamp(now)
            elif target is JobStatus.SESSION_CREATED:
                updates["session_created_at"] = _timestamp(now)
            elif target is JobStatus.RUNNING:
                updates["started_at"] = _timestamp(now)
            elif target in TERMINAL_STATUSES:
                updates["completed_at"] = _timestamp(now)

            normalized = self._normalize_updates(updates)
            assignments = ", ".join(f"{column} = ?" for column in normalized)
            connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",  # noqa: S608
                (*normalized.values(), job_id),
            )
            connection.commit()

        updated = self.get(job_id)
        if updated is None:
            raise RuntimeError("updated job could not be loaded")
        return updated

    def update_runtime(self, job_id: str, fields: Mapping[str, object]) -> Job:
        """Update non-state runtime data on a job."""
        updates = dict(fields)
        unsupported = set(updates) - _MUTABLE_COLUMNS
        if unsupported:
            raise ValueError(f"unsupported job fields: {sorted(unsupported)}")
        updates["updated_at"] = _timestamp(utc_now())
        normalized = self._normalize_updates(updates)
        assignments = ", ".join(f"{column} = ?" for column in normalized)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",  # noqa: S608
                (*normalized.values(), job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)
            connection.commit()
        updated = self.get(job_id)
        if updated is None:
            raise RuntimeError("updated job could not be loaded")
        return updated

    def metrics(self) -> dict[str, object]:
        """Calculate production job metrics, excluding simulations."""
        all_jobs = self.list_jobs()
        return self._calculate_metrics(all_jobs)

    def dashboard_snapshot(self) -> tuple[dict[str, object], Job | None]:
        """Return metrics and the latest production job from one job snapshot."""
        all_jobs = self.list_jobs()
        latest_job = next((job for job in all_jobs if not job.simulated), None)
        return self._calculate_metrics(all_jobs), latest_job

    @staticmethod
    def _calculate_metrics(all_jobs: list[Job]) -> dict[str, object]:
        jobs = [job for job in all_jobs if not job.simulated]
        terminal_counts = {
            status.value: sum(job.status is status for job in jobs) for status in TERMINAL_STATUSES
        }
        active_tasks = sum(job.status in ACTIVE_STATUSES for job in jobs)
        terminal_tasks = sum(terminal_counts.values())
        completed_tasks = terminal_counts[JobStatus.SUCCEEDED.value]
        elapsed = [value for job in jobs if (value := job.elapsed_seconds_to_pr) is not None]
        simulated_tasks = len(all_jobs) - len(jobs)
        return {
            "tasks_started": len(jobs),
            "active_tasks": active_tasks,
            "terminal_status_counts": terminal_counts,
            "completion_rate": (completed_tasks / terminal_tasks if terminal_tasks else 0.0),
            "average_elapsed_seconds_to_pr": (sum(elapsed) / len(elapsed) if elapsed else None),
            "tasks_with_pr": len(elapsed),
            "simulated_tasks": simulated_tasks,
        }

    @staticmethod
    def _normalize_updates(updates: Mapping[str, object]) -> dict[str, object]:
        normalized: dict[str, object] = {}
        for column, value in updates.items():
            if column == "structured_output" and value is not None:
                normalized[column] = json.dumps(value, sort_keys=True)
            elif isinstance(value, datetime):
                normalized[column] = _timestamp(value)
            else:
                normalized[column] = value
        return normalized

    @staticmethod
    def _to_job(row: sqlite3.Row) -> Job:
        structured_output: dict[str, object] | None = None
        raw_structured_output = row["structured_output"]
        if isinstance(raw_structured_output, str):
            parsed = json.loads(raw_structured_output)
            if isinstance(parsed, dict):
                structured_output = {str(key): value for key, value in parsed.items()}

        return Job(
            id=str(row["id"]),
            delivery_id=str(row["delivery_id"]),
            issue_number=int(row["issue_number"]),
            issue_title=str(row["issue_title"]),
            issue_body=str(row["issue_body"]),
            issue_url=str(row["issue_url"]),
            repository=str(row["repository"]),
            simulated=bool(row["simulated"]),
            status=JobStatus(str(row["status"])),
            received_at=_parse_timestamp(row["received_at"])
            or raise_invalid_timestamp("received_at"),
            updated_at=_parse_timestamp(row["updated_at"]) or raise_invalid_timestamp("updated_at"),
            queued_at=_parse_timestamp(row["queued_at"]),
            session_requested_at=_parse_timestamp(row["session_requested_at"]),
            session_created_at=_parse_timestamp(row["session_created_at"]),
            started_at=_parse_timestamp(row["started_at"]),
            completed_at=_parse_timestamp(row["completed_at"]),
            devin_id=str(row["devin_id"]) if row["devin_id"] else None,
            devin_url=str(row["devin_url"]) if row["devin_url"] else None,
            pr_url=str(row["pr_url"]) if row["pr_url"] else None,
            pr_created_at=_parse_timestamp(row["pr_created_at"]),
            structured_output=structured_output,
            last_message=str(row["last_message"]) if row["last_message"] else None,
            error=str(row["error"]) if row["error"] else None,
            attempts=int(row["attempts"]),
        )


def raise_invalid_timestamp(column: str) -> datetime:
    """Raise a useful error for corrupt required timestamps."""
    raise ValueError(f"invalid timestamp in {column}")
