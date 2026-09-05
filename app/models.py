"""Domain models and state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum


class JobStatus(str, Enum):
    """Persisted remediation job states."""

    RECEIVED = "received"
    QUEUED = "queued"
    SESSION_CREATED = "session_created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    NEEDS_HUMAN_INPUT = "needs_human_input"


TERMINAL_STATUSES = frozenset(
    {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.TIMED_OUT,
        JobStatus.NEEDS_HUMAN_INPUT,
    }
)

ACTIVE_STATUSES = frozenset(
    {
        JobStatus.RECEIVED,
        JobStatus.QUEUED,
        JobStatus.SESSION_CREATED,
        JobStatus.RUNNING,
    }
)

ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.RECEIVED: frozenset({JobStatus.QUEUED, JobStatus.FAILED}),
    JobStatus.QUEUED: frozenset({JobStatus.SESSION_CREATED, JobStatus.FAILED, JobStatus.TIMED_OUT}),
    JobStatus.SESSION_CREATED: frozenset(
        {
            JobStatus.RUNNING,
            JobStatus.FAILED,
            JobStatus.TIMED_OUT,
            JobStatus.NEEDS_HUMAN_INPUT,
        }
    ),
    JobStatus.RUNNING: TERMINAL_STATUSES,
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.TIMED_OUT: frozenset(),
    JobStatus.NEEDS_HUMAN_INPUT: frozenset(),
}


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class Job:
    """A persisted remediation job."""

    id: str
    delivery_id: str
    issue_number: int
    issue_title: str
    issue_body: str
    issue_url: str
    repository: str
    simulated: bool
    status: JobStatus
    received_at: datetime
    updated_at: datetime
    queued_at: datetime | None = None
    session_created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    devin_id: str | None = None
    devin_url: str | None = None
    pr_url: str | None = None
    pr_created_at: datetime | None = None
    structured_output: dict[str, object] | None = None
    last_message: str | None = None
    error: str | None = None
    attempts: int = 0

    @property
    def elapsed_seconds_to_pr(self) -> float | None:
        """Return elapsed seconds from receipt to the first observed PR."""
        if self.pr_created_at is None:
            return None
        return (self.pr_created_at - self.received_at).total_seconds()

    def as_dict(self) -> dict[str, object]:
        """Serialize the job for JSON responses."""
        return {
            "id": self.id,
            "delivery_id": self.delivery_id,
            "issue_number": self.issue_number,
            "issue_title": self.issue_title,
            "issue_url": self.issue_url,
            "repository": self.repository,
            "simulated": self.simulated,
            "status": self.status.value,
            "received_at": self.received_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "queued_at": self.queued_at.isoformat() if self.queued_at else None,
            "session_created_at": (
                self.session_created_at.isoformat() if self.session_created_at else None
            ),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (self.completed_at.isoformat() if self.completed_at else None),
            "devin_id": self.devin_id,
            "devin_url": self.devin_url,
            "pr_url": self.pr_url,
            "pr_created_at": (self.pr_created_at.isoformat() if self.pr_created_at else None),
            "elapsed_seconds_to_pr": self.elapsed_seconds_to_pr,
            "structured_output": self.structured_output,
            "last_message": self.last_message,
            "error": self.error,
            "attempts": self.attempts,
        }
