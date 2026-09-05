# Testing strategy

## Development method

Behavior changes follow Red-Green-Refactor:

1. Add or adjust the smallest test that expresses the required behavior.
2. Run the narrow unit or integration target and observe the failure.
3. Implement the minimum production change.
4. Re-run the narrow target until green.
5. Refactor without changing behavior.
6. Run the complete unit, integration, lint, format, and type-check gates.

The initial v1 predates this documented workflow, so the project does not make
a retroactive claim that every original line was test-first. The Python
3.13/uv hardening added the cross-boundary integration scenario before changing
runtime packaging, and this workflow governs subsequent behavior changes.

## Unit tests

`tests/unit` covers isolated behavior:

- GitHub HMAC signing, verification, and event filtering.
- Devin request construction, authentication headers, response validation,
  pagination, and errors through `httpx.MockTransport`.

Run with:

```bash
make test-unit
```

## Integration tests

`tests/integration` covers collaborating components:

- FastAPI routes with temporary SQLite persistence.
- Operator bearer authentication, delivery idempotency, and exact simulation
  payload behavior.
- SQLite state transitions and aggregate metrics.
- Atomic enqueue, received-job recovery, creation-outcome reconciliation, and
  duplicate-session prevention with deterministic Devin boundary fakes.
- Immediate failure for missing pre-request Devin configuration and finite
  reconciliation deadlines for migrated creation attempts.
- Paginated exact-tag reconciliation and rejection of ambiguous matches.
- Strict structured success mapping and active-session timeouts.
- Worker-cycle/per-job failure isolation, remote timeout termination, and
  cancellation of active polling during shutdown.
- Preservation of remote completion output and PR metadata after an overdue
  polling gap.
- Retryable polling failures and enforcement of the original request deadline
  across delayed or failing reconciliation.
- Terminal-state and timeout handling when message polling fails, plus safe
  termination of overdue sessions whose status cannot be observed.
- The complete webhook-to-worker-to-jobs/metrics path.

Run with:

```bash
make test-integration
```

## External isolation

No automated test calls GitHub or Devin. Tests use temporary files,
`httpx.MockTransport`, and typed deterministic fakes. A real end-to-end test
requires explicit credentials and is outside the default test suite.

## Quality gate

```bash
make check
```

This executes the pre-push hook stage across the repository, including unit and
integration tests.
