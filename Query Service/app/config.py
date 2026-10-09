import os
from pydantic import BaseModel


class Settings(BaseModel):
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://cloud_admin:cloud_pass@postgres:5432/cloud_g3",
    )
    internal_service_token: str = os.getenv(
        "INTERNAL_SERVICE_TOKEN",
        os.getenv(
            "ORCHESTRATOR_INTERNAL_SERVICE_TOKEN",
            "4f10228bcac31e22155deea948bf9b7245e5758dc5aa9c08bb7cae8005d9e937",
        ),
    )
    db_pool_min_size: int = int(os.getenv("DB_POOL_MIN_SIZE", "2"))
    db_pool_max_size: int = int(os.getenv("DB_POOL_MAX_SIZE", "10"))


settings = Settings()
