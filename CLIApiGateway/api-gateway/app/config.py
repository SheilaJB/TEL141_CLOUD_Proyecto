"""
config.py — Configuración y Políticas de Rutas del API Gateway
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set

# Clave pública para verificación de JWT RS256
def load_public_key() -> str:
    path_str = os.getenv("JWT_PUBLIC_KEY_PATH")
    search_paths = []
    if path_str:
        search_paths.append(Path(path_str))
    search_paths.append(Path("keys/jwt-public.pem"))
    search_paths.append(Path("/run/edge-keys/jwt-public.pem"))
    search_paths.append(Path("../keys/jwt-public.pem"))
    search_paths.append(Path("../Auth - API GW/keys/jwt-public.pem"))
    search_paths.append(Path("C:/Users/Luis/Documents/Proy CLOUD Auth - API GW/keys/jwt-public.pem"))

    for p in search_paths:
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8")
    return ""


INTERNAL_SERVICE_TOKEN = os.getenv(
    "ORCHESTRATOR_INTERNAL_SERVICE_TOKEN",
    os.getenv("INTERNAL_SERVICE_TOKEN", "4f10228bcac31e22155deea948bf9b7245e5758dc5aa9c08bb7cae8005d9e937")
)

# Mapeo de URLs base de microservicios internos
UPSTREAMS = {
    "auth": os.getenv("AUTH_URL", "http://auth:8000"),
    "slices": os.getenv("ORCHESTRATOR_SLICE_MANAGER_URL", os.getenv("SLICE_MANAGER_URL", "http://slice-manager:8000")),
    "cruds": os.getenv("CRUDS_URL", "http://crud-service:8001"),
    "image": os.getenv("IMAGE_MANAGER_URL", "http://computemanager:8000"),
    "vms": os.getenv("VM_MANAGER_URL", "http://computemanager:8000"),
    "network": os.getenv("NETWORK_MANAGER_URL", "http://networkmanager:8000"),
}

MAX_BODY_SIZE = 2 * 1024 * 1024  # 2MB límite para buffer en RAM; mayor a esto o multipart usa streaming directo


# -----------------------------------------------------------------------------
# Matriz de Políticas RBAC (Control de Acceso Perimetral)
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class RoutePolicy:
    public: bool
    roles: frozenset[str] = frozenset()


PUBLIC = RoutePolicy(public=True)
ALL_ROLES = frozenset({"consumidor", "operador", "admin"})
AUTHENTICATED = RoutePolicy(public=False, roles=ALL_ROLES)
CONSUMERS_AND_OPERATORS = RoutePolicy(public=False, roles=ALL_ROLES)
OPERATORS = RoutePolicy(public=False, roles=frozenset({"operador"}))
OPERATORS_AND_ADMINS = RoutePolicy(public=False, roles=frozenset({"operador", "admin"}))
ADMIN = RoutePolicy(public=False, roles=frozenset({"admin"}))


def get_route_policy(path: str, method: str) -> Optional[RoutePolicy]:
    # Rutas públicas (login, refresh, logout, monitoreo)
    if path in {
        "/auth/login", "/auth/refresh", "/auth/logout",
        "/login", "/refresh", "/logout",
        "/health", "/healthz"
    }:
        return PUBLIC

    # Rutas de perfil y verificación de Auth
    if path in {"/auth/me", "/auth/verify", "/verify"}:
        return AUTHENTICATED

    # Administración de usuarios en Auth
    if path in {"/auth/usuarios", "/auth/register"} and method == "POST":
        return ADMIN
    if path.startswith("/auth/usuarios/") and method == "PATCH":
        return ADMIN
    if path == "/auth/users" and method == "GET":
        return OPERATORS_AND_ADMINS

    # Slices - Aprobación de despliegues (AUTHENTICATION.rst / SLICE_API.rst)
    if method == "POST" and "/deployments/" in path and path.endswith("/approve"):
        return OPERATORS_AND_ADMINS
    if path == "/slices/aprobaciones" or path.startswith("/slices/aprobaciones/"):
        return OPERATORS
    if path == "/slices" or path.startswith("/slices/"):
        return CONSUMERS_AND_OPERATORS

    # CRUDs / Query Service / Admin views
    if path == "/cruds" or path.startswith("/cruds/"):
        if method == "GET":
            return AUTHENTICATED
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            return ADMIN
        return RoutePolicy(public=False)

    if path == "/admin" or path.startswith("/admin/"):
        return ADMIN

    # Image / Network
    if path.startswith("/image") or path.startswith("/network"):
        return ALL_ROLES

    # Por defecto, cualquier otra ruta requiere estar autenticado
    return AUTHENTICATED
