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
  -> SQLite job
  -> in-process worker
  -> Devin v3 session creation and polling
  -> persisted session/message/PR data
  -> GET /jobs and GET /metrics
```

HTTP handlers delegate to application services. Application services delegate
to the SQLite repository and the typed Devin client. This keeps external
boundaries injectable in tests.

## State model

```text
received -> queued -> session_created -> running
                                      -> succeeded
                                      -> failed
                                      -> timed_out
                                      -> needs_human_input
```

Transitions are validated by the repository. Terminal states are immutable.
The unique GitHub delivery ID is the idempotency key.

## Trust boundaries

- GitHub input is untrusted until its raw body passes HMAC verification.
- Secrets enter through environment variables and are never persisted or
  logged.
- Devin responses are validated with typed Pydantic models.
- Simulated jobs are marked in storage and excluded from worker queries.
- The service cannot merge because it has no merge implementation or endpoint.

## Runtime model

FastAPI and the worker share one process and one SQLite database. SQLite uses
WAL mode and a busy timeout. This is intentionally a single-replica v1; a
multi-replica deployment requires durable queue ownership or job leasing.
