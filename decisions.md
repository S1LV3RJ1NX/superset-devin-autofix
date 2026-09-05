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
use a namespaced delivery ID.

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

**Consequence:** Missing success output or PR evidence becomes a failed job.

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
