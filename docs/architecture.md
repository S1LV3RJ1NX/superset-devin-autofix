# Architecture

## Responsibility

This service is a control plane between authenticated GitHub issue events and
Devin's v3 Organization Sessions API. It owns intake, persistence,
orchestration, and observability. It does not own pull-request review or merge.

## Data flow

```text
GitHub issues.labeled
  -> POST /webhooks/github
  -> HMAC-SHA256 verification
  -> event and repository filtering
  -> delivery-ID deduplication
  -> atomic queued SQLite job
  -> in-process worker
  -> tagged Devin v3 session creation, reconciliation, and polling
  -> persisted session/message/PR data
  -> GET /jobs and GET /metrics
```

HTTP handlers delegate to application services. Application services delegate
to the SQLite repository and the typed Devin client. This keeps external
boundaries injectable in tests.

## State model

```text
received -> queued
queued -> session_created | failed | needs_human_input
session_created -> running
running -> succeeded | failed | timed_out | needs_human_input
```

Transitions are validated by the repository. Terminal states are immutable.
The diagram shows transitions exercised by the worker; the repository permits
additional terminal transitions from active states for guarded recovery. The
unique GitHub delivery ID is the intake idempotency key. New webhook jobs are
inserted as queued in one transaction; the worker also recovers received rows
written by earlier versions.

Before the paid session call, the worker persists `session_requested_at` and a
single attempt. The request includes a unique job tag. If the process loses the
response or cannot persist it, the worker searches Devin by that tag, verifies
exact membership across paginated results, and never automatically issues a
second create request. Ambiguous matches are rejected instead of relying on API
ordering. Missing credentials are distinct pre-request failures and fail the
job immediately. Legacy attempts without a request timestamp use their last
durable update as the fallback deadline. An unreconciled request becomes
`needs_human_input` after the configured timeout, including when repeated
lookup failures leave the creation outcome unknown.

Session status and messages are polled independently. The message endpoint's
chronological pagination is preserved when storing the latest observed
Devin-authored message. Status remains authoritative when message retrieval
fails. An overdue session whose status cannot be observed is terminated and
marked `needs_human_input`, avoiding both unbounded external work and a
fabricated completion result.

## Trust boundaries

- GitHub input, including `X-GitHub-Delivery`, is untrusted until its raw body
  passes HMAC verification. Possession of the webhook secret is therefore part
  of the trusted operator boundary.
- Job data and development simulation require an operator bearer token checked
  against `CONTROL_PLANE_API_KEY` with constant-time comparison.
- Secrets enter through environment variables and are never persisted or
  logged.
- Devin responses are validated with typed Pydantic models.
- A PR URL is evidence of a created PR, not evidence of successful remediation;
  only structured `status=succeeded` records success.
- Simulated jobs are marked in storage and excluded from worker queries.
- The service cannot merge because it has no merge implementation or endpoint.

## Runtime model

FastAPI and the worker share one process and one SQLite database. SQLite uses
WAL mode and a busy timeout. This is intentionally a single-replica v1; a
multi-replica deployment requires durable queue ownership or job leasing.
