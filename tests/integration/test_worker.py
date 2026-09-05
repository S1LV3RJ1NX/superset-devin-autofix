"""SQLite-backed orchestration worker integration tests."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

import app.database as database_module
import app.worker as worker_module
from app.config import Settings
from app.database import JobRepository
from app.devin import DevinClient, DevinMessage, DevinSession, IssueContext
from app.models import Job, JobStatus, utc_now
from app.worker import JobWorker


class SuccessfulDevinClient:
    """Deterministic fake that never performs HTTP requests."""

    def __init__(self) -> None:
        self.terminated_sessions: list[str] = []

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

    async def terminate_session(self, devin_id: str) -> None:
        self.terminated_sessions.append(devin_id)


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

    async def terminate_session(self, devin_id: str) -> None:
        return None


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


class StructuredPullRequestDevinClient(SuccessfulDevinClient):
    """Fake that reports its PR only through structured completion output."""

    async def get_session(self, devin_id: str) -> DevinSession:
        return DevinSession(
            session_id=devin_id,
            status="exit",
            status_detail="finished",
            url="https://app.devin.ai/sessions/structured-pr",
            structured_output={
                "status": "succeeded",
                "summary": "Fixed",
                "validation": ["pytest"],
                "limitations": [],
                "pr_url": "https://github.com/S1LV3RJ1NX/superset/pull/91",
            },
        )


class ActiveSessionDevinClient(SuccessfulDevinClient):
    """Fake that reports an active remote session."""

    async def get_session(self, devin_id: str) -> DevinSession:
        return DevinSession(
            session_id=devin_id,
            status="running",
            url=f"https://app.devin.ai/sessions/{devin_id}",
        )

    async def list_messages(self, devin_id: str) -> list[DevinMessage]:
        return []


class FailingTerminationDevinClient(ActiveSessionDevinClient):
    """Fake that cannot terminate an active session."""

    async def terminate_session(self, devin_id: str) -> None:
        raise RuntimeError("termination unavailable")


class CompletedDuringGapDevinClient(SuccessfulDevinClient):
    """Fake that reports completion after a local polling gap."""

    def __init__(self) -> None:
        super().__init__()
        self.session_reads = 0
        self.message_reads = 0

    async def get_session(self, devin_id: str) -> DevinSession:
        self.session_reads += 1
        return DevinSession(
            session_id=devin_id,
            status="exit",
            status_detail="finished",
            url=f"https://app.devin.ai/sessions/{devin_id}",
            pull_requests=[
                {
                    "pr_url": "https://github.com/S1LV3RJ1NX/superset/pull/92",
                    "pr_state": "open",
                }
            ],
            structured_output={
                "status": "succeeded",
                "summary": "Completed while the worker was stopped",
                "validation": ["pytest"],
                "limitations": [],
                "pr_url": "https://github.com/S1LV3RJ1NX/superset/pull/92",
            },
        )

    async def list_messages(self, devin_id: str) -> list[DevinMessage]:
        self.message_reads += 1
        return []


class ReconciliationOnlyDevinClient(SuccessfulDevinClient):
    """Fake that fails if the worker attempts a second paid create call."""

    async def create_session(self, issue: IssueContext, tracking_tag: str) -> DevinSession:
        raise AssertionError("duplicate create_session call")


class TransientPollingFailureDevinClient(SuccessfulDevinClient):
    """Fake whose first session read fails before a successful retry."""

    def __init__(self) -> None:
        super().__init__()
        self.session_reads = 0

    async def get_session(self, devin_id: str) -> DevinSession:
        self.session_reads += 1
        if self.session_reads == 1:
            raise RuntimeError("poll unavailable")
        return await super().get_session(devin_id)


class FailingMessageDevinClient(SuccessfulDevinClient):
    """Fake whose session is observable while message polling fails."""

    async def list_messages(self, devin_id: str) -> list[DevinMessage]:
        raise RuntimeError("messages unavailable")


class FailingMessageActiveSessionDevinClient(ActiveSessionDevinClient):
    """Fake whose active session is observable while message polling fails."""

    async def list_messages(self, devin_id: str) -> list[DevinMessage]:
        raise RuntimeError("messages unavailable")


class FailingStatusDevinClient(SuccessfulDevinClient):
    """Fake whose session status cannot be observed."""

    async def get_session(self, devin_id: str) -> DevinSession:
        raise RuntimeError("status unavailable")


class FailingReconciliationDevinClient(ReconciliationOnlyDevinClient):
    """Fake whose tracking-tag lookup remains unavailable."""

    async def find_session_by_tag(self, tracking_tag: str) -> DevinSession | None:
        raise RuntimeError("lookup unavailable")


def test_worker_run_recovers_after_cycle_failure(
    settings: Settings,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = JobWorker(settings, repository, SuccessfulDevinClient())
    calls = 0

    async def fail_once_then_stop() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary repository failure")
        worker.stop()

    monkeypatch.setattr(worker, "run_once", fail_once_then_stop)

    asyncio.run(asyncio.wait_for(worker.run(), timeout=1))

    assert calls == 2


def test_worker_isolates_transition_failure_to_one_job(
    settings: Settings,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_job, _ = repository.create_or_get(
        delivery_id="failed-transition",
        issue_number=86,
        issue_title="Fail one transition",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/86",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    healthy_job, _ = repository.create_or_get(
        delivery_id="healthy-transition",
        issue_number=87,
        issue_title="Continue after transition failure",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/87",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    original_transition = repository.transition

    def fail_one_transition(
        job_id: str,
        target: JobStatus,
        fields: Mapping[str, object] | None = None,
    ) -> Job:
        if job_id == failed_job.id and target is JobStatus.QUEUED:
            raise RuntimeError("job-specific transition failure")
        return original_transition(job_id, target, fields)

    monkeypatch.setattr(repository, "transition", fail_one_transition)
    worker = JobWorker(settings, repository, SuccessfulDevinClient())

    asyncio.run(worker.run_once())

    failed = repository.get(failed_job.id)
    advanced = repository.get(healthy_job.id)
    assert failed is not None
    assert advanced is not None
    assert failed.status is JobStatus.RECEIVED
    assert advanced.status is JobStatus.SUCCEEDED


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


def test_worker_records_pr_reported_only_in_structured_output(
    settings: Settings,
    repository: JobRepository,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="structured-pr",
        issue_number=91,
        issue_title="Record structured PR",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/91",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    worker = JobWorker(settings, repository, StructuredPullRequestDevinClient())

    asyncio.run(worker.run_once())

    completed = repository.get(job.id)
    assert completed is not None
    assert completed.status is JobStatus.SUCCEEDED
    assert completed.pr_url == "https://github.com/S1LV3RJ1NX/superset/pull/91"
    assert repository.metrics()["tasks_with_pr"] == 1


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


@pytest.mark.parametrize(
    ("devin_api_key", "devin_org_id", "expected_error"),
    [
        ("", "org-test", "DEVIN_API_KEY is not configured"),
        ("test-key", "", "DEVIN_ORG_ID is not configured"),
    ],
)
def test_missing_devin_configuration_fails_before_reconciliation(
    settings: Settings,
    repository: JobRepository,
    devin_api_key: str,
    devin_org_id: str,
    expected_error: str,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="missing-devin-configuration",
        issue_number=90,
        issue_title="Reject unconfigured work",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/90",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    unconfigured_settings = replace(
        settings,
        devin_api_key=devin_api_key,
        devin_org_id=devin_org_id,
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    http_client = httpx.AsyncClient(
        base_url=unconfigured_settings.devin_api_base_url,
        transport=httpx.MockTransport(handler),
    )
    worker = JobWorker(
        unconfigured_settings,
        repository,
        DevinClient(unconfigured_settings, http_client),
    )

    asyncio.run(worker.run_once())
    asyncio.run(http_client.aclose())

    failed = repository.get(job.id)
    assert failed is not None
    assert failed.status is JobStatus.FAILED
    assert failed.error == f"Devin session creation failed: {expected_error}"
    assert requests == []


def test_legacy_creation_attempt_without_request_time_expires(
    settings: Settings,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="legacy-attempt-without-request-time",
        issue_number=90,
        issue_title="Expire legacy reconciliation",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/90",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    legacy_attempt_time = utc_now() - timedelta(seconds=settings.session_timeout_seconds + 1)
    monkeypatch.setattr(database_module, "utc_now", lambda: legacy_attempt_time)
    repository.update_runtime(job.id, {"attempts": 1})
    monkeypatch.setattr(worker_module, "utc_now", utc_now)
    worker = JobWorker(settings, repository, ReconciliationOnlyDevinClient())

    asyncio.run(worker.run_once())

    unresolved = repository.get(job.id)
    assert unresolved is not None
    assert unresolved.status is JobStatus.NEEDS_HUMAN_INPUT
    assert unresolved.session_requested_at == legacy_attempt_time


def test_polling_failure_keeps_session_active_for_retry(
    settings: Settings,
    repository: JobRepository,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="transient-poll-failure",
        issue_number=90,
        issue_title="Retry session polling",
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
            "devin_id": "devin-worker",
            "devin_url": "https://app.devin.ai/sessions/worker",
        },
    )
    repository.transition(job.id, JobStatus.RUNNING)
    devin_client = TransientPollingFailureDevinClient()
    worker = JobWorker(settings, repository, devin_client)

    asyncio.run(worker.run_once())

    retryable = repository.get(job.id)
    assert retryable is not None
    assert retryable.status is JobStatus.RUNNING
    assert retryable.error == "Devin session status polling failed: poll unavailable"

    asyncio.run(worker.run_once())

    completed = repository.get(job.id)
    assert completed is not None
    assert completed.status is JobStatus.SUCCEEDED
    assert devin_client.session_reads == 2
    assert devin_client.terminated_sessions == []


def test_overdue_active_session_terminates_when_message_polling_fails(
    settings: Settings,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="overdue-message-failure",
        issue_number=90,
        issue_title="Enforce timeout without messages",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/90",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    repository.update_runtime(
        job.id,
        {
            "session_requested_at": (
                utc_now() - timedelta(seconds=settings.session_timeout_seconds + 1)
            )
        },
    )
    repository.transition(
        job.id,
        JobStatus.SESSION_CREATED,
        {
            "devin_id": "devin-worker",
            "devin_url": "https://app.devin.ai/sessions/worker",
        },
    )
    repository.transition(job.id, JobStatus.RUNNING)
    monkeypatch.setattr(worker_module, "utc_now", utc_now)
    devin_client = FailingMessageActiveSessionDevinClient()
    worker = JobWorker(settings, repository, devin_client)

    asyncio.run(worker.run_once())

    timed_out = repository.get(job.id)
    assert timed_out is not None
    assert timed_out.status is JobStatus.TIMED_OUT
    assert devin_client.terminated_sessions == ["devin-worker"]


def test_completed_session_succeeds_when_message_polling_fails(
    settings: Settings,
    repository: JobRepository,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="completed-message-failure",
        issue_number=90,
        issue_title="Preserve completion without messages",
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
            "devin_id": "devin-worker",
            "devin_url": "https://app.devin.ai/sessions/worker",
        },
    )
    repository.transition(job.id, JobStatus.RUNNING)
    devin_client = FailingMessageDevinClient()
    worker = JobWorker(settings, repository, devin_client)

    asyncio.run(worker.run_once())

    completed = repository.get(job.id)
    assert completed is not None
    assert completed.status is JobStatus.SUCCEEDED
    assert completed.pr_url == "https://github.com/S1LV3RJ1NX/superset/pull/88"
    assert completed.structured_output is not None
    assert devin_client.terminated_sessions == []


def test_overdue_unobservable_session_is_terminated_as_unknown(
    settings: Settings,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="overdue-status-failure",
        issue_number=90,
        issue_title="Stop unobservable overdue session",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/90",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    repository.update_runtime(
        job.id,
        {
            "session_requested_at": (
                utc_now() - timedelta(seconds=settings.session_timeout_seconds + 1)
            )
        },
    )
    repository.transition(
        job.id,
        JobStatus.SESSION_CREATED,
        {
            "devin_id": "devin-worker",
            "devin_url": "https://app.devin.ai/sessions/worker",
        },
    )
    repository.transition(job.id, JobStatus.RUNNING)
    monkeypatch.setattr(worker_module, "utc_now", utc_now)
    devin_client = FailingStatusDevinClient()
    worker = JobWorker(settings, repository, devin_client)

    asyncio.run(worker.run_once())

    unresolved = repository.get(job.id)
    assert unresolved is not None
    assert unresolved.status is JobStatus.NEEDS_HUMAN_INPUT
    assert unresolved.error is not None
    assert "status unavailable" in unresolved.error
    assert "terminated" in unresolved.error
    assert devin_client.terminated_sessions == ["devin-worker"]


def test_reconciled_session_timeout_uses_original_request_time(
    settings: Settings,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="reconciled-session-timeout",
        issue_number=91,
        issue_title="Preserve reconciliation deadline",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/91",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    repository.update_runtime(
        job.id,
        {
            "attempts": 1,
            "session_requested_at": (
                utc_now() - timedelta(seconds=settings.session_timeout_seconds + 1)
            ),
        },
    )
    repository.transition(
        job.id,
        JobStatus.SESSION_CREATED,
        {
            "devin_id": "devin-worker",
            "devin_url": "https://app.devin.ai/sessions/worker",
        },
    )
    repository.transition(job.id, JobStatus.RUNNING)
    monkeypatch.setattr(worker_module, "utc_now", utc_now)
    devin_client = ActiveSessionDevinClient()
    worker = JobWorker(settings, repository, devin_client)

    asyncio.run(worker.run_once())

    timed_out = repository.get(job.id)
    assert timed_out is not None
    assert timed_out.status is JobStatus.TIMED_OUT
    assert devin_client.terminated_sessions == ["devin-worker"]


def test_reconciliation_failure_honors_original_request_deadline(
    settings: Settings,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="reconciliation-timeout",
        issue_number=91,
        issue_title="Stop uncertain reconciliation",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/91",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    repository.update_runtime(
        job.id,
        {
            "attempts": 1,
            "session_requested_at": (
                utc_now() - timedelta(seconds=settings.session_timeout_seconds + 1)
            ),
        },
    )
    monkeypatch.setattr(worker_module, "utc_now", utc_now)
    worker = JobWorker(settings, repository, FailingReconciliationDevinClient())

    asyncio.run(worker.run_once())

    unresolved = repository.get(job.id)
    assert unresolved is not None
    assert unresolved.status is JobStatus.NEEDS_HUMAN_INPUT
    assert unresolved.error is not None
    assert "reconciliation failed: lookup unavailable" in unresolved.error
    assert "deadline exceeded" in unresolved.error


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
    devin_client = ActiveSessionDevinClient()
    worker = JobWorker(settings, repository, devin_client)

    asyncio.run(worker.run_once())

    timed_out = repository.get(job.id)
    assert timed_out is not None
    assert timed_out.status is JobStatus.TIMED_OUT
    assert devin_client.terminated_sessions == ["devin-timeout"]


@pytest.mark.parametrize("initial_status", [JobStatus.SESSION_CREATED, JobStatus.RUNNING])
def test_overdue_completed_session_preserves_remote_result(
    settings: Settings,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
    initial_status: JobStatus,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id=f"completed-during-gap-{initial_status.value}",
        issue_number=92,
        issue_title="Completed during polling gap",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/92",
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
            "devin_id": "devin-completed-during-gap",
            "devin_url": "https://app.devin.ai/sessions/completed-during-gap",
        },
    )
    if initial_status is JobStatus.RUNNING:
        repository.transition(job.id, JobStatus.RUNNING)
    monkeypatch.setattr(worker_module, "utc_now", utc_now)
    devin_client = CompletedDuringGapDevinClient()
    worker = JobWorker(settings, repository, devin_client)

    asyncio.run(worker.run_once())

    completed = repository.get(job.id)
    assert completed is not None
    assert completed.status is JobStatus.SUCCEEDED
    assert completed.pr_url == "https://github.com/S1LV3RJ1NX/superset/pull/92"
    assert completed.structured_output is not None
    assert devin_client.session_reads == 1
    assert devin_client.message_reads == 1
    assert devin_client.terminated_sessions == []


def test_timeout_remains_active_until_remote_termination_succeeds(
    settings: Settings,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="timeout-termination-failure",
        issue_number=92,
        issue_title="Retry termination",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/92",
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
            "devin_id": "devin-termination-failure",
            "devin_url": "https://app.devin.ai/sessions/termination-failure",
        },
    )
    repository.transition(job.id, JobStatus.RUNNING)
    monkeypatch.setattr(worker_module, "utc_now", utc_now)
    worker = JobWorker(settings, repository, FailingTerminationDevinClient())

    asyncio.run(worker.run_once())

    active = repository.get(job.id)
    assert active is not None
    assert active.status is JobStatus.RUNNING
    assert active.error == "Devin session termination failed: termination unavailable"


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
