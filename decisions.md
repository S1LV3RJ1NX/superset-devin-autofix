# Decision log

This log records durable project decisions and their consequences.

## D001: FastAPI for the HTTP control plane

**Decision:** Use FastAPI with typed request/response boundaries.

**Why:** The service needs a small asynchronous API, lifecycle-managed
background work, and straightforward dependency injection for tests.

**Consequence:** HTTP handlers remain thin; business and persistence behavior
must stay outside route functions.

## D002: SQLite for v1 persistence

**Decision:** Store jobs in SQLite with WAL mode, a busy timeout, and a unique
delivery-ID constraint.

**Why:** V1 is a single-service deployment and needs durable idempotency without
external infrastructure.

**Consequence:** Only one worker replica is supported. Multi-replica operation
requires a database-backed lease or external queue.

## D003: Explicit guarded job states

**Decision:** Persist every workflow state and validate transitions in the
repository.

**Why:** State must survive restarts and invalid transitions must fail close to
storage.

**Consequence:** New remote states require an explicit mapping and transition
update rather than implicit string handling.

## D004: GitHub delivery IDs are idempotency keys

**Decision:** Enforce uniqueness on `X-GitHub-Delivery`.

**Why:** GitHub retries deliveries and duplicate work must not create duplicate
Devin sessions.

**Consequence:** A duplicate returns the original job. Development simulations
use a namespaced delivery ID. The header is trusted only after HMAC
authentication; compromise of the webhook secret compromises this boundary.

## D005: Authenticate the raw webhook body before parsing

**Decision:** Verify `X-Hub-Signature-256` with HMAC-SHA256 and constant-time
comparison before accepting payload contents.

**Why:** Parsing or acting on unauthenticated data crosses the GitHub trust
boundary.

**Consequence:** The exact request bytes must be available to the intake
service.

## D006: Use only the Devin v3 Organization Sessions API

**Decision:** Create, poll, and read messages through
`/v3/organizations/{org_id}/sessions`.

**Why:** V3 is the supported organization-scoped API and provides structured
completion output.

**Consequence:** Tests mock this contract and must reject regressions to
deprecated endpoints.

## D007: Require constrained structured Devin output

**Decision:** Send repository scope, issue context, security constraints,
focused validation, no-auto-merge instructions, and a JSON schema with every
session.

**Why:** The worker needs deterministic status and PR extraction while keeping
human review mandatory.

**Consequence:** Missing or unrecognized success output becomes a failed job.
A PR URL never substitutes for structured success.

## D008: Keep simulation on the production intake path

**Decision:** Sign and replay a packaged fixture through the same service path,
while marking the job simulated and excluding it from workers.

**Why:** Development should exercise authentication, filtering, idempotency,
storage, and queueing without external effects.

**Consequence:** Simulation is available only in development and never claims a
real session or PR.

## D009: Use an in-process polling worker for v1

**Decision:** Run a cooperative worker in the FastAPI lifespan.

**Why:** It keeps deployment small while supporting durable restart recovery.

**Consequence:** API and worker scaling are coupled. Retries, distributed
leases, and exponential backoff are deferred.

## D010: Use Python 3.13 and uv

**Decision:** Pin the project to Python 3.13, select it with `.python-version`,
and manage environments and the lockfile with uv.

**Why:** One tool provides fast interpreter selection, deterministic dependency
resolution, isolated command execution, and reproducible container installs.

**Consequence:** Contributors use `uv sync --frozen` and commit `uv.lock`;
direct pip-managed development environments are unsupported. Resolution uses
an `exclude-newer` cutoff so releases have at least a seven-day observation
window before entering the lockfile.

## D011: Enforce layered tests with pre-commit

**Decision:** Separate isolated unit tests from SQLite/FastAPI/worker integration
tests. Run lint, formatting, typing, and lock validation at pre-commit; add both
test layers at pre-push.

**Why:** Fast feedback belongs close to edits, while cross-boundary regressions
must block pushes.

