# Operations

## Configuration

Copy `.env.example` to `.env` and provide secrets outside version control.
Required webhook configuration is `GITHUB_WEBHOOK_SECRET`. Real Devin work also
requires `DEVIN_API_KEY` and `DEVIN_ORG_ID`.

The Devin service user needs `ManageOrgSessions` to create sessions and
`ViewOrgSessions` to reconcile and poll them.

Keep `APP_ENV=production` in deployed environments. `POST /simulate` is exposed
only when `APP_ENV=development`.

## Container lifecycle

```bash
docker compose up --build
docker compose ps
docker compose logs -f control-plane
docker compose down
```

The service listens on port 8000 and stores SQLite data in the
`control-plane-data` volume. The Compose health check calls `/health`.

## Observability

- `/health` reports process availability.
- `/jobs` exposes durable state, Devin session URLs, last messages, structured
  output, PR URLs, errors, and timestamps.
- `/metrics` exposes task counts, terminal outcomes, completion rate, and
  elapsed PR timing. PR count and timing include all production jobs with an
  observed PR, while completion rate counts only structured successful
  outcomes.
- Structured application logs include delivery and job identifiers but not
  secrets or raw authorization headers.

## Recovery

Jobs are durable across process restarts. Received, queued, session-created,
and running jobs are reconsidered by the worker. Webhook intake inserts new
jobs directly as queued in the same transaction as delivery deduplication.

Session creation intent is persisted before calling Devin, and each request has
a unique job tag. After a lost response or database write failure, the worker
reconciles that tag instead of creating another paid session. If no session can
be found before `SESSION_TIMEOUT_SECONDS`, the job moves to
`needs_human_input`; an operator must inspect Devin and the job before deciding
whether to submit a new GitHub delivery. Polling errors remain terminal in v1.

Back up the SQLite volume before destructive infrastructure changes.

## Deployment constraints

Run one worker-enabled replica. Multiple replicas need distributed leasing.
Place read and simulation endpoints behind trusted ingress because service-level
authentication is not implemented. Human review and merge remain mandatory.
