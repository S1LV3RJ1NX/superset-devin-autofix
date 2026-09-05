"""SQLite-backed orchestration worker integration tests."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import timedelta

import pytest

import app.database as database_module
import app.worker as worker_module
from app.config import Settings
from app.database import JobRepository
from app.devin import DevinMessage, DevinSession, IssueContext
from app.models import Job, JobStatus, utc_now
from app.worker import JobWorker


class SuccessfulDevinClient:
    """Deterministic fake that never performs HTTP requests."""

    async def create_session(self, issue: IssueContext, tracking_tag: str) -> DevinSession:
        assert issue.repository == "S1LV3RJ1NX/superset"
        assert tracking_tag.startswith("devin-autofix-job-")
        return DevinSession(
            session_id="devin-worker",
            status="new",
            url="https://app.devin.ai/sessions/worker",
            pull_requests=[],
        )

    async def find_session_by_tag(self, tracking_tag: str) -> DevinSession | None:
        return None

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


class ReconcilingDevinClient:
    """Fake that exposes a created session through its durable tracking tag."""

    def __init__(self) -> None:
        self.create_calls = 0
        self.tracking_tag: str | None = None
        self.session: DevinSession | None = None

    async def create_session(self, issue: IssueContext, tracking_tag: str) -> DevinSession:
        self.create_calls += 1
        self.tracking_tag = tracking_tag
        self.session = DevinSession(
            session_id="devin-reconciled",
            status="new",
            url="https://app.devin.ai/sessions/reconciled",
        )
        return self.session

    async def find_session_by_tag(self, tracking_tag: str) -> DevinSession | None:
        assert tracking_tag == self.tracking_tag
        return self.session

    async def get_session(self, devin_id: str) -> DevinSession:
        return DevinSession(
            session_id=devin_id,
            status="running",
            status_detail="working",
            url="https://app.devin.ai/sessions/reconciled",
        )

    async def list_messages(self, devin_id: str) -> list[DevinMessage]:
        return []


class UnexpectedCompletionDevinClient(SuccessfulDevinClient):
    """Fake that exits with a PR but without a recognized completion status."""

    async def get_session(self, devin_id: str) -> DevinSession:
        return DevinSession(
            session_id=devin_id,
            status="exit",
            status_detail="finished",
            url="https://app.devin.ai/sessions/unexpected",
            pull_requests=[
                {
                    "pr_url": "https://github.com/S1LV3RJ1NX/superset/pull/90",
                    "pr_state": "open",
                }
            ],
            structured_output={
                "status": "partial",
                "summary": "Opened a diagnostic PR",
                "validation": [],
                "limitations": ["Issue remains unresolved"],
                "pr_url": "https://github.com/S1LV3RJ1NX/superset/pull/90",
            },
        )

    async def list_messages(self, devin_id: str) -> list[DevinMessage]:
        return []


class ReconciliationOnlyDevinClient(SuccessfulDevinClient):
    """Fake that fails if the worker attempts a second paid create call."""

    async def create_session(self, issue: IssueContext, tracking_tag: str) -> DevinSession:
        raise AssertionError("duplicate create_session call")


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


def test_worker_recovers_received_job(
    settings: Settings,
    repository: JobRepository,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="received-before-crash",
        issue_number=89,
        issue_title="Recover received job",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/89",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    devin_client = ReconcilingDevinClient()
    worker = JobWorker(settings, repository, devin_client)

    asyncio.run(worker.run_once())

    recovered = repository.get(job.id)
    assert recovered is not None
    assert recovered.status is JobStatus.RUNNING
    assert recovered.devin_id == "devin-reconciled"
    assert devin_client.create_calls == 1


def test_worker_reconciles_session_after_persistence_failure_without_duplicate_create(
    settings: Settings,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="session-persistence-crash",
        issue_number=90,
        issue_title="Recover created session",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/90",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    devin_client = ReconcilingDevinClient()
    worker = JobWorker(settings, repository, devin_client)
    original_transition = repository.transition
    failed_once = False

    def fail_first_session_persistence(
        job_id: str,
        target: JobStatus,
        fields: Mapping[str, object] | None = None,
    ) -> Job:
        nonlocal failed_once
        if target is JobStatus.SESSION_CREATED and not failed_once:
            failed_once = True
            raise RuntimeError("database write interrupted")
        return original_transition(job_id, target, fields)

    monkeypatch.setattr(repository, "transition", fail_first_session_persistence)

    asyncio.run(worker.run_once())
    asyncio.run(worker.run_once())

    recovered = repository.get(job.id)
    assert recovered is not None
    assert recovered.status is JobStatus.RUNNING
    assert recovered.devin_id == "devin-reconciled"
    assert devin_client.create_calls == 1


def test_worker_refuses_duplicate_create_when_first_request_cannot_be_reconciled(
    settings: Settings,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="ambiguous-session-request",
        issue_number=90,
        issue_title="Do not duplicate session",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/90",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    request_time = utc_now() - timedelta(seconds=settings.session_timeout_seconds + 1)
    repository.update_runtime(
        job.id,
        {
            "attempts": 1,
            "session_requested_at": request_time,
        },
    )
    monkeypatch.setattr(worker_module, "utc_now", utc_now)
    devin_client = ReconciliationOnlyDevinClient()
    worker = JobWorker(settings, repository, devin_client)

    asyncio.run(worker.run_once())

    unresolved = repository.get(job.id)
    assert unresolved is not None
    assert unresolved.status is JobStatus.NEEDS_HUMAN_INPUT
    assert unresolved.devin_id is None
    assert unresolved.attempts == 1


def test_pr_does_not_override_unrecognized_structured_status(
    settings: Settings,
    repository: JobRepository,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="unexpected-status",
        issue_number=90,
        issue_title="Do not infer success",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/90",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    repository.transition(
        job.id,
        JobStatus.SESSION_CREATED,
        {
            "devin_id": "devin-unexpected",
            "devin_url": "https://app.devin.ai/sessions/unexpected",
        },
    )
    repository.transition(job.id, JobStatus.RUNNING)
    worker = JobWorker(settings, repository, UnexpectedCompletionDevinClient())

    asyncio.run(worker.run_once())

    completed = repository.get(job.id)
    assert completed is not None
    assert completed.status is JobStatus.FAILED
    assert completed.pr_url == "https://github.com/S1LV3RJ1NX/superset/pull/90"


@pytest.mark.parametrize("initial_status", [JobStatus.SESSION_CREATED, JobStatus.RUNNING])
def test_active_session_times_out_from_session_creation(
    settings: Settings,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
    initial_status: JobStatus,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id=f"timeout-{initial_status.value}",
        issue_number=91,
        issue_title="Timeout",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/91",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    creation_time = utc_now() - timedelta(seconds=settings.session_timeout_seconds + 1)
    monkeypatch.setattr(database_module, "utc_now", lambda: creation_time)
    repository.transition(
        job.id,
        JobStatus.SESSION_CREATED,
        {
            "devin_id": "devin-timeout",
            "devin_url": "https://app.devin.ai/sessions/timeout",
        },
    )
    if initial_status is JobStatus.RUNNING:
        repository.transition(job.id, JobStatus.RUNNING)
    monkeypatch.setattr(worker_module, "utc_now", utc_now)
    worker = JobWorker(settings, repository, SuccessfulDevinClient())

    asyncio.run(worker.run_once())

    timed_out = repository.get(job.id)
    assert timed_out is not None
    assert timed_out.status is JobStatus.TIMED_OUT


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
