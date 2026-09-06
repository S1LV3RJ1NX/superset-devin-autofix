"""Operator dashboard integration tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import JobRepository
from app.models import JobStatus


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


def test_dashboard_returns_retryable_sanitized_error_state(
    client: TestClient,
    repository: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_metrics() -> dict[str, object]:
        raise RuntimeError("sensitive database details")

    monkeypatch.setattr(repository, "metrics", fail_metrics)

    response = client.get("/dashboard")

    assert response.status_code == 503
    assert '<meta http-equiv="refresh" content="10">' in response.text
    assert "Dashboard data is temporarily unavailable" in response.text
    assert "Automatic refresh will retry" in response.text
    assert "sensitive database details" not in response.text
