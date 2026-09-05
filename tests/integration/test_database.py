"""SQLite job state machine and metrics integration tests."""

from __future__ import annotations

import pytest

from app.database import InvalidTransitionError, JobRepository
from app.models import JobStatus, utc_now


def _create_job(repository: JobRepository, delivery_id: str = "delivery") -> str:
    job, created = repository.create_or_get(
        delivery_id=delivery_id,
        issue_number=10,
        issue_title="Example",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/10",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    assert created is True
    return job.id


def test_state_transitions_are_guarded(repository: JobRepository) -> None:
    job_id = _create_job(repository)

    with pytest.raises(InvalidTransitionError):
        repository.transition(job_id, JobStatus.RUNNING)

    repository.transition(job_id, JobStatus.QUEUED)
    repository.transition(
        job_id,
        JobStatus.SESSION_CREATED,
        {
            "devin_id": "devin-test",
            "devin_url": "https://app.devin.ai/sessions/test",
        },
    )
    repository.transition(job_id, JobStatus.RUNNING)
    finished = repository.transition(job_id, JobStatus.SUCCEEDED)

    assert finished.completed_at is not None
    with pytest.raises(InvalidTransitionError):
        repository.transition(job_id, JobStatus.FAILED)


def test_metrics_include_terminal_counts_and_elapsed_pr_time(
    repository: JobRepository,
) -> None:
    succeeded_id = _create_job(repository, "succeeded")
    repository.transition(succeeded_id, JobStatus.QUEUED)
    repository.transition(
        succeeded_id,
        JobStatus.SESSION_CREATED,
        {
            "devin_id": "devin-success",
            "devin_url": "https://app.devin.ai/sessions/success",
        },
    )
    repository.transition(succeeded_id, JobStatus.RUNNING)
    repository.update_runtime(
        succeeded_id,
        {
            "pr_url": "https://github.com/S1LV3RJ1NX/superset/pull/99",
            "pr_created_at": utc_now(),
        },
    )
    repository.transition(succeeded_id, JobStatus.SUCCEEDED)

    active_id = _create_job(repository, "active")
    repository.transition(active_id, JobStatus.QUEUED)

    metrics = repository.metrics()

    assert metrics["tasks_started"] == 2
    assert metrics["active_tasks"] == 1
    assert metrics["terminal_status_counts"]["succeeded"] == 1
    assert metrics["completion_rate"] == 1.0
    assert metrics["tasks_with_pr"] == 1
    assert isinstance(metrics["average_elapsed_seconds_to_pr"], float)
