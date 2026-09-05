# Superset Devin Autofix Control Plane

A small, Dockerized FastAPI service that turns an authenticated GitHub
`issues.labeled` webhook into a durable Devin remediation job for
`S1LV3RJ1NX/superset`.

The service creates and observes Devin sessions. It has no merge endpoint and
never merges or auto-merges pull requests.

## Documentation

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
  -> JSON job and aggregate metrics endpoints
```

The boundaries are intentionally small:

- `app/main.py`: HTTP routes and application lifecycle.
- `app/github.py`: webhook authentication and issues.labeled parsing.
- `app/service.py`: idempotent webhook-to-job workflow.
- `app/database.py`: SQLite schema, queries, and guarded transitions.
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

## Configuration

Copy the existing example and supply secrets only in your local environment:

```bash
cp .env.example .env
```

Required for webhook intake and development simulation:

- `GITHUB_WEBHOOK_SECRET`

Required for operator endpoints:

- `CONTROL_PLANE_API_KEY`

Required for real session creation:

- `DEVIN_API_KEY` (a v3 service-user credential with the `cog_` prefix)
- `DEVIN_ORG_ID` (an ID with the `org-` prefix)

The Devin service user needs organization-level `ManageOrgSessions` and
`ViewOrgSessions` permissions so uncertain creation outcomes can be reconciled
without issuing a duplicate paid request.

Important optional settings:

- `APP_ENV`: set to `development` to enable `POST /simulate`.
- `DATABASE_PATH`: defaults to `data/control-plane.sqlite3` outside Compose.
- `DEVIN_API_BASE_URL`: defaults to `https://api.devin.ai`.
- `TARGET_REPOSITORY`: defaults to `S1LV3RJ1NX/superset`.
- `AUTOFIX_LABEL`: defaults to `devin-autofix`.
- `POLL_INTERVAL_SECONDS`: defaults to `10`.
- `SESSION_TIMEOUT_SECONDS`: defaults to `3600`.
- `WORKER_ENABLED`: defaults to `true`.

`GITHUB_TOKEN` remains reserved for future GitHub status/comment updates and is
not used by this version.

## Run

### Docker Compose

```bash
docker compose up --build
```

### Local development

Python 3.13 and [uv](https://docs.astral.sh/uv/) are required. The committed
`.python-version` selects the Python 3.13 series, and `uv.lock` locks the
dependency graph.

```bash
make install
make hooks
make run
```

The service listens on port `8000`. Docker Compose reads `.env` automatically;
local `make run` reads the process environment instead. Export the required
variables in the shell, and leave `DATABASE_PATH` unset or set it to a writable
local path such as `data/control-plane.sqlite3`.

## Endpoints

### `POST /webhooks/github`

GitHub should send:

- `X-Hub-Signature-256`
- `X-GitHub-Delivery`
- `X-GitHub-Event: issues`

Only an `issues.labeled` event with the configured `devin-autofix` label and
target repository creates a job. Repeated delivery IDs return the original job
without creating another session.

### `GET /jobs`

Lists jobs newest first. `?include_simulated=false` hides development
simulations. Send `Authorization: Bearer $CONTROL_PLANE_API_KEY`.

### `GET /metrics`

Returns real-job counts for tasks started, active tasks, each terminal status,
completion rate, PR count, and average elapsed seconds to PR. It also reports
the separate simulated-job count. PR count and elapsed time include every
production job with an observed PR, regardless of its eventual terminal status;
completion rate is based only on structured `succeeded` outcomes.

### `POST /simulate`

Available only with `APP_ENV=development`. With no body, it replays
`app/fixtures/issues_labeled.json` through the same signature verification,
filtering, deduplication, persistence, and queueing path:

```bash
curl -X POST http://localhost:8000/simulate \
  -H "Authorization: Bearer $CONTROL_PLANE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"delivery_id":"local-example-1"}'
```

An explicitly supplied payload, including an empty object, is processed as
provided and is never replaced by the fixture.

The resulting job is explicitly marked `simulated`. The worker excludes
simulated jobs, and the response explicitly reports that no external Devin
session or pull request was created.

## Devin session contract

The client uses only the current v3 organization endpoints:

- `POST /v3/organizations/{org_id}/sessions`
- `GET /v3/organizations/{org_id}/sessions?tags=...`
- `GET /v3/organizations/{org_id}/sessions/{devin_id}`
- `GET /v3/organizations/{org_id}/sessions/{devin_id}/messages`
- `DELETE /v3/organizations/{org_id}/sessions/{devin_id}`

The creation request scopes Devin to the target repository and issue, requires
focused tests plus changed-file pre-commit validation, forbids credential
exposure and auto-merge, and requires JSON-schema-validated completion output.
Each request also carries a unique job tag. The worker records request intent
before the external call and reconciles by that tag after an uncertain outcome
instead of issuing a second paid session. Reconciliation paginates candidates,
verifies exact tag membership locally, and rejects ambiguous matches rather
than trusting API result order. Missing Devin credentials are identified as
pre-request failures and fail the job immediately. Legacy attempted jobs use
their last durable update as a fallback request deadline. The timeout is
measured from the original session request, including reconciliation delays.
When it expires, the worker first observes the latest remote state so completion
output and PR metadata are preserved, then terminates only a still-active
session before marking the job timed out. Timeout termination intentionally uses
the v3 default `archive=false`, so the terminated session cannot be resumed.
Message-polling failures do not discard an observed session state. If an overdue
session's status cannot be read, the worker terminates it and records
`needs_human_input` instead of guessing its result. No test calls the real Devin
API.

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
- `/metrics` and `/health` remain unauthenticated; use trusted ingress if
  operational metrics should not be public.
- GitHub status comments and checks are not written in this version.
