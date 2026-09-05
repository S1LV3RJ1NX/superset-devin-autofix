"""Application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str) -> bool:
    """Parse a conventional environment boolean."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    app_env: str = "production"
    database_path: Path = Path("data/control-plane.sqlite3")
    github_webhook_secret: str = ""
    devin_api_key: str = ""
    devin_org_id: str = ""
    devin_api_base_url: str = "https://api.devin.ai"
    target_repository: str = "S1LV3RJ1NX/superset"
    autofix_label: str = "devin-autofix"
    poll_interval_seconds: float = 10.0
    session_timeout_seconds: int = 3600
    worker_enabled: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from the process environment."""
        return cls(
            app_env=os.getenv("APP_ENV", "production"),
            database_path=Path(os.getenv("DATABASE_PATH", "data/control-plane.sqlite3")),
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
            devin_api_key=os.getenv("DEVIN_API_KEY", ""),
            devin_org_id=os.getenv("DEVIN_ORG_ID", ""),
            devin_api_base_url=os.getenv("DEVIN_API_BASE_URL", "https://api.devin.ai").rstrip("/"),
            target_repository=os.getenv("TARGET_REPOSITORY", "S1LV3RJ1NX/superset"),
            autofix_label=os.getenv("AUTOFIX_LABEL", "devin-autofix"),
            poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "10")),
            session_timeout_seconds=int(os.getenv("SESSION_TIMEOUT_SECONDS", "3600")),
            worker_enabled=_as_bool(os.getenv("WORKER_ENABLED", "true")),
        )

    @property
    def simulation_enabled(self) -> bool:
        """Return whether development-only simulation is available."""
        return self.app_env.lower() == "development"
