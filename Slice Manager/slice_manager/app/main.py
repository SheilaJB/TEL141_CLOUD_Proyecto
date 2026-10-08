import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from contracts.errors import ErrorCode, ErrorResponse
from slice_manager.app.api.deployments import router as api_router
from slice_manager.app.api.slices import router as slices_router
from slice_manager.app.config import get_settings
from slice_manager.app.domain.errors import DomainError, InsufficientCapacityError
from slice_manager.app.domain.slice_service import SliceService
from slice_manager.app.internal_api.deployments import router as internal_router
from slice_manager.app.planning.errors import InvalidSpecError
from slice_manager.app.repositories.deployments import DeploymentRepository
from slice_manager.app.repositories.database import create_pool
from slice_manager.app.temporal_client import TemporalWorkflowClient

logger = logging.getLogger(__name__)


async def recover_pending_workflows(application: FastAPI) -> None:
    settings = get_settings()
    while True:
        await asyncio.sleep(settings.workflow_recovery_interval_seconds)
        service = SliceService(
            pool=application.state.database_pool,
            repository=DeploymentRepository(),
            workflow_client=application.state.temporal_workflow_client,
            settings=settings,
        )
        try:
            await service.recover_pending_workflows()
        except (asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            logger.error(
                "Could not scan for pending deployments",
                extra={"error": str(exc)},
            )


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    pool = await create_pool(settings.database_url)
    application.state.database_pool = pool
    application.state.temporal_workflow_client = TemporalWorkflowClient(settings)
    recovery_task = asyncio.create_task(recover_pending_workflows(application))
    try:
        yield
    finally:
        recovery_task.cancel()
        try:
            await recovery_task
        except asyncio.CancelledError:
            pass
        await pool.close()


app = FastAPI(title="Slice Manager", version="0.1.0", lifespan=lifespan)
app.include_router(api_router, prefix="/api/v1")
app.include_router(slices_router, prefix="/api/v1")
app.include_router(internal_router, prefix="/internal")


@app.middleware("http")
async def authenticate_internal_request(
    request: Request,
    call_next: RequestResponseEndpoint,
) -> Response:
    if request.url.path == "/health":
        return await call_next(request)

    settings = get_settings()
    if not settings.internal_service_token:
        return JSONResponse(
            status_code=503,
            content={"detail": "internal service authentication is not configured"},
        )

    supplied_token = request.headers.get("x-internal-token", "").strip()
    if not secrets.compare_digest(supplied_token, settings.internal_service_token):
        return JSONResponse(
            status_code=401,
            content={"detail": "invalid internal service credential"},
        )
    request.state.service_authenticated = True

    if request.url.path.startswith("/api/v1/"):
        user_id = request.headers.get("x-user-id", "").strip()
        role = request.headers.get("x-user-role", "").strip()
        service_level = request.headers.get("x-user-nivel", "").strip()
        if not user_id.isdecimal() or int(user_id) <= 0:
            return JSONResponse(
                status_code=401,
                content={"detail": "valid Gateway user identity is required"},
            )
        if role not in {"consumidor", "operador", "admin"}:
            return JSONResponse(
                status_code=401,
                content={"detail": "valid Gateway user role is required"},
            )
        if service_level not in {"", "basico", "avanzado"}:
            return JSONResponse(
                status_code=401,
                content={"detail": "valid Gateway service level is required"},
            )
        if (role == "consumidor") != bool(service_level):
            return JSONResponse(
                status_code=401,
                content={"detail": "Gateway role and service level are inconsistent"},
            )

        request.state.user_id = int(user_id)
        request.state.user_role = role
        request.state.user_level = service_level or None

    return await call_next(request)


@app.exception_handler(InvalidSpecError)
async def invalid_spec_handler(
    request: Request, error: InvalidSpecError
) -> JSONResponse:
    body = ErrorResponse(
        code=ErrorCode.INVALID_SPEC,
        message=str(error),
        retryable=False,
    )
    return JSONResponse(status_code=422, content=body.model_dump(mode="json"))


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, error: DomainError) -> JSONResponse:
    if isinstance(error, InsufficientCapacityError):
        code = ErrorCode.CAPACITY_EXCEEDED
    else:
        code = {
            403: ErrorCode.CONFLICT,
            404: ErrorCode.RESOURCE_NOT_FOUND,
            409: ErrorCode.CONFLICT,
            503: ErrorCode.BACKEND_UNAVAILABLE,
        }.get(error.status_code, ErrorCode.CONFLICT)
    body = ErrorResponse(
        code=code,
        message=str(error),
        retryable=error.status_code == 503,
    )
    return JSONResponse(
        status_code=error.status_code,
        content=body.model_dump(mode="json"),
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
