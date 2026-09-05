"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import JobRepository
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="development",
        database_path=tmp_path / "jobs.sqlite3",
        github_webhook_secret="test-webhook-secret",
        devin_api_key="test-devin-key",
        devin_org_id="org-test",
        poll_interval_seconds=0.01,
        session_timeout_seconds=60,
        worker_enabled=False,
    )


@pytest.fixture
def repository(settings: Settings) -> JobRepository:
    repository = JobRepository(settings.database_path)
    repository.initialize()
    return repository


@pytest.fixture
def client(settings: Settings, repository: JobRepository) -> Iterator[TestClient]:
    app = create_app(
        settings=settings,
        repository=repository,
        start_worker=False,
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def labeled_payload() -> dict[str, object]:
    return {
        "action": "labeled",
        "label": {"name": "devin-autofix"},
        "issue": {
            "number": 42,
            "title": "Fix the reported regression",
            "body": "A concise reproduction and expected behavior.",
            "html_url": "https://github.com/S1LV3RJ1NX/superset/issues/42",
        },
        "repository": {"full_name": "S1LV3RJ1NX/superset"},
    }
