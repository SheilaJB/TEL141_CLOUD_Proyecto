"""Environment-backed Compute Manager configuration."""

import os


DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "5"))
INTERNAL_SERVICE_TOKEN = os.environ.get("INTERNAL_SERVICE_TOKEN") or os.environ.get(
    "ORCHESTRATOR_INTERNAL_SERVICE_TOKEN"
)
TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "default")
