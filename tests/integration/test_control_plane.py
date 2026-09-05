"""End-to-end integration of webhook intake, persistence, worker, and metrics."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import JobRepository
from app.devin import DevinMessage, DevinSession, IssueContext
from app.github import sign_payload
from app.worker import JobWorker


class CompletingDevinClient:
    """Deterministic Devin boundary fake for control-plane integration."""

    async def create_session(self, issue: IssueContext, tracking_tag: str) -> DevinSession:
        return DevinSession(
            session_id=f"devin-{issue.number}",
            status="new",
            url=f"https://app.devin.ai/sessions/devin-{issue.number}",
        )

    async def find_session_by_tag(self, tracking_tag: str) -> DevinSession | None:
        return None

    async def get_session(self, devin_id: str) -> DevinSession:
        return DevinSession(
            session_id=devin_id,
            status="exit",
            status_detail="finished",
            url=f"https://app.devin.ai/sessions/{devin_id}",
            pull_requests=[
                {
                    "pr_url": "https://github.com/S1LV3RJ1NX/superset/pull/42",
                    "pr_state": "open",
                }
            ],
            structured_output={
                "status": "succeeded",
                "summary": "Remediation complete",
                "validation": ["focused tests"],
                "limitations": [],
                "pr_url": "https://github.com/S1LV3RJ1NX/superset/pull/42",
            },
        )

    async def list_messages(self, devin_id: str) -> list[DevinMessage]:
        return [
            DevinMessage(
                event_id=f"message-{devin_id}",
                source="devin",
                message="Remediation complete.",
                created_at=1,
            )
        ]


def test_labeled_issue_reaches_success_and_updates_metrics(
    client: TestClient,
    settings: Settings,
    repository: JobRepository,
    labeled_payload: dict[str, object],
) -> None:
    body = json.dumps(labeled_payload).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sign_payload(body, settings.github_webhook_secret),
            "X-GitHub-Delivery": "integration-delivery",
            "X-GitHub-Event": "issues",
        },
    )
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "queued"

    worker = JobWorker(settings, repository, CompletingDevinClient())
    asyncio.run(worker.run_once())

    jobs = client.get(
        "/jobs",
        headers={"Authorization": "Bearer test-control-plane-key"},
    ).json()["items"]
    assert jobs[0]["status"] == "succeeded"
    assert jobs[0]["devin_url"] == "https://app.devin.ai/sessions/devin-42"
    assert jobs[0]["pr_url"] == "https://github.com/S1LV3RJ1NX/superset/pull/42"

    metrics = client.get("/metrics").json()
    assert metrics["tasks_started"] == 1
    assert metrics["active_tasks"] == 0
    assert metrics["terminal_status_counts"]["succeeded"] == 1
    assert metrics["completion_rate"] == 1.0
