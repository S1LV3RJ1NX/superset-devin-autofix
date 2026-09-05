"""FastAPI application for the remediation control plane."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings
from app.database import JobRepository
from app.devin import DevinClient
from app.github import WebhookAuthenticationError, sign_payload
from app.service import IngestResult, JobService
from app.worker import JobWorker

logger = logging.getLogger(__name__)
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "issues_labeled.json"


class SimulationRequest(BaseModel):
    """Optional simulation payload overrides."""

    delivery_id: str = Field(default_factory=lambda: str(uuid4()))
    payload: dict[str, object] | None = None


def _result_payload(result: IngestResult) -> dict[str, object]:
    if result.job is None:
        return {
            "accepted": False,
            "ignored": True,
            "reason": result.ignored_reason,
        }
    return {
        "accepted": True,
        "ignored": False,
        "duplicate": not result.created,
        "job": result.job.as_dict(),
    }


def _require_operator_auth(authorization: str | None, api_key: str) -> None:
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="control plane API authentication is not configured",
        )
    scheme, separator, credentials = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "bearer" or not hmac.compare_digest(credentials, api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid operator credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def create_app(
    *,
    settings: Settings | None = None,
    repository: JobRepository | None = None,
    devin_client: DevinClient | None = None,
    start_worker: bool | None = None,
) -> FastAPI:
    """Create an application with injectable infrastructure for tests."""
    resolved_settings = settings or Settings.from_env()
    resolved_repository = repository or JobRepository(resolved_settings.database_path)
    resolved_devin_client = devin_client or DevinClient(resolved_settings)
    service = JobService(resolved_settings, resolved_repository)
    worker = JobWorker(resolved_settings, resolved_repository, resolved_devin_client)
    worker_enabled = resolved_settings.worker_enabled if start_worker is None else start_worker

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        resolved_repository.initialize()
        worker_task: asyncio.Task[None] | None = None
        if worker_enabled:
            worker_task = asyncio.create_task(worker.run())
        try:
            yield
        finally:
            worker.stop()
            if worker_task is not None:
                await worker_task
            await resolved_devin_client.close()

    app = FastAPI(
        title="Superset Devin Autofix Control Plane",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/github")
    async def github_webhook(
        request: Request,
        x_hub_signature_256: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
        x_github_event: str | None = Header(default=None),
    ) -> dict[str, object]:
        body = await request.body()
        try:
            result = service.ingest(
                body=body,
                signature=x_hub_signature_256,
                delivery_id=x_github_delivery,
                github_event=x_github_event,
            )
        except WebhookAuthenticationError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        response = _result_payload(result)
        logger.info(
            "processed GitHub delivery_id=%s accepted=%s duplicate=%s",
            x_github_delivery,
            response["accepted"],
            response.get("duplicate", False),
        )
        return response

    @app.get("/jobs")
    async def list_jobs(
        include_simulated: bool = True,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        _require_operator_auth(authorization, resolved_settings.control_plane_api_key)
        jobs = resolved_repository.list_jobs(include_simulated=include_simulated)
        return {"items": [job.as_dict() for job in jobs], "total": len(jobs)}

    @app.get("/metrics")
    async def metrics() -> dict[str, object]:
        return resolved_repository.metrics()

    @app.post("/simulate")
    async def simulate(
        simulation: SimulationRequest | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        if not resolved_settings.simulation_enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="simulation is available only when APP_ENV=development",
            )
        _require_operator_auth(authorization, resolved_settings.control_plane_api_key)
        request_data = simulation or SimulationRequest()
        payload = request_data.payload if request_data.payload is not None else _load_fixture()
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        try:
            result = service.ingest(
                body=body,
                signature=sign_payload(body, resolved_settings.github_webhook_secret),
                delivery_id=request_data.delivery_id,
                github_event="issues",
                simulated=True,
            )
        except WebhookAuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        response = _result_payload(result)
        response["simulation"] = True
        response["external_session_created"] = False
        response["pr_created"] = False
        return response

    return app


def _load_fixture() -> dict[str, object]:
    payload = json.loads(FIXTURE_PATH.read_text())
    if not isinstance(payload, dict):
        raise ValueError("simulation fixture must contain a JSON object")
    return {str(key): value for key, value in payload.items()}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
app = create_app()
