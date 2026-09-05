# Module guide

## `app.config`

Defines the immutable `Settings` model and environment parsing. It centralizes
runtime defaults, secret names, worker timing, target repository, and the
development-only simulation gate.

## `app.models`

Defines `JobStatus`, active and terminal state sets, legal transitions, UTC
timestamp helpers, and the immutable `Job` representation returned by the
repository and API.

## `app.database`

Owns the SQLite schema and all persistence. `JobRepository` initializes the
database, enforces delivery-ID uniqueness and legal transitions, stores runtime
session fields, atomically inserts queued webhook jobs, migrates compatible
schema additions, lists worker candidates, and calculates metrics.

## `app.github`

Owns GitHub webhook boundary logic. It signs and verifies raw bodies with
HMAC-SHA256 and parses only matching `issues.labeled` events for the configured
label and repository.

## `app.service`

Coordinates webhook intake. `JobService` authenticates first, requires a
delivery ID, filters the event, inserts or retrieves the durable job, and
atomically queues newly created work. Simulation uses this same path with a
separate delivery-ID namespace.

## `app.devin`

Contains typed Devin v3 session/message models, prompt construction, the
structured completion schema, and the asynchronous Organization Sessions API
client. The client supports session creation, status polling, cursor-paginated
messages, exact and unambiguous tracking-tag reconciliation, session
termination, and safe error translation.

## `app.worker`

Advances real jobs through session creation and polling. It persists Devin
session request intent before the external call, reconciles uncertain outcomes
without duplicate creates, recovers received jobs, persists session metadata,
messages, structured output, and PR URLs, maps remote states to terminal job
states, isolates cycle and per-job failures, preserves completions observed
after polling gaps, and keeps transient polling failures retryable. Known
pre-request configuration failures fail immediately, while legacy creation
attempts use their durable update time as a fallback reconciliation deadline.
Still-active remote work is terminated before recording a timeout. Session
status remains actionable when optional message retrieval fails; overdue
sessions with unreadable status are terminated and escalated for human review.

## `app.main`

Builds the FastAPI application and lifecycle. It wires settings, repository,
service, client, and worker; exposes health, webhook, jobs, metrics, and
simulation routes; enforces operator bearer authentication for job data and
simulation; cancels active worker requests during shutdown; and closes
background resources.

## `app.fixtures`

Contains packaged development simulation payloads. Fixtures must remain safe,
deterministic, and free of credentials.
