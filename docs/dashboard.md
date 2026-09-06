# Operator dashboard

`GET /dashboard` is a lightweight presentation view for operators and
engineering leaders. It reads live durable SQLite state; it does not use mock
data or call another control-plane endpoint.

> **Demo-only operator console — not suitable for public deployment.**
> Expandable details expose raw workflow traces from persisted Devin updates,
> completion summaries, validation, limitations, and operator notes.

## Data shown

Summary cards use the existing repository metric calculation:

- tasks started;
- active tasks;
- pull requests opened;
- completion rate;
- average time to pull request;
- simulated tasks;
- terminal outcome counts.

The latest-workflow section selects the newest non-simulated job and shows its
issue number and title, readable state, pull request link when valid, created
and completed times, and time to pull request. Selected workflow details can be
expanded without exposing the full job response. A job in
`needs_human_input` with a valid pull request displays:

> PR opened — human review required

Metrics and the latest workflow are derived from one immutable repository
snapshot so they describe the same database state. Simulations contribute only
to the simulated-task count and never replace the latest production workflow.

## Rendering and refresh

FastAPI renders complete HTML on the server. A 10-second HTML refresh reloads
current durable data without browser JavaScript, a frontend build, or browser
access to the bearer-protected `/jobs` endpoint.

The page provides:

- an empty state when no production workflow exists;
- a retryable `503` state when data loading or rendering fails;
- `Cache-Control: no-store` and restrictive browser security headers.

Dynamic text is HTML-escaped. Links are rendered only for valid HTTP or HTTPS
URLs; malformed persisted URLs are omitted instead of failing the page.
Issue bodies, delivery IDs, internal job IDs, arbitrary structured output, and
credentials are not rendered. Escaping protects HTML rendering but does not
redact sensitive content from the selected workflow traces.

## Access boundary

The dashboard intentionally follows the existing unauthenticated observability
boundary used by `/metrics` and `/health`; it does not add authentication or
authorization. Keep `/dashboard` behind trusted ingress for controlled demos.

Production readiness requires, at minimum, authenticated access with RBAC,
field-level redaction and data classification for workflow traces, and reviewed
retention and audit controls.
