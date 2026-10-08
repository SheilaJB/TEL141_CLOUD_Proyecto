from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from slice_manager.app.config import Settings, get_settings
from slice_manager.app.domain.slice_lifecycle import SliceLifecycleService
from slice_manager.app.domain.slice_service import SliceService
from slice_manager.app.repositories.deployments import DeploymentRepository
from slice_manager.app.repositories.slices import SliceRepository
from slice_manager.app.temporal_client import TemporalWorkflowClient


def get_pool(request: Request) -> asyncpg.Pool:
    pool: asyncpg.Pool | None = getattr(request.app.state, "database_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database pool is unavailable",
        )
    return pool


def get_current_user_id(request: Request) -> int:
    user_id = getattr(request.state, "user_id", None)
    if not isinstance(user_id, int) or user_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="an authenticated user principal is required",
        )
    return user_id


def require_internal_service(request: Request) -> None:
    if (
        not request.url.path.startswith("/internal/")
        or not getattr(request.state, "service_authenticated", False)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid internal service credential",
        )


def get_slice_service(
    request: Request,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SliceService:
    workflow_client: TemporalWorkflowClient | None = getattr(
        request.app.state, "temporal_workflow_client", None
    )
    if workflow_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Temporal client is unavailable",
        )
    return SliceService(
        pool=pool,
        repository=DeploymentRepository(),
        workflow_client=workflow_client,
        settings=settings,
    )


def get_slice_lifecycle_service(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SliceLifecycleService:
    return SliceLifecycleService(
        pool=pool,
        repository=SliceRepository(),
        deployment_repository=DeploymentRepository(),
        capabilities_dir=settings.capabilities_dir,
    )
