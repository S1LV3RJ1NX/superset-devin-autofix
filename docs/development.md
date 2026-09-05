# Development

## Prerequisites

- Python 3.13
- uv 0.7.9 or a compatible newer release
- Docker and Docker Compose for container validation

## Bootstrap

```bash
uv python install 3.13
make install
make hooks
```

`uv sync --all-groups --frozen` creates `.venv` from `uv.lock`. Add or update
dependencies with uv, regenerate the lockfile with `uv lock`, and commit both
`pyproject.toml` and `uv.lock`.

```bash
uv add "package==version"
uv add --dev "package==version"
uv remove package
```

Do not edit dependency arrays or `uv.lock` by hand.

## Runtime configuration

Docker Compose loads `.env` through `env_file`. Local `make run` does not load
that file; export the required variables into the shell before starting the
service. If `.env` is sourced for local development, change `DATABASE_PATH`
from the container path `/data/control-plane.sqlite3` to a writable local path
such as `data/control-plane.sqlite3`.

## Common commands

```bash
make run
make test-unit
make test-integration
make check
make docker-build
make up
make down
```

## Pre-commit policy

The pre-commit stage validates file hygiene, the uv lockfile, Ruff lint and
format, and mypy. The pre-push stage also runs unit and integration tests.

```bash
uv run pre-commit run --all-files
uv run pre-commit run --hook-stage pre-push --all-files
```

Do not skip hooks to work around failures. Fix the source, tests, lockfile, or
configuration that caused the failure.

## Dependency policy

Runtime and development dependencies are pinned. Prefer established releases
and regenerate the lockfile through uv rather than editing it manually.
`tool.uv.exclude-newer` is a fixed publication cutoff. Advance it deliberately
when updating dependencies, after candidate releases have had the intended
supply-chain observation period.
