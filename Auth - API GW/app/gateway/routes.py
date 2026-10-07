from dataclasses import dataclass


@dataclass(frozen=True)
class RoutePolicy:
    public: bool
    roles: frozenset[str] = frozenset()


PUBLIC = RoutePolicy(public=True)
ALL_ROLES = frozenset({"consumidor", "operador", "admin"})
AUTHENTICATED = RoutePolicy(public=False, roles=ALL_ROLES)
USER_MANAGERS = RoutePolicy(public=False, roles=frozenset({"operador", "admin"}))
CONSUMERS_AND_OPERATORS = RoutePolicy(public=False, roles=ALL_ROLES)
OPERATORS = RoutePolicy(public=False, roles=frozenset({"operador"}))
ADMIN = RoutePolicy(public=False, roles=frozenset({"admin"}))


def is_gateway_path(path: str) -> bool:
    return (
        path == "/auth"
        or path.startswith("/auth/")
        or path in {"/cruds", "/slices"}
        or path.startswith("/cruds/")
        or path.startswith("/slices/")
    )


def policy_for(path: str, method: str) -> RoutePolicy | None:
    if path in {"/auth/login", "/auth/refresh", "/auth/logout"}:
        return PUBLIC
    if path == "/auth/me" and method == "GET":
        return AUTHENTICATED
    if path == "/auth/usuarios" and method == "POST":
        return ADMIN
    if path.startswith("/auth/usuarios/") and method == "PATCH":
        return ADMIN
    if path == "/slices/aprobaciones" or path.startswith("/slices/aprobaciones/"):
        return OPERATORS
    if path == "/slices" or path.startswith("/slices/"):
        return CONSUMERS_AND_OPERATORS
    if path == "/cruds" or path.startswith("/cruds/"):
        if method == "GET":
            return AUTHENTICATED
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            return ADMIN
        return RoutePolicy(public=False)
    return None


def upstream_for(path: str) -> tuple[str, str] | None:
    if path == "/cruds" or path.startswith("/cruds/"):
        suffix = path.removeprefix("/cruds")
        return "cruds", suffix or "/"
    if path == "/slices" or path.startswith("/slices/"):
        suffix = path.removeprefix("/slices")
        if suffix == "/deployments" or suffix.startswith("/deployments/"):
            return "slice_manager", f"/api/v1{suffix}"
        if suffix == "/aprobaciones" or suffix.startswith("/aprobaciones/"):
            return "slice_manager", f"/api/v1{suffix}"
        return "slice_manager", f"/api/v1/slices{suffix}"
    return None
