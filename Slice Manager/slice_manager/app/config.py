from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRATOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://postgres:postgres@localhost:5432/postgres"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "orchestrator-queue"
    internal_service_token: str | None = None
    approval_timeout_seconds: int = Field(
        default=86_400,
        gt=0,
        validation_alias="APPROVAL_TIMEOUT_SECONDS",
    )
    workflow_recovery_interval_seconds: int = Field(default=30, gt=0)
    capabilities_dir: Path | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
