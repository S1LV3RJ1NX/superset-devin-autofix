# Control-plane API and Devin session contract

This reference defines the boundaries between GitHub, the control plane,
operators, and Devin. The service creates and observes work; a human still
reviews and merges every pull request.

## Control-plane endpoints

### `POST /webhooks/github`

GitHub sends:

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

### `GET /dashboard`

Returns a sanitized, server-rendered operator view of the same durable metrics
and the latest non-simulated workflow. The page refreshes every 10 seconds,
shows empty and database-error states, and exposes selected presentation fields
instead of the raw `/jobs` response. It contains no browser JavaScript and does
not call the bearer-protected `/jobs` endpoint.

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
