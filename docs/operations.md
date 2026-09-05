# Operations

## Configuration

Copy `.env.example` to `.env` and provide secrets outside version control.
Required webhook configuration is `GITHUB_WEBHOOK_SECRET`. Real Devin work also
requires `DEVIN_API_KEY` and `DEVIN_ORG_ID`. Set `CONTROL_PLANE_API_KEY` to a
random secret used as the bearer token for `/jobs` and `/simulate`.

The Devin service user needs `ManageOrgSessions` to create sessions and
`ViewOrgSessions` to reconcile and poll them.

Keep `APP_ENV=production` in deployed environments. `POST /simulate` is exposed
only when `APP_ENV=development` and also requires the operator bearer token.

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
  output, PR URLs, errors, and timestamps. It requires
  `Authorization: Bearer $CONTROL_PLANE_API_KEY`.
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
reconciles that tag instead of creating another paid session. The client
paginates the filtered response, verifies exact tag membership on each returned
session, and treats multiple exact matches as an error rather than choosing by
undocumented result order. If no session can be found before
`SESSION_TIMEOUT_SECONDS`, the job moves to `needs_human_input`; an operator
must inspect Devin and the job before deciding whether to submit a new GitHub
delivery.

Worker-cycle and individual-job orchestration failures are logged without
terminating the background task. Polling errors keep the job active with an
operator-visible error so a later cycle can retry. Timeout is measured from the
original session request, not a later reconciliation timestamp. Persistent
reconciliation failures become `needs_human_input` when that deadline expires.
For an active session, the worker first reads its latest remote state. Message
retrieval failure does not block applying that state or enforcing the deadline.
A session that completed during a polling gap keeps its structured result and
PR metadata. A session that remains active is terminated through the v3 API
before the job records `timed_out`. If status itself cannot be read after the
deadline, the worker terminates the session and records `needs_human_input`
rather than inventing a result. A failed termination leaves the job active so
the worker retries rather than losing visibility of remote work.

Application shutdown signals and cancels the worker task, interrupting active
HTTP polling before the Devin client is closed.

Back up the SQLite volume before destructive infrastructure changes.

## Deployment constraints

Run one worker-enabled replica. Multiple replicas need distributed leasing.
Protect `/metrics` with trusted ingress when operational metrics are sensitive.
Human review and merge remain mandatory.
