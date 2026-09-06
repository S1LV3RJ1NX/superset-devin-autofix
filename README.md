# Superset Devin Autofix Control Plane

A small, Dockerized FastAPI service that turns an authenticated GitHub
`issues.labeled` webhook into a durable Devin remediation job for
`S1LV3RJ1NX/superset`.

The service creates and observes Devin sessions. It has no merge endpoint and
never merges or auto-merges pull requests.

## Documentation

- [Setup and local run](docs/setup.md)
- [Live GitHub-to-Devin demonstration](docs/live-demo.md)
- [Control-plane API and Devin session contract](docs/api.md)
- [Architecture and data flow](docs/architecture.md)
- [Application modules](docs/modules.md)
- [Development with Python 3.13 and uv](docs/development.md)
- [Testing strategy](docs/testing.md)
- [Operations](docs/operations.md)
- [Decision log](decisions.md)

## Architecture

```text
GitHub webhook
  -> HMAC verification and event filtering
  -> SQLite delivery deduplication and job state machine
  -> background worker
  -> Devin v3 Organization Sessions API
  -> JSON operator endpoints and server-rendered dashboard
```

The boundaries are intentionally small:

- `app/main.py`: HTTP routes and application lifecycle.
- `app/github.py`: webhook authentication and issues.labeled parsing.
- `app/service.py`: idempotent webhook-to-job workflow.
- `app/database.py`: SQLite schema, queries, and guarded transitions.
- `app/dashboard.py`: sanitized, server-rendered operator presentation.
- `app/devin.py`: typed client for the v3 Organization Sessions API.
- `app/worker.py`: session creation, polling, timeout, and terminal-state mapping.

Jobs move through:

```text
received -> queued
queued -> session_created | failed | needs_human_input
session_created -> running
running -> succeeded | failed | timed_out | needs_human_input
```

Each job records its GitHub delivery and issue, timestamps, simulation marker,
session-request timestamp, Devin session ID/URL, latest observed Devin-authored
message, structured completion output, observed PR URL, and elapsed time to the
first observed PR.

## Quick start

Copy the local configuration template, add your local-only credentials, and
start the Dockerized service:

```bash
cp .env.example .env
docker compose up --build
```

Then verify it is healthy:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health
```

Read [Setup and local run](docs/setup.md) for configuration requirements and
development options. Read [Live GitHub-to-Devin demonstration](docs/live-demo.md)
for the signed GitHub webhook, Cloudflare/ngrok tunnel choices, evidence
collection, and shutdown steps.

## Interfaces

Read [Control-plane API and Devin session contract](docs/api.md) for webhook,
operator, metrics, simulation, and Devin v3 session details.

Open `GET /dashboard` for a lightweight operator view of durable metrics and
the latest production workflow. The server renders selected, escaped SQLite
data directly, refreshes every 10 seconds, and provides empty and retryable
database-error states. It uses no browser JavaScript or `/jobs` credentials;
simulations remain visible only in their summary count and never replace the
latest production workflow.

## Validation

```bash
cp .env.example .env
make check
make test-unit
make test-integration
docker build -t superset-devin-autofix .
docker compose config -q
```

`make check` runs the full pre-push pre-commit stage: lockfile validation,
repository hygiene, Ruff lint/format, mypy, unit tests, and integration tests.
Tests use temporary SQLite databases and deterministic HTTP/session fakes; no
test calls the real Devin API.

## Version 1 limitations

- The background worker is designed for one service replica; distributed
  leasing is not implemented.
- Status-polling errors before the deadline keep jobs active for later retries,
  but retry backoff is not implemented. Worker-cycle and per-job orchestration
  failures are logged and retried on later cycles. An uncertain creation
  outcome is reconciled by job tag and escalated for human review when lookup
  remains unavailable or no session appears before the configured timeout.
  Missing Devin credentials fail before reconciliation because no external
  request was attempted.
- SQLite is local to one deployment and has no external backup automation.
- `/dashboard`, `/metrics`, and `/health` remain unauthenticated; use trusted
  ingress if operational data should not be public.
- GitHub status comments and checks are not written in this version.
