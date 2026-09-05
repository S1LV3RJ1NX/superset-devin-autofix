"""Unit tests for the Devin v3 API client with mocked HTTP."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.config import Settings
from app.devin import COMPLETION_SCHEMA, DevinClient, IssueContext


def test_create_session_uses_v3_organization_api_and_constraints(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "session_id": "devin-123",
                "status": "new",
                "url": "https://app.devin.ai/sessions/123",
                "pull_requests": [],
            },
        )

    settings = Settings(
        database_path=tmp_path / "jobs.sqlite3",
        devin_api_key="secret-key",
        devin_org_id="org-123",
    )
    http_client = httpx.AsyncClient(
        base_url="https://api.devin.ai",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer secret-key"},
    )
    client = DevinClient(settings, http_client)

    session = asyncio.run(
        client.create_session(
            IssueContext(
                number=7,
                title="Fix regression",
                body="Reproduction",
                url="https://github.com/S1LV3RJ1NX/superset/issues/7",
                repository="S1LV3RJ1NX/superset",
            ),
            "devin-autofix-job-job-123",
        )
    )
    asyncio.run(http_client.aclose())

    assert session.session_id == "devin-123"
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/v3/organizations/org-123/sessions"
    assert request.headers["authorization"] == "Bearer secret-key"
    payload = json.loads(request.content)
    assert payload["repos"] == ["S1LV3RJ1NX/superset"]
    assert payload["structured_output_required"] is True
    assert payload["structured_output_schema"] == COMPLETION_SCHEMA
    assert "devin-autofix-job-job-123" in payload["tags"]
    assert "Never merge or auto-merge" in payload["prompt"]
    assert "pre-commit run --from-ref origin/master --to-ref HEAD" in payload["prompt"]


def test_find_session_by_tracking_tag(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "session_id": "devin-reconciled",
                        "status": "running",
                        "url": "https://app.devin.ai/sessions/reconciled",
                        "pull_requests": [],
                    }
                ],
                "has_next_page": False,
                "end_cursor": None,
            },
        )

    settings = Settings(
        database_path=tmp_path / "jobs.sqlite3",
        devin_api_key="secret-key",
        devin_org_id="org-123",
    )
    http_client = httpx.AsyncClient(
        base_url="https://api.devin.ai", transport=httpx.MockTransport(handler)
    )
    client = DevinClient(settings, http_client)

    session = asyncio.run(client.find_session_by_tag("devin-autofix-job-job-123"))
    asyncio.run(http_client.aclose())

    assert session is not None
    assert session.session_id == "devin-reconciled"
    assert requests[0].url.path == "/v3/organizations/org-123/sessions"
    assert requests[0].url.params["tags"] == "devin-autofix-job-job-123"
    assert requests[0].url.params["first"] == "1"


def test_terminate_session_uses_v3_organization_api(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "session_id": "devin-123",
                "status": "exit",
                "url": "https://app.devin.ai/sessions/123",
                "pull_requests": [],
            },
        )

    settings = Settings(
        database_path=tmp_path / "jobs.sqlite3",
        devin_api_key="secret-key",
        devin_org_id="org-123",
    )
    http_client = httpx.AsyncClient(
        base_url="https://api.devin.ai", transport=httpx.MockTransport(handler)
    )
    client = DevinClient(settings, http_client)

    asyncio.run(client.terminate_session("devin-123"))
    asyncio.run(http_client.aclose())

    assert len(requests) == 1
    assert requests[0].method == "DELETE"
    assert requests[0].url.path == "/v3/organizations/org-123/sessions/devin-123"


def test_poll_status_and_paginated_messages(tmp_path: Path) -> None:
    message_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal message_calls
        if request.url.path.endswith("/messages"):
            message_calls += 1
            if message_calls == 1:
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "event_id": "event-1",
                                "source": "devin",
                                "message": "Working",
                                "created_at": 1,
                            }
                        ],
                        "has_next_page": True,
                        "end_cursor": "cursor-1",
                    },
                )
            assert request.url.params["after"] == "cursor-1"
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "event_id": "event-2",
                            "source": "devin",
                            "message": "Done",
                            "created_at": 2,
                        }
                    ],
                    "has_next_page": False,
                    "end_cursor": None,
                },
            )
        return httpx.Response(
            200,
            json={
                "session_id": "devin-123",
                "status": "exit",
                "status_detail": "finished",
                "url": "https://app.devin.ai/sessions/123",
                "pull_requests": [
                    {
                        "pr_url": ("https://github.com/S1LV3RJ1NX/superset/pull/123"),
                        "pr_state": "open",
                    }
                ],
                "structured_output": {
                    "status": "succeeded",
                    "summary": "Fixed",
                    "validation": ["pytest"],
                    "limitations": [],
                    "pr_url": "https://github.com/S1LV3RJ1NX/superset/pull/123",
                },
            },
        )

    settings = Settings(
        database_path=tmp_path / "jobs.sqlite3",
        devin_api_key="secret-key",
        devin_org_id="org-123",
    )
    http_client = httpx.AsyncClient(
        base_url="https://api.devin.ai", transport=httpx.MockTransport(handler)
    )
    client = DevinClient(settings, http_client)

    session = asyncio.run(client.get_session("devin-123"))
    messages = asyncio.run(client.list_messages("devin-123"))
    asyncio.run(http_client.aclose())

    assert session.status == "exit"
    assert session.pr_url == "https://github.com/S1LV3RJ1NX/superset/pull/123"
    assert [message.message for message in messages] == ["Working", "Done"]
    assert message_calls == 2
