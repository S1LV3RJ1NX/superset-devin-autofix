"""FastAPI webhook and simulation integration tests."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import JobRepository
from app.devin import DevinMessage, DevinSession, IssueContext
from app.github import sign_payload
from app.main import create_app
from app.models import Job, JobStatus

OPERATOR_HEADERS = {"Authorization": "Bearer test-control-plane-key"}


class BlockingDevinClient:
    """Fake client that records cancellation of active session polling."""

    def __init__(self) -> None:
        self.poll_started = threading.Event()
        self.poll_cancelled = threading.Event()

    async def create_session(self, issue: IssueContext, tracking_tag: str) -> DevinSession:
        raise AssertionError("session creation is not expected")

    async def find_session_by_tag(self, tracking_tag: str) -> DevinSession | None:
        return None

    async def get_session(self, devin_id: str) -> DevinSession:
        self.poll_started.set()
        try:
            await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            self.poll_cancelled.set()
            raise
        return DevinSession(
            session_id=devin_id,
            status="running",
            url="https://app.devin.ai/sessions/shutdown",
        )

    async def list_messages(self, devin_id: str) -> list[DevinMessage]:
        await asyncio.sleep(0.25)
        return []

    async def terminate_session(self, devin_id: str) -> None:
        return None

    async def close(self) -> None:
        return None


def _post_webhook(
    client: TestClient,
    payload: dict[str, object],
    *,
    secret: str = "test-webhook-secret",
    delivery_id: str = "delivery-1",
    event: str = "issues",
) -> object:
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sign_payload(body, secret),
            "X-GitHub-Delivery": delivery_id,
            "X-GitHub-Event": event,
        },
    )


def test_valid_hmac_creates_queued_job(
    client: TestClient,
    repository: JobRepository,
    labeled_payload: dict[str, object],
) -> None:
    response = _post_webhook(client, labeled_payload)

    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    assert data["duplicate"] is False
    assert data["job"]["status"] == "queued"
    assert repository.list_jobs()[0].delivery_id == "delivery-1"


def test_webhook_enqueue_does_not_require_a_second_state_transaction(
    client: TestClient,
    repository: JobRepository,
    labeled_payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_transition = repository.transition

    def reject_separate_enqueue(
        job_id: str,
        target: JobStatus,
        fields: Mapping[str, object] | None = None,
    ) -> Job:
        if target is JobStatus.QUEUED:
            raise RuntimeError("separate enqueue transaction")
        return original_transition(job_id, target, fields)

    monkeypatch.setattr(repository, "transition", reject_separate_enqueue)

    response = _post_webhook(client, labeled_payload, delivery_id="atomic-enqueue")

    assert response.status_code == 200
    assert response.json()["job"]["status"] == "queued"


def test_invalid_hmac_is_rejected(client: TestClient, labeled_payload: dict[str, object]) -> None:
    response = _post_webhook(client, labeled_payload, secret="wrong-secret")

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid webhook signature"


def test_nonmatching_label_is_ignored(
    client: TestClient,
    repository: JobRepository,
    labeled_payload: dict[str, object],
) -> None:
    labeled_payload["label"] = {"name": "triage"}

    response = _post_webhook(client, labeled_payload)

    assert response.status_code == 200
    assert response.json()["ignored"] is True
    assert repository.list_jobs() == []


def test_non_labeled_issue_action_is_ignored(
    client: TestClient,
    repository: JobRepository,
    labeled_payload: dict[str, object],
) -> None:
    labeled_payload["action"] = "opened"
    labeled_payload.pop("label")

    response = _post_webhook(client, labeled_payload)

    assert response.status_code == 200
    assert response.json()["ignored"] is True
    assert repository.list_jobs() == []


def test_delivery_id_is_idempotent(
    client: TestClient,
    repository: JobRepository,
    labeled_payload: dict[str, object],
) -> None:
    first = _post_webhook(client, labeled_payload, delivery_id="same-delivery")
    second = _post_webhook(client, labeled_payload, delivery_id="same-delivery")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["job"]["id"] == first.json()["job"]["id"]
    assert len(repository.list_jobs()) == 1


def test_jobs_requires_valid_bearer_authentication(
    client: TestClient,
    labeled_payload: dict[str, object],
) -> None:
    _post_webhook(client, labeled_payload)

    missing = client.get("/jobs")
    invalid = client.get("/jobs", headers={"Authorization": "Bearer wrong-key"})
    authorized = client.get("/jobs", headers=OPERATOR_HEADERS)

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert invalid.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["total"] == 1


def test_simulation_uses_shared_path_without_external_claims(
    client: TestClient, repository: JobRepository
) -> None:
    response = client.post(
        "/simulate",
        json={"delivery_id": "fixture-replay"},
        headers=OPERATOR_HEADERS,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["simulation"] is True
    assert data["external_session_created"] is False
    assert data["pr_created"] is False
    assert data["job"]["simulated"] is True
    assert data["job"]["status"] == "queued"
    assert data["job"]["devin_id"] is None
    assert data["job"]["pr_url"] is None
    assert repository.metrics()["tasks_started"] == 0
    assert repository.metrics()["simulated_tasks"] == 1


def test_simulation_requires_valid_bearer_authentication(client: TestClient) -> None:
    missing = client.post("/simulate")
    invalid = client.post(
        "/simulate",
        headers={"Authorization": "Bearer wrong-key"},
    )

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert invalid.status_code == 401


def test_explicit_empty_simulation_payload_is_validated(
    client: TestClient,
    repository: JobRepository,
) -> None:
    response = client.post(
        "/simulate",
        json={"delivery_id": "empty-payload", "payload": {}},
        headers=OPERATOR_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert response.json()["ignored"] is True
    assert response.json()["reason"] == "issue action is not labeled"
    assert repository.list_jobs() == []


def test_simulation_is_hidden_outside_development(tmp_path: Path) -> None:
    settings = Settings(
        app_env="production",
        database_path=tmp_path / "jobs.sqlite3",
        github_webhook_secret="secret",
        worker_enabled=False,
    )
    app = create_app(settings=settings, start_worker=False)

    with TestClient(app) as production_client:
        response = production_client.post("/simulate")

    assert response.status_code == 404


def test_shutdown_cancels_active_worker_polling(
    settings: Settings,
    repository: JobRepository,
) -> None:
    job, _ = repository.create_or_get(
        delivery_id="shutdown-poll",
        issue_number=43,
        issue_title="Cancel shutdown polling",
        issue_body="Body",
        issue_url="https://github.com/S1LV3RJ1NX/superset/issues/43",
        repository="S1LV3RJ1NX/superset",
        simulated=False,
    )
    repository.transition(job.id, JobStatus.QUEUED)
    repository.transition(
        job.id,
        JobStatus.SESSION_CREATED,
        {
            "devin_id": "devin-shutdown",
            "devin_url": "https://app.devin.ai/sessions/shutdown",
        },
    )
    repository.transition(job.id, JobStatus.RUNNING)
    devin_client = BlockingDevinClient()
    app = create_app(
        settings=settings,
        repository=repository,
        devin_client=devin_client,
        start_worker=True,
    )

    with TestClient(app):
        assert devin_client.poll_started.wait(timeout=1)

    assert devin_client.poll_cancelled.is_set()
