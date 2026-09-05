"""Background orchestration for Devin remediation sessions."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Protocol

from app.config import Settings
from app.database import JobRepository
from app.devin import DevinMessage, DevinSession, IssueContext
from app.models import Job, JobStatus, utc_now

logger = logging.getLogger(__name__)


class DevinSessionClient(Protocol):
    """Operations required by the orchestration worker."""

    async def create_session(self, issue: IssueContext, tracking_tag: str) -> DevinSession:
        """Create a remediation session."""

    async def find_session_by_tag(self, tracking_tag: str) -> DevinSession | None:
        """Find a session by its durable control-plane tracking tag."""

    async def get_session(self, devin_id: str) -> DevinSession:
        """Fetch a remediation session."""

    async def list_messages(self, devin_id: str) -> list[DevinMessage]:
        """Fetch remediation session messages."""


class JobWorker:
    """Advance queued jobs and poll active Devin sessions."""

    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        devin_client: DevinSessionClient,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.devin_client = devin_client
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        """Run until stopped, isolating failures to individual jobs."""
        while not self._stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.poll_interval_seconds,
                )
            except TimeoutError:
                continue

    def stop(self) -> None:
        """Request worker shutdown."""
        self._stop_event.set()

    async def run_once(self) -> None:
        """Advance every non-simulated active job once."""
        received_jobs = self.repository.list_by_statuses(frozenset({JobStatus.RECEIVED}))
        for job in received_jobs:
            self.repository.transition(job.id, JobStatus.QUEUED)

        queued_jobs = self.repository.list_by_statuses(frozenset({JobStatus.QUEUED}))
        for job in queued_jobs:
            await self._create_session(job)

        created_jobs = self.repository.list_by_statuses(frozenset({JobStatus.SESSION_CREATED}))
        for job in created_jobs:
            if self._has_timed_out(job.session_created_at):
                self.repository.transition(
                    job.id,
                    JobStatus.TIMED_OUT,
                    {"error": "Devin session exceeded the configured timeout"},
                )
            else:
                self.repository.transition(job.id, JobStatus.RUNNING)

        running_jobs = self.repository.list_by_statuses(frozenset({JobStatus.RUNNING}))
        for job in running_jobs:
            await self._poll_session(job)

    async def _create_session(self, job: Job) -> None:
        tracking_tag = _session_tracking_tag(job.id)
        if job.attempts > 0:
            await self._reconcile_session(job, tracking_tag)
            return

        job = self.repository.update_runtime(
            job.id,
            {
                "attempts": 1,
                "error": None,
                "session_requested_at": utc_now(),
            },
        )
        try:
            session = await self.devin_client.create_session(
                IssueContext(
                    number=job.issue_number,
                    title=job.issue_title,
                    body=job.issue_body,
                    url=job.issue_url,
                    repository=job.repository,
                ),
                tracking_tag,
            )
            self.repository.transition(
                job.id,
                JobStatus.SESSION_CREATED,
                _session_fields(session),
            )
            logger.info(
                "created Devin session for job_id=%s issue_number=%s",
                job.id,
                job.issue_number,
            )
        except Exception as exc:
            logger.exception("Devin session creation outcome is uncertain for job_id=%s", job.id)
            self.repository.update_runtime(
                job.id,
                {
                    "error": (
                        "Devin session creation outcome is uncertain; "
                        f"reconciliation required: {_safe_error(exc)}"
                    )
                },
            )

    async def _reconcile_session(self, job: Job, tracking_tag: str) -> None:
        try:
            session = await self.devin_client.find_session_by_tag(tracking_tag)
        except Exception as exc:
            logger.exception("failed to reconcile Devin session for job_id=%s", job.id)
            self.repository.update_runtime(
                job.id,
                {"error": f"Devin session reconciliation failed: {_safe_error(exc)}"},
            )
            return

        if session is not None:
            self.repository.transition(
                job.id,
                JobStatus.SESSION_CREATED,
                _session_fields(session),
            )
            logger.info("reconciled Devin session for job_id=%s", job.id)
            return

        if self._has_timed_out(job.session_requested_at):
            self.repository.transition(
                job.id,
                JobStatus.NEEDS_HUMAN_INPUT,
                {
                    "error": (
                        "Could not reconcile the first Devin session creation request; "
                        "refusing to create a duplicate session"
                    )
                },
            )

    async def _poll_session(self, job: Job) -> None:
        if not job.devin_id:
            self.repository.transition(
                job.id,
                JobStatus.FAILED,
                {"error": "running job has no Devin session ID"},
            )
            return

        if self._has_timed_out(job.session_created_at or job.started_at):
            self.repository.transition(
                job.id,
                JobStatus.TIMED_OUT,
                {"error": "Devin session exceeded the configured timeout"},
            )
            return

        try:
            session, messages = await asyncio.gather(
                self.devin_client.get_session(job.devin_id),
                self.devin_client.list_messages(job.devin_id),
            )
            fields: dict[str, object] = {}
            if session.structured_output is not None:
                fields["structured_output"] = session.structured_output
            if session.pr_url and not job.pr_url:
                fields["pr_url"] = session.pr_url
                fields["pr_created_at"] = utc_now()
            devin_messages = [message.message for message in messages if message.source == "devin"]
            if devin_messages:
                fields["last_message"] = devin_messages[-1]
            if fields:
                job = self.repository.update_runtime(job.id, fields)
            self._apply_session_state(job, session)
        except Exception as exc:
            logger.exception("failed to poll Devin session for job_id=%s", job.id)
            self.repository.transition(job.id, JobStatus.FAILED, {"error": _safe_error(exc)})

    def _apply_session_state(self, job: Job, session: DevinSession) -> None:
        if session.status_detail in {"waiting_for_user", "waiting_for_approval"}:
            self.repository.transition(
                job.id,
                JobStatus.NEEDS_HUMAN_INPUT,
                {"error": f"Devin is {session.status_detail}"},
            )
            return
        if session.status == "error":
            self.repository.transition(
                job.id, JobStatus.FAILED, {"error": "Devin session reported an error"}
            )
            return
        if session.status == "suspended":
            self.repository.transition(
                job.id,
                JobStatus.NEEDS_HUMAN_INPUT,
                {"error": f"Devin session suspended: {session.status_detail or 'unknown'}"},
            )
            return
        if session.status != "exit" and session.status_detail != "finished":
            return

        output_status = (
            session.structured_output.get("status")
            if session.structured_output is not None
            else None
        )
        if output_status == JobStatus.NEEDS_HUMAN_INPUT.value:
            target = JobStatus.NEEDS_HUMAN_INPUT
        elif output_status == JobStatus.FAILED.value:
            target = JobStatus.FAILED
        elif output_status == JobStatus.SUCCEEDED.value:
            target = JobStatus.SUCCEEDED
        else:
            self.repository.transition(
                job.id,
                JobStatus.FAILED,
                {"error": "Devin completed without a recognized structured completion status"},
            )
            return
        self.repository.transition(job.id, target)

    def _has_timed_out(self, started_at: datetime | None) -> bool:
        if started_at is None:
            return False
        elapsed = (utc_now() - started_at).total_seconds()
        return elapsed >= self.settings.session_timeout_seconds


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip()
    return message[:1000] if message else exc.__class__.__name__


def _session_tracking_tag(job_id: str) -> str:
    return f"devin-autofix-job-{job_id}"


def _session_fields(session: DevinSession) -> dict[str, object]:
    fields: dict[str, object] = {
        "devin_id": session.session_id,
        "devin_url": session.url,
    }
    if session.pr_url:
        fields["pr_url"] = session.pr_url
        fields["pr_created_at"] = utc_now()
    return fields
