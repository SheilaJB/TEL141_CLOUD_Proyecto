from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    jwt_private_key_path: Path
    jwt_public_key_path: Path
    tls_cert_path: Path
    tls_key_path: Path
    ca_cert_path: Path
    internal_service_token: SecretStr
    upstream_cruds_url: str
    upstream_slice_manager_url: str
    access_ttl_min: int = Field(default=15, gt=0)
    refresh_ttl_days: int = Field(default=7, gt=0)
    cookie_secure: bool = True
    cors_origins: list[str] = Field(default_factory=list)
    http_timeout_seconds: float = Field(default=15, gt=0)
    login_attempts_per_minute: int = Field(default=5, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
