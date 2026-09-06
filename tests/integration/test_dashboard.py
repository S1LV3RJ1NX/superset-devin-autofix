"""Operator dashboard integration tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import JobRepository
from app.models import Job, JobStatus


def test_dashboard_renders_durable_metrics_and_latest_production_workflow(
    client: TestClient,
    repository: JobRepository,
) -> None:
    job, _ = repository.create_or_get_queued(
        delivery_id="dashboard-production",
        issue_number=1,
        issue_title="<script>alert('title')</script> Cache compatibility",
        issue_body="Sensitive issue body must not be rendered.",
        issue_url="javascript:alert('issue')",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(
        job.id,
        JobStatus.SESSION_CREATED,
        {
            "devin_id": "dashboard-session",
            "devin_url": "https://app.devin.ai/sessions/dashboard-session",
        },
    )
    repository.transition(job.id, JobStatus.RUNNING)
    repository.transition(
        job.id,
        JobStatus.NEEDS_HUMAN_INPUT,
        {
            "pr_url": "https://github.com/S1LV3RJ1NX/superset/pull/3",
            "pr_created_at": job.received_at + timedelta(seconds=301),
            "structured_output": {
                "summary": "Implemented the focused compatibility fix.",
                "validation": ["Focused tests passed."],
                "limitations": ["Full application validation remains out of scope."],
            },
            "last_message": "Pull request is ready for review.",
            "error": "Devin is waiting_for_user",
        },
    )
    repository.create_or_get_queued(
        delivery_id="dashboard-simulation",
        issue_number=99,
        issue_title="Newer simulation",
        issue_body="Fixture",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/99",
        repository="S1LV3RJ1NX/superset",
        simulated=True,
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert '<meta http-equiv="refresh" content="10">' in response.text
    assert "Tasks started" in response.text
    assert "PRs opened" in response.text
    assert "Simulated tasks" in response.text
    assert (
        '<div class="metric-label">Tasks started</div>\n          <div class="metric-value">1</div>'
    ) in response.text
    assert (
        '<div class="metric-label">PRs opened</div>\n          <div class="metric-value">1</div>'
    ) in response.text
    assert (
        '<div class="metric-label">Simulated tasks</div>\n'
        '          <div class="metric-value">1</div>'
    ) in response.text
    assert "Needs human review" in response.text
    assert "PR opened — human review required" in response.text
    assert "5m 1s" in response.text
    assert "Newer simulation" not in response.text
    assert "&lt;script&gt;alert(&#x27;title&#x27;)&lt;/script&gt;" in response.text
    assert "<script>alert('title')</script>" not in response.text
    assert "<script" not in response.text
    assert "/jobs" not in response.text
    assert "javascript:" not in response.text
    assert "Sensitive issue body must not be rendered." not in response.text
    assert "dashboard-production" not in response.text
    assert job.id not in response.text
    assert "Show workflow details" in response.text
    assert "Implemented the focused compatibility fix." in response.text


def test_dashboard_renders_empty_state_from_an_empty_database(client: TestClient) -> None:
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "No production workflows yet" in response.text
    assert "0.0%" in response.text
    assert "Dashboard data is temporarily unavailable" not in response.text


def test_dashboard_uses_one_immutable_job_snapshot(
    client: TestClient,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository.create_or_get_queued(
        delivery_id="snapshot-original",
        issue_number=1,
        issue_title="Original workflow",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/1",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    original_list_jobs = repository.list_jobs
    calls = 0

    def list_jobs_with_concurrent_insert(
        *,
        include_simulated: bool = True,
    ) -> list[Job]:
        nonlocal calls
        calls += 1
        jobs = original_list_jobs(include_simulated=include_simulated)
        if calls == 1:
            repository.create_or_get_queued(
                delivery_id="snapshot-concurrent",
                issue_number=2,
                issue_title="Concurrent workflow",
                issue_body="Body",
                issue_url="https://github.com/S1LV3RJ1NX/superset/issues/2",
                repository="S1LV3RJ1NX/superset",
                simulated=False,
            )
        return jobs

    monkeypatch.setattr(repository, "list_jobs", list_jobs_with_concurrent_insert)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert calls == 1
    assert "Original workflow" in response.text
    assert "Concurrent workflow" not in response.text


def test_dashboard_rejects_malformed_bracketed_urls(
    client: TestClient,
    repository: JobRepository,
) -> None:
    job, _ = repository.create_or_get_queued(
        delivery_id="malformed-urls",
        issue_number=3,
        issue_title="Malformed links",
        issue_body="Body",
        issue_url="http://[",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(
        job.id,
        JobStatus.SESSION_CREATED,
        {"devin_id": "malformed-session", "devin_url": "http://["},
    )
    repository.transition(job.id, JobStatus.RUNNING)
    repository.transition(
        job.id,
        JobStatus.NEEDS_HUMAN_INPUT,
        {
            "pr_url": "http://[",
            "pr_created_at": job.received_at + timedelta(seconds=10),
        },
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Malformed links" in response.text
    assert "http://[" not in response.text
    assert "Open pull request" not in response.text
    assert "Human input required before work can continue" in response.text
    assert "Not available" in response.text


def test_dashboard_returns_retryable_sanitized_error_state(
    client: TestClient,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_snapshot() -> tuple[dict[str, object], Job | None]:
        raise RuntimeError("sensitive database details")

    monkeypatch.setattr(repository, "dashboard_snapshot", fail_snapshot)

    response = client.get("/dashboard")

    assert response.status_code == 503
    assert '<meta http-equiv="refresh" content="10">' in response.text
    assert "Dashboard data is temporarily unavailable" in response.text
    assert "Automatic refresh will retry" in response.text
    assert "sensitive database details" not in response.text


def test_dashboard_returns_error_state_when_rendering_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_render(*args: object, **kwargs: object) -> str:
        raise RuntimeError("sensitive rendering details")

    monkeypatch.setattr("app.main.render_dashboard", fail_render)

    response = client.get("/dashboard")

    assert response.status_code == 503
    assert "Dashboard data is temporarily unavailable" in response.text
    assert "sensitive rendering details" not in response.text
