from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRATOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "orchestrator-queue"
    slice_manager_url: str = "http://localhost:8000"
    network_manager_url: str = "http://networkmanager:8000"
    internal_service_token: str | None = None


def load_settings() -> Settings:
    return Settings()