**Consequence:** Behavior changes follow the Red-Green-Refactor workflow in
`docs/testing.md`; no test contacts the real Devin API.

## D012: Store secrets only in the environment

**Decision:** Read credentials from environment variables and preserve
`.env.example` as names-only documentation.

**Why:** Credentials must not enter source, images, logs, fixtures, or tests.

**Consequence:** Production secret provisioning is an operator responsibility,
and missing credentials fail with safe messages.

## D013: Reconcile session creation instead of retrying it

**Decision:** Persist one session-creation attempt and its timestamp before the
external call, attach a unique job tag to the request, and search Devin by that
tag after an uncertain outcome. Paginate results, verify exact tag membership
locally, and reject multiple exact matches instead of relying on API filtering
or ordering. Never automatically issue a second create request for the same
job.

**Why:** The v3 create endpoint does not expose a documented idempotency key.
A crash after remote creation but before local persistence would otherwise
create duplicate paid sessions.

**Consequence:** The service user also needs `ViewOrgSessions`. If a crash
occurs before Devin accepts the request and no tagged session appears, the job
requires human review after the configured timeout rather than risking
duplicate spend. Ambiguous exact matches remain unreconciled and surface an
operator-visible error.

## D014: Keep PR timing independent from remediation success

**Decision:** `tasks_with_pr` and `average_elapsed_seconds_to_pr` include every
production job with an observed PR. `completion_rate` counts only jobs whose
validated structured output reports `succeeded`.

**Why:** Time to first PR and successful issue remediation are different
operational signals. A later failure does not erase the fact that a PR was
created, while PR presence alone must not inflate fix rate.

**Consequence:** Consumers must use terminal status counts or completion rate
when they need successful-remediation metrics.

## D015: Authenticate operator endpoints with a bearer token

**Decision:** Require `Authorization: Bearer $CONTROL_PLANE_API_KEY` for
`GET /jobs` and development-only `POST /simulate`. Fail closed when the key is
not configured and compare credentials in constant time.

**Why:** Job records contain issue and Devin execution details, while simulation
can create unbounded durable records. Both capabilities belong to operators,
not anonymous network clients.

**Consequence:** Deployments must provision a separate control-plane key.
`/health`, `/metrics`, and the independently HMAC-authenticated GitHub webhook
remain outside this bearer-token boundary.

## D016: Keep worker failures and remote timeouts observable

**Decision:** Catch unexpected failures at both worker-cycle and per-job
boundaries. On application shutdown, cancel the worker task before closing the
HTTP client. When an active job exceeds its timeout, terminate the Devin
session through the v3 API before transitioning the local job to `timed_out`,
but only after reading its latest state.

**Why:** An uncaught repository or transition error must not permanently stop
all orchestration. Shutdown should not wait for in-flight HTTP timeouts.
Stopping local polling without stopping remote work would allow unobserved
spend and pull requests. Checking current state first prevents sessions that
completed during a polling gap from becoming false timeouts.

**Consequence:** Polling and failed termination attempts leave the job active
with an error and are retried on later worker cycles. The timeout remains
anchored to the original session request across reconciliation delays, and an
unknown creation outcome becomes `needs_human_input` when that deadline expires.
Session status is applied independently of message retrieval. An overdue
session with unreadable status is terminated and marked `needs_human_input`
rather than reported as a known timeout or left running without observation.
Per-job failures are logged and do not block unrelated jobs, while broad
repository-read failures defer the cycle without killing the long-running
worker.

## D017: Separate known pre-request failures from uncertain creation

**Decision:** Raise a distinct configuration error before Devin HTTP requests
and fail that job immediately. Continue reconciling transport, response, and
persistence failures that could have created a paid session. Backfill missing
request timestamps for attempted legacy jobs from their last durable update.

**Why:** Missing credentials prove no external side effect occurred, while
other failures may hide a live session. Every ambiguous attempt still needs a
finite reconciliation deadline.

**Consequence:** Configuration errors are terminal without unnecessary human
review. Legacy attempted jobs retain duplicate-session protection and
eventually escalate if no tagged session can be reconciled.
