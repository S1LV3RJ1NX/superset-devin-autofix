# Setup and local run

Use this guide to configure and run the control plane locally. For the full
issue-label-to-Devin workflow, see [Live GitHub-to-Devin demonstration](live-demo.md).

## Prerequisites

- Docker and Docker Compose for the recommended local run.
- Python 3.13 and [uv](https://docs.astral.sh/uv/) only for local development.
- A Devin v3 organization service user when running real remediation work.

## Configure local environment

Copy the committed template. `.env` is intentionally ignored by Git.

```bash
cp .env.example .env
```

Set these values in the local `.env` file:

| Setting | Purpose |
| --- | --- |
| `GITHUB_WEBHOOK_SECRET` | Verifies GitHub webhook HMAC signatures. |
| `CONTROL_PLANE_API_KEY` | Protects the `/jobs` and development `/simulate` endpoints. |
| `DEVIN_API_KEY` | A v3 service-user credential with the `cog_` prefix. |
| `DEVIN_ORG_ID` | The target organization ID with the `org-` prefix. |

### Generate local secrets

Generate a different high-entropy value for each of these local secrets:

- `GITHUB_WEBHOOK_SECRET`
- `CONTROL_PLANE_API_KEY`

Run this command once for each value, then paste the output into the matching
line in `.env`:

```bash
openssl rand -hex 32
```

Do not generate `DEVIN_API_KEY` or `DEVIN_ORG_ID`. Create the former as a
service-user credential in Devin and copy the latter from the Devin
organization settings.

The Devin service user needs organization-level `ManageOrgSessions` and
`ViewOrgSessions` permissions. The first creates sessions; the second lets the
worker reconcile uncertain requests and poll their status without creating a
duplicate paid session.

The defaults in `.env.example` target `S1LV3RJ1NX/superset` and the
`devin-autofix` label. Version 1 does not require or read a `GITHUB_TOKEN`:
GitHub signs inbound webhooks with `GITHUB_WEBHOOK_SECRET`, and the control
plane makes no outbound GitHub API calls. A future version that writes issue
comments, commit statuses, or checks should introduce a minimally scoped token
at that time.

Never commit `.env`, put credentials in a screenshot, or include them in a
Loom recording.

## Run with Docker Compose

From the repository root:

```bash
docker compose up --build
```

In another terminal, confirm the service is healthy:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

Docker Compose reads `.env` automatically and stores SQLite state in the named
`control-plane-data` volume. Starting the service does not create a Devin
session; a qualifying, signed GitHub label event is required.

To stop the local service while preserving the demonstration history:

```bash
docker compose down
```

Use `docker compose down -v` only when you intentionally want to delete that
local SQLite volume.

## Run from a local Python environment

The committed `.python-version` selects Python 3.13 and `uv.lock` fixes the
dependency graph:

```bash
make install
make hooks
make run
```

Unlike Docker Compose, `make run` does not read `.env` automatically. Export
the required variables in your shell first, and use a locally writable
`DATABASE_PATH`, such as `data/control-plane.sqlite3`, rather than the
container path `/data/control-plane.sqlite3`.

For contributor commands and the pre-commit policy, see
[Development](development.md). For deployed-operation and recovery guidance,
see [Operations](operations.md).
