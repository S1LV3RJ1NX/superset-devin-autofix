"""Webhook and simulation endpoint tests."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import JobRepository
from app.github import sign_payload
from app.main import create_app


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


def test_simulation_uses_shared_path_without_external_claims(
    client: TestClient, repository: JobRepository
) -> None:
    response = client.post("/simulate", json={"delivery_id": "fixture-replay"})

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
