"""Webhook-to-job application service."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.database import JobRepository
from app.github import IgnoredWebhook, parse_autofix_event, verify_signature
from app.models import Job, JobStatus


@dataclass(frozen=True)
class IngestResult:
    """Result of processing a GitHub delivery."""

    job: Job | None
    created: bool
    ignored_reason: str | None = None


class JobService:
    """Authenticate, filter, deduplicate, and enqueue webhook jobs."""

    def __init__(self, settings: Settings, repository: JobRepository) -> None:
        self.settings = settings
        self.repository = repository

    def ingest(
        self,
        *,
        body: bytes,
        signature: str | None,
        delivery_id: str | None,
        github_event: str | None,
        simulated: bool = False,
    ) -> IngestResult:
        """Process a webhook through the shared authentication and job path."""
        verify_signature(body, signature, self.settings.github_webhook_secret)
        if not delivery_id:
            raise ValueError("X-GitHub-Delivery is required")

        try:
            event = parse_autofix_event(
                body,
                github_event=github_event,
                expected_label=self.settings.autofix_label,
                target_repository=self.settings.target_repository,
            )
        except IgnoredWebhook as exc:
            return IngestResult(job=None, created=False, ignored_reason=str(exc))

        effective_delivery_id = f"simulation:{delivery_id}" if simulated else delivery_id
        job, created = self.repository.create_or_get(
            delivery_id=effective_delivery_id,
            issue_number=event.issue.number,
            issue_title=event.issue.title,
            issue_body=event.issue.body or "",
            issue_url=event.issue.html_url,
            repository=event.repository.full_name,
            simulated=simulated,
        )
        if created:
            job = self.repository.transition(job.id, JobStatus.QUEUED)
        return IngestResult(job=job, created=created)
