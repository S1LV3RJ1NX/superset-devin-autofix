# Operations

## Configuration

Copy `.env.example` to `.env` and provide secrets outside version control.
Required webhook configuration is `GITHUB_WEBHOOK_SECRET`. Real Devin work also
requires `DEVIN_API_KEY` and `DEVIN_ORG_ID`.

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
  elapsed PR timing.
- Structured application logs include delivery and job identifiers but not
  secrets or raw authorization headers.

## Recovery

Jobs are durable across process restarts. Queued, session-created, and running
jobs are reconsidered by the worker. Back up the SQLite volume before destructive
infrastructure changes.

Devin API errors are terminal in v1. Operators should inspect the job error and
start a new GitHub delivery after correcting the cause.

## Deployment constraints

Run one worker-enabled replica. Multiple replicas need distributed leasing.
Place read and simulation endpoints behind trusted ingress because service-level
authentication is not implemented. Human review and merge remain mandatory.
