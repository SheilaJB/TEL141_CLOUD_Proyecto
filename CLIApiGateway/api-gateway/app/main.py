"""
main.py — Punto de Entrada del API Gateway (100% Stateless)
Cumple con la Rúbrica R1, R1B, R1C y el contrato AUTHENTICATION.rst:
- Validación criptográfica RS256 mediante Clave Pública (0 DB, 0 memoria compartida).
- Control de Acceso Perimetral (RBAC) por ruta y método HTTP.
- Sanitización e inyección de X-Internal-Token y X-User-*.
- Proxy híbrido con soporte nativo para Streaming de archivos masivos (ISOs/QCOW2).
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import jwt
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import (
    load_public_key, get_route_policy, UPSTREAMS,
    MAX_BODY_SIZE, RoutePolicy
)
from app.proxy import (
    sanitize_and_prepare_headers, forward_request, stream_request
)

ISSUER = "tel141-auth"
AUDIENCE = "tel141-edge"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 1. Cargar clave pública en memoria del proceso
    app.state.public_key = load_public_key()
    # 2. Cliente HTTP compartido con connection pooling
    app.state.http_client = httpx.AsyncClient(timeout=30.0)
    yield
    await app.state.http_client.aclose()


app = FastAPI(
    title="TEL141 API Gateway - Grupo 3",
    description="Gateway de Borde perimetral para Orquestador Cloud (Stateless, RS256, Streaming)",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"]
)


def _resolve_target_url(path: str, query: str = "") -> str:
    """
    Resuelve la URL destino en la red Docker interna según el prefijo de servicio.
    """
    clean_path = "/" + path.lstrip("/")

    # 1. Rutas de autenticación
    if clean_path.startswith("/auth") or clean_path in {"/login", "/refresh", "/logout", "/verify"}:
        base = UPSTREAMS["auth"].rstrip("/")
        # Normalizar si viene como /login a /auth/login
        if clean_path in {"/login", "/refresh", "/logout", "/verify"}:
            clean_path = f"/auth{clean_path}"
        target = f"{base}{clean_path}"

    # 2. Rutas de Slices (Slice Manager) según contrato SLICE_API.rst
    elif clean_path == "/slices" or clean_path.startswith("/slices/"):
        base = UPSTREAMS["slices"].rstrip("/")
        suffix = clean_path.removeprefix("/slices")
        if suffix.startswith("/deployments") or suffix.startswith("/aprobaciones"):
            target = f"{base}/api/v1{suffix}"
        else:
            target = f"{base}/api/v1/slices{suffix}"

    # 3. CRUDs / Query Service
    elif clean_path == "/cruds" or clean_path.startswith("/cruds/"):
        base = UPSTREAMS["cruds"].rstrip("/")
        suffix = clean_path.removeprefix("/cruds")
        target = f"{base}/cruds{suffix}"

    # 4. Image Manager
    elif clean_path.startswith("/image"):
        base = UPSTREAMS["image"].rstrip("/")
        target = f"{base}{clean_path}"

    # 5. Network Manager
    elif clean_path.startswith("/network"):
        base = UPSTREAMS["network"].rstrip("/")
        target = f"{base}{clean_path}"

    else:
        # Fallback a servicio auth o slice
        base = UPSTREAMS["slices"].rstrip("/")
        target = f"{base}{clean_path}"

    if query:
        target = f"{target}?{query}"
    return target


@app.get("/health", tags=["Monitoreo"])
@app.get("/healthz", tags=["Monitoreo"], include_in_schema=False)
async def health():
    return {"status": "ok", "service": "api-gateway"}


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    include_in_schema=False
)
async def gateway_dispatcher(path: str, request: Request):
    """
    Dispatcher principal del API Gateway:
    1. Genera X-Request-Id para auditoría.
    2. Aplica políticas de ruta (Pública vs Protegida).
    3. Valida JWT RS256 con Clave Pública en modo 100% Stateless.
    4. Aplica RBAC perimetral (403 si el rol no tiene permisos).
    5. Sanitiza cabeceras cliente e inyecta X-Internal-Token y X-User-*.
    6. Reenvía mediante streaming o memoria al microservicio correspondiente.
    """
    request_id = str(uuid4())
    norm_path = "/" + path.lstrip("/")

    # Permitir peticiones preflight CORS directamente
    if request.method == "OPTIONS":
        return Response(status_code=204)

    policy = get_route_policy(norm_path, request.method)
    claims = None

    # Verificación de Seguridad y RBAC para rutas protegidas
    if policy and not policy.public:
        auth_header = request.headers.get("authorization", "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return JSONResponse(
                status_code=401,
                content={"detail": "Se requiere autenticación Bearer."},
                headers={"WWW-Authenticate": "Bearer", "X-Request-Id": request_id}
            )

        public_key = getattr(request.app.state, "public_key", None) or load_public_key()
        if not public_key:
            return JSONResponse(
                status_code=500,
                content={"detail": "Error de configuración: Clave pública no disponible en Gateway."},
                headers={"X-Request-Id": request_id}
            )

        try:
            claims = jwt.decode(
                token.strip(),
                public_key,
                algorithms=["RS256"],
                issuer=ISSUER,
                audience=AUDIENCE,
                options={"require": ["exp", "iat", "sub", "cod", "rol"]}
            )
        except jwt.ExpiredSignatureError:
            return JSONResponse(
                status_code=401,
                content={"detail": "El token de acceso ha expirado."},
                headers={"WWW-Authenticate": "Bearer", "X-Request-Id": request_id}
            )
        except jwt.InvalidTokenError as err:
            return JSONResponse(
                status_code=401,
                content={"detail": f"Token inválido: {str(err)}"},
                headers={"WWW-Authenticate": "Bearer", "X-Request-Id": request_id}
            )

        # Validación de Roles (RBAC perimetral)
        user_role = claims.get("rol")
        if policy.roles and user_role not in policy.roles:
            return JSONResponse(
                status_code=403,
                content={"detail": f"Acceso denegado: el rol '{user_role}' no está autorizado para esta acción."},
                headers={"X-Request-Id": request_id}
            )

    # Resolución de URL y preparación de cabeceras seguras
    target_url = _resolve_target_url(norm_path, request.url.query)
    forward_headers = sanitize_and_prepare_headers(request, claims=claims, request_id=request_id)

    # Identificar si es subida masiva (Streaming) o payload JSON normal
    content_type = request.headers.get("content-type", "")
    is_streamed = content_type.startswith("multipart/form-data")

    if is_streamed:
        resp = await stream_request(request.method, target_url, forward_headers, request)
    else:
        body = await request.body()
        if len(body) > MAX_BODY_SIZE:
            return JSONResponse(
                status_code=413,
                content={"detail": "Carga útil excede 2MB. Use carga en streaming (multipart)."},
                headers={"X-Request-Id": request_id}
            )
        client: httpx.AsyncClient = request.app.state.http_client
        resp = await forward_request(client, request.method, target_url, forward_headers, body)

    resp.headers["X-Request-Id"] = request_id
    return resp
