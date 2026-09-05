"""Client for the Devin v3 Organization Sessions API."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings


class DevinAPIError(RuntimeError):
    """Raised for a failed Devin API request."""


class DevinPullRequest(BaseModel):
    """Pull request attached to a Devin session."""

    model_config = ConfigDict(extra="ignore")

    pr_url: str
    pr_state: str | None = None


class DevinSession(BaseModel):
    """Devin session fields used by the worker."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    status: str
    url: str
    status_detail: str | None = None
    pull_requests: list[DevinPullRequest] = Field(default_factory=list)
    structured_output: dict[str, object] | None = None

    @property
    def pr_url(self) -> str | None:
        """Return the first PR URL reported by Devin."""
        return self.pull_requests[0].pr_url if self.pull_requests else None


class DevinMessage(BaseModel):
    """A message from a Devin session."""

    model_config = ConfigDict(extra="ignore")

    event_id: str
    source: str
    message: str
    created_at: int


class DevinMessagePage(BaseModel):
    """Cursor-paginated Devin messages response."""

    model_config = ConfigDict(extra="ignore")

    items: list[DevinMessage]
    end_cursor: str | None = None
    has_next_page: bool = False


class DevinSessionPage(BaseModel):
    """Cursor-paginated Devin sessions response."""

    model_config = ConfigDict(extra="ignore")

    items: list[DevinSession]
    end_cursor: str | None = None
    has_next_page: bool = False


@dataclass(frozen=True)
class IssueContext:
    """Issue information used to construct a constrained Devin prompt."""

    number: int
    title: str
    body: str
    url: str
    repository: str


COMPLETION_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "validation", "limitations", "pr_url"],
    "properties": {
        "status": {
            "type": "string",
            "enum": ["succeeded", "failed", "needs_human_input"],
        },
        "summary": {"type": "string"},
        "validation": {
            "type": "array",
            "items": {"type": "string"},
        },
        "limitations": {
            "type": "array",
            "items": {"type": "string"},
        },
        "pr_url": {"type": ["string", "null"], "format": "uri"},
    },
}


def build_session_prompt(issue: IssueContext) -> str:
    """Build a scoped remediation prompt for a target issue."""
    return f"""Remediate GitHub issue #{issue.number} in {issue.repository}.

Issue title:
{issue.title}

Issue URL:
{issue.url}

Issue body:
{issue.body or "(no issue body provided)"}

Constraints:
- Work only in the {issue.repository} repository.
- Treat the issue as the source of requirements; ask for human input when intent is ambiguous.
- Make the smallest production-quality change that resolves the issue.
- Do not request, print, commit, or expose credentials.
- Do not weaken security controls, skip hooks, or bypass branch protections.
- Never merge or auto-merge a pull request. A human must review and merge.
- Run focused tests for the affected area.
- Run this changed-files validation before completion:
  pre-commit run --from-ref origin/master --to-ref HEAD
- Open a pull request when a code change is warranted.
- Return the required structured output with status, summary, validation,
  limitations, and the pull request URL (or null).
"""


class DevinClient:
    """Typed asynchronous v3 API client."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._http_client = http_client
        self._owns_client = http_client is None

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self.settings.devin_api_base_url,
                headers={
                    "Authorization": f"Bearer {self.settings.devin_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(30),
            )
        return self._http_client

    def _validate_configuration(self) -> None:
        if not self.settings.devin_api_key:
            raise DevinAPIError("DEVIN_API_KEY is not configured")
        if not self.settings.devin_org_id:
            raise DevinAPIError("DEVIN_ORG_ID is not configured")

    async def create_session(self, issue: IssueContext, tracking_tag: str) -> DevinSession:
        """Create a constrained Devin remediation session."""
        self._validate_configuration()
        payload: dict[str, object] = {
            "prompt": build_session_prompt(issue),
            "title": f"Autofix #{issue.number}: {issue.title}",
            "repos": [issue.repository],
            "tags": ["devin-autofix", f"github-issue-{issue.number}", tracking_tag],
            "resumable": True,
            "structured_output_required": True,
            "structured_output_schema": COMPLETION_SCHEMA,
        }
        response = await self._client().post(
            f"/v3/organizations/{self.settings.devin_org_id}/sessions",
            json=payload,
        )
        self._raise_for_status(response)
        return DevinSession.model_validate_json(response.content)

    async def find_session_by_tag(self, tracking_tag: str) -> DevinSession | None:
        """Find a session created for a durable control-plane job."""
        self._validate_configuration()
        response = await self._client().get(
            f"/v3/organizations/{self.settings.devin_org_id}/sessions",
            params={"first": 1, "tags": tracking_tag},
        )
        self._raise_for_status(response)
        page = DevinSessionPage.model_validate_json(response.content)
        return page.items[0] if page.items else None

    async def get_session(self, devin_id: str) -> DevinSession:
        """Get the latest session status and structured output."""
        self._validate_configuration()
        response = await self._client().get(
            f"/v3/organizations/{self.settings.devin_org_id}/sessions/{devin_id}"
        )
        self._raise_for_status(response)
        return DevinSession.model_validate_json(response.content)

    async def list_messages(self, devin_id: str) -> list[DevinMessage]:
        """List all session messages using v3 cursor pagination."""
        self._validate_configuration()
        messages: list[DevinMessage] = []
        after: str | None = None
        while True:
            params: dict[str, str | int] = {"first": 100}
            if after:
                params["after"] = after
            response = await self._client().get(
                (f"/v3/organizations/{self.settings.devin_org_id}/sessions/{devin_id}/messages"),
                params=params,
            )
            self._raise_for_status(response)
            page = DevinMessagePage.model_validate_json(response.content)
            messages.extend(page.items)
            if not page.has_next_page or not page.end_cursor:
                return messages
            after = page.end_cursor

    async def close(self) -> None:
        """Close the internally managed HTTP client."""
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        detail = response.reason_phrase
        try:
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
                detail = payload["detail"]
        except ValueError:
            pass
        raise DevinAPIError(f"Devin API request failed with HTTP {response.status_code}: {detail}")
