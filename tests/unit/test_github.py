"""Unit tests for GitHub webhook authentication and parsing."""

from __future__ import annotations

import json

import pytest

from app.github import (
    IgnoredWebhook,
    WebhookAuthenticationError,
    parse_autofix_event,
    sign_payload,
    verify_signature,
)


def test_signature_round_trip() -> None:
    body = b'{"action":"labeled"}'
    signature = sign_payload(body, "secret")

    verify_signature(body, signature, "secret")


@pytest.mark.parametrize(
    ("signature", "secret"),
    [
        (None, "secret"),
        ("sha1=legacy", "secret"),
        ("sha256=invalid", "secret"),
        ("sha256=invalid", ""),
    ],
)
def test_signature_rejects_missing_or_invalid_credentials(
    signature: str | None,
    secret: str,
) -> None:
    with pytest.raises(WebhookAuthenticationError):
        verify_signature(b"payload", signature, secret)


def test_parser_returns_only_matching_labeled_issue(
    labeled_payload: dict[str, object],
) -> None:
    event = parse_autofix_event(
        json.dumps(labeled_payload).encode(),
        github_event="issues",
        expected_label="devin-autofix",
        target_repository="S1LV3RJ1NX/superset",
    )

    assert event.issue.number == 42
    assert event.repository.full_name == "S1LV3RJ1NX/superset"


@pytest.mark.parametrize(
    ("github_event", "action", "label", "repository"),
    [
        ("push", "labeled", "devin-autofix", "S1LV3RJ1NX/superset"),
        ("issues", "opened", "devin-autofix", "S1LV3RJ1NX/superset"),
        ("issues", "labeled", "triage", "S1LV3RJ1NX/superset"),
        ("issues", "labeled", "devin-autofix", "example/other"),
    ],
)
def test_parser_ignores_events_outside_the_workflow(
    labeled_payload: dict[str, object],
    github_event: str,
    action: str,
    label: str,
    repository: str,
) -> None:
    labeled_payload["action"] = action
    labeled_payload["label"] = {"name": label}
    labeled_payload["repository"] = {"full_name": repository}

    with pytest.raises(IgnoredWebhook):
        parse_autofix_event(
            json.dumps(labeled_payload).encode(),
            github_event=github_event,
            expected_label="devin-autofix",
            target_repository="S1LV3RJ1NX/superset",
        )
