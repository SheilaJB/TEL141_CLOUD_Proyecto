import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession

from app.auth.router import router as auth_router
from app.auth.service import AuthService
from app.config import Settings, get_settings
from app.gateway.proxy import router as proxy_router
from app.gateway.routes import is_gateway_path, policy_for
from app.shared.database import create_database
from app.shared.denylist import Denylist
from app.shared.jwt_tokens import decode_access_token

logger = logging.getLogger(__name__)


def _required_file(path: Path, setting_name: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"{setting_name} cannot be read: {path}") from exc


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings: Settings = getattr(application.state, "settings", None) or get_settings()
    application.state.settings = settings
    private_key = _required_file(settings.jwt_private_key_path, "JWT_PRIVATE_KEY_PATH")
    public_key = _required_file(settings.jwt_public_key_path, "JWT_PUBLIC_KEY_PATH")
    _required_file(settings.ca_cert_path, "CA_CERT_PATH")
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    engine, sessions = create_database(settings.database_url)
    application.state.engine = engine
    application.state.session_factory = sessions
    application.state.denylist = Denylist(settings.access_ttl_min * 60)
    application.state.auth_service = AuthService(
        session_factory=sessions,
        private_key=private_key,
        settings=settings,
        denylist=application.state.denylist,
    )
    application.state.public_key = public_key
    application.state.http_client = httpx.AsyncClient(
        verify=str(settings.ca_cert_path),
        timeout=settings.http_timeout_seconds,
        follow_redirects=False,
    )
    try:
        yield
    finally:
        await application.state.http_client.aclose()
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(
        title="TEL141 Edge",
        version="0.1.0",
        lifespan=lifespan,
    )
    if settings is not None:
        application.state.settings = settings

    application.include_router(auth_router)
    application.include_router(proxy_router)

    @application.middleware("http")
    async def edge_middleware(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = str(uuid4())
        request.state.request_id = request_id
        configured: Settings | None = getattr(request.app.state, "settings", None)
        origin = request.headers.get("origin")

        if (
            request.method == "OPTIONS"
            and origin
            and configured is not None
            and origin in configured.cors_origins
        ):
            response = Response(status_code=204)
        else:
            policy = policy_for(request.url.path, request.method)
            if is_gateway_path(request.url.path) and policy is None:
                response = JSONResponse(
                    status_code=404,
                    content={"detail": "gateway route not found"},
                )
            elif policy is not None and not policy.public:
                authorization = request.headers.get("authorization", "")
                scheme, separator, token = authorization.partition(" ")
                if not separator or scheme.lower() != "bearer" or not token.strip():
                    response = JSONResponse(
                        status_code=401,
                        content={"detail": "authentication is required"},
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                else:
                    try:
                        claims = decode_access_token(
                            token.strip(),
                            request.app.state.public_key,
                        )
                    except jwt.InvalidTokenError:
                        response = JSONResponse(
                            status_code=401,
                            content={"detail": "invalid access token"},
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    else:
                        denylist: Denylist = request.app.state.denylist
                        if denylist.is_revoked(claims.sub, claims.iat):
                            response = JSONResponse(
                                status_code=401,
                                content={"detail": "access token has been revoked"},
                                headers={"WWW-Authenticate": "Bearer"},
                            )
                        elif policy.roles and claims.rol not in policy.roles:
                            response = JSONResponse(
                                status_code=403,
                                content={"detail": "role is not allowed for this route"},
                            )
                        else:
                            request.state.claims = claims
                            response = await call_next(request)
            else:
                response = await call_next(request)

        if origin and configured is not None and origin in configured.cors_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = (
                "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            )
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type"
            )
            response.headers["Access-Control-Expose-Headers"] = "X-Request-Id"
            response.headers.append("Vary", "Origin")
        response.headers["X-Request-Id"] = request_id
        return response

    @application.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
