"""GitHub webhook authentication and event parsing."""

from __future__ import annotations

import hashlib
import hmac
import json

from pydantic import BaseModel


class WebhookAuthenticationError(ValueError):
    """Raised when a webhook signature cannot be authenticated."""


class IgnoredWebhook(ValueError):
    """Raised when a valid webhook is outside the autofix workflow."""


class GitHubLabel(BaseModel):
    """GitHub issue label subset."""

    name: str


class GitHubIssue(BaseModel):
    """GitHub issue fields used to create a remediation job."""

    number: int
    title: str
    body: str | None = None
    html_url: str


class GitHubRepository(BaseModel):
    """GitHub repository fields used by the control plane."""

    full_name: str


class GitHubIssueLabeledEvent(BaseModel):
    """GitHub issues.labeled payload subset."""

    action: str
    label: GitHubLabel
    issue: GitHubIssue
    repository: GitHubRepository


def sign_payload(body: bytes, secret: str) -> str:
    """Return a GitHub-compatible SHA-256 webhook signature."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(body: bytes, signature: str | None, secret: str) -> None:
    """Verify X-Hub-Signature-256 without exposing the shared secret."""
    if not secret:
        raise WebhookAuthenticationError("GITHUB_WEBHOOK_SECRET is not configured")
    if not signature or not signature.startswith("sha256="):
        raise WebhookAuthenticationError("missing or malformed webhook signature")
    expected = sign_payload(body, secret)
    if not hmac.compare_digest(expected, signature):
        raise WebhookAuthenticationError("invalid webhook signature")


def parse_autofix_event(
    body: bytes,
    *,
    github_event: str | None,
    expected_label: str,
    target_repository: str,
) -> GitHubIssueLabeledEvent:
    """Parse a matching issues.labeled event or mark it ignored."""
    if github_event != "issues":
        raise IgnoredWebhook("event is not an issues event")

    raw_payload = json.loads(body)
    if not isinstance(raw_payload, dict):
        raise ValueError("webhook payload must be a JSON object")
    if raw_payload.get("action") != "labeled":
        raise IgnoredWebhook("issue action is not labeled")

    event = GitHubIssueLabeledEvent.model_validate_json(body)
    if event.label.name != expected_label:
        raise IgnoredWebhook("label does not match the autofix label")
    if event.repository.full_name != target_repository:
        raise IgnoredWebhook("event repository does not match the target repository")
    return event
