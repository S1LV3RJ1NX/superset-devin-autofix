"""SQLite-backed orchestration worker integration tests."""

from __future__ import annotations

import asyncio

from app.config import Settings
from app.database import JobRepository
from app.devin import DevinMessage, DevinSession, IssueContext
from app.models import JobStatus
from app.worker import JobWorker


class SuccessfulDevinClient:
    """Deterministic fake that never performs HTTP requests."""

    async def create_session(self, issue: IssueContext) -> DevinSession:
        assert issue.repository == "S1LV3RJ1NX/superset"
        return DevinSession(
            session_id="devin-worker",
            status="new",
            url="https://app.devin.ai/sessions/worker",
            pull_requests=[],
        )

    async def get_session(self, devin_id: str) -> DevinSession:
        assert devin_id == "devin-worker"
        return DevinSession(
            session_id=devin_id,
            status="exit",
            status_detail="finished",
            url="https://app.devin.ai/sessions/worker",
            pull_requests=[
                {
                    "pr_url": "https://github.com/S1LV3RJ1NX/superset/pull/88",
                    "pr_state": "open",
                }
            ],
            structured_output={
                "status": "succeeded",
                "summary": "Fixed",
                "validation": ["pytest"],
                "limitations": [],
                "pr_url": "https://github.com/S1LV3RJ1NX/superset/pull/88",
            },
        )

    async def list_messages(self, devin_id: str) -> list[DevinMessage]:
        assert devin_id == "devin-worker"
        return [
            DevinMessage(
                event_id="message-1",
                source="devin",
                message="Opened the pull request.",
                created_at=1,
            )
        ]


def test_worker_advances_job_to_success(settings: Settings, repository: JobRepository) -> None:
    job, _ = repository.create_or_get(
        delivery_id="worker-delivery",
        issue_number=88,
        issue_title="Fix worker example",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/88",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    worker = JobWorker(settings, repository, SuccessfulDevinClient())

    asyncio.run(worker.run_once())

    completed = repository.get(job.id)
    assert completed is not None
    assert completed.status is JobStatus.SUCCEEDED
    assert completed.devin_id == "devin-worker"
    assert completed.pr_url == "https://github.com/S1LV3RJ1NX/superset/pull/88"
    assert completed.last_message == "Opened the pull request."


def test_simulated_jobs_are_never_sent_to_devin(
    settings: Settings, repository: JobRepository
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="simulation:worker-delivery",
        issue_number=89,
        issue_title="Simulation",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/89",
        repository="S1LV3RJ1NX/superset",
        simulated=True,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    worker = JobWorker(settings, repository, SuccessfulDevinClient())

    asyncio.run(worker.run_once())

    unchanged = repository.get(job.id)
    assert unchanged is not None
    assert unchanged.status is JobStatus.QUEUED
    assert unchanged.devin_id is None
