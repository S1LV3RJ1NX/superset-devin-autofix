"""SQLite job state machine and metrics integration tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.database import InvalidTransitionError, JobRepository
from app.models import Job, JobStatus, utc_now


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


def test_pr_metrics_include_every_observed_pr_regardless_of_outcome(
    repository: JobRepository,
) -> None:
    for delivery_id, outcome in (
        ("successful-pr", JobStatus.SUCCEEDED),
        ("failed-after-pr", JobStatus.FAILED),
    ):
        job_id = _create_job(repository, delivery_id)
        repository.transition(job_id, JobStatus.QUEUED)
        repository.transition(
            job_id,
            JobStatus.SESSION_CREATED,
            {
                "devin_id": f"devin-{delivery_id}",
                "devin_url": f"https://app.devin.ai/sessions/{delivery_id}",
            },
        )
        repository.transition(job_id, JobStatus.RUNNING)
        repository.update_runtime(
            job_id,
            {
                "pr_url": f"https://github.com/S1LV3RJ1NX/superset/pull/{delivery_id}",
                "pr_created_at": utc_now(),
            },
        )
        repository.transition(job_id, outcome)

    metrics = repository.metrics()

    assert metrics["tasks_with_pr"] == 2
    assert isinstance(metrics["average_elapsed_seconds_to_pr"], float)


def test_metrics_use_one_job_snapshot(
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_list_jobs = repository.list_jobs
    inserted = False

    def list_jobs_with_concurrent_insert(
        *,
        include_simulated: bool = True,
    ) -> list[Job]:
        nonlocal inserted
        jobs = original_list_jobs(include_simulated=include_simulated)
        if not inserted:
            inserted = True
            _create_job(repository, "concurrent-production")
        return jobs

    monkeypatch.setattr(repository, "list_jobs", list_jobs_with_concurrent_insert)

    metrics = repository.metrics()

    assert metrics["simulated_tasks"] == 0


def test_initialize_migrates_session_request_timestamp(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE jobs (
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

    repository = JobRepository(database_path)
    repository.initialize()

    with sqlite3.connect(database_path) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(jobs)")}
    assert "session_requested_at" in columns
