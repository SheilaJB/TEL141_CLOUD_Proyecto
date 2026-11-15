"""HTTP client for the OpenStack APIs (Keystone, Nova, Neutron, Glance).

Mismo enfoque que lab6.py (requests + REST), con lo que necesita un worker:
- túnel SSH al headnode vía gateway, que se reabre si se cae;
- tokens en caché por proyecto, renovados antes de expirar o tras un 401;
- códigos HTTP traducidos a RetryableError / NonRetryableError / NotFoundError.

No se usa el catálogo de Keystone: sus URLs son http://controller:..., que no
resuelven fuera del headnode.
"""

import socket
import subprocess
import threading
import time
from datetime import datetime
from typing import Any

import requests

from . import config
from .errors import NonRetryableError, NotFoundError, RetryableError

# Margen para renovar un token antes de que Keystone lo dé por vencido.
_TOKEN_MARGIN_S = 120

_tunnel_lock = threading.Lock()
_tunnel: subprocess.Popen | None = None

_token_lock = threading.Lock()
_tokens: dict[str, tuple[str, float]] = {}  # project_id -> (token, expires_epoch)

_local = threading.local()


# --- Túnel SSH -----------------------------------------------------------------

def _port_open(port: int) -> bool:
    try:
        with socket.create_connection((config.ACCESS_IP, port), timeout=1):
            return True
    except OSError:
        return False


def _tunnel_args() -> list[str]:
    args = [
        "ssh", "-N",
        "-p", str(config.HEADNODE_SSH_PORT),
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-o", f"ConnectTimeout={config.SSH_TIMEOUT}",
    ]
    if config.SSH_KEY:
        args.extend(["-i", config.SSH_KEY])
    if config.KNOWN_HOSTS:
        args.extend(["-o", f"UserKnownHostsFile={config.KNOWN_HOSTS}"])
    for service, local_port in config.LOCAL_PORTS.items():
        remote_port = config.REMOTE_PORTS[service]
        args.extend([
            "-L", f"{config.ACCESS_IP}:{local_port}:{config.REMOTE_API_HOST}:{remote_port}",
        ])
    args.append(f"{config.SSH_USER}@{config.GATEWAY_IP}")
    return args


def _stop_tunnel() -> None:
    global _tunnel
    if _tunnel is not None and _tunnel.poll() is None:
        _tunnel.terminate()
        try:
            _tunnel.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _tunnel.kill()
    _tunnel = None


def _ensure_tunnel() -> None:
    """Deja el túnel arriba (idempotente y seguro entre hilos)."""
    global _tunnel
    if not config.TUNNEL:
        return
    with _tunnel_lock:
        if _tunnel is not None and _tunnel.poll() is None:
            return
        _stop_tunnel()
        try:
            _tunnel = subprocess.Popen(
                _tunnel_args(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise RetryableError("TUNNEL_FAILED", str(exc)) from exc
        deadline = time.monotonic() + config.TUNNEL_START_TIMEOUT
        while time.monotonic() < deadline:
            if _tunnel.poll() is not None:
                stderr = (_tunnel.stderr.read() if _tunnel.stderr else "").strip()
                _tunnel = None
                raise RetryableError("TUNNEL_FAILED", stderr[:300] or "ssh exited")
            if all(_port_open(port) for port in config.LOCAL_PORTS.values()):
                return
            time.sleep(0.2)
        _stop_tunnel()
        raise RetryableError("TUNNEL_TIMEOUT", f"gateway {config.GATEWAY_IP}:{config.HEADNODE_SSH_PORT}")


def close() -> None:
    with _tunnel_lock:
        _stop_tunnel()


# --- HTTP ----------------------------------------------------------------------

def _session() -> requests.Session:
    # requests.Session no es thread-safe: una por hilo del worker.
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        _local.session = session
    return session


def url(service: str, path: str = "") -> str:
    port = config.LOCAL_PORTS[service]
    return f"http://{config.ACCESS_IP}:{port}{config.API_PATHS[service]}{path}"


def _error(response: requests.Response, method: str, path: str) -> Exception:
    status = response.status_code
    detail = f"{method} {path} -> {status}: {response.text[:300]}"
    if status == 404:
        return NotFoundError(detail)
    if status in {400, 422}:
        return NonRetryableError("OPENSTACK_BAD_REQUEST", detail)
    if status in {401, 403}:
        return NonRetryableError("OPENSTACK_FORBIDDEN", detail)
    if status == 409:
        # Nova/Neutron: recurso ocupado (task_state) o conflicto transitorio.
        return RetryableError("OPENSTACK_CONFLICT", detail)
    if status >= 500:
        return RetryableError("OPENSTACK_SERVER_ERROR", detail)
    return NonRetryableError(f"OPENSTACK_HTTP_{status}", detail)


def _send(method: str, target: str, **kwargs: Any) -> requests.Response:
    _ensure_tunnel()
    try:
        return _session().request(method, target, timeout=config.HTTP_TIMEOUT, **kwargs)
    except requests.Timeout as exc:
        raise RetryableError("OPENSTACK_TIMEOUT", f"{method} {target}") from exc
    except requests.ConnectionError as exc:
        # Probable túnel caído: se cierra para que el reintento lo reabra.
        close()
        raise RetryableError("OPENSTACK_UNREACHABLE", f"{method} {target}") from exc


# --- Keystone: tokens ----------------------------------------------------------

def _expires_epoch(expires_at: str) -> float:
    return datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()


def _issue_token(project_id: str) -> tuple[str, float]:
    body = {"auth": {
        "identity": {"methods": ["password"],
                     "password": {"user": {"id": config.ADMIN_USER_ID,
                                           "domain": {"id": config.DOMAIN_ID},
                                           "password": config.ADMIN_USER_PASSWORD}}},
        "scope": {"project": {"domain": {"id": config.DOMAIN_ID}, "id": project_id}},
    }}
    response = _send("POST", url("identity", "/auth/tokens"), json=body)
    if response.status_code != 201:
        raise _error(response, "POST", "/auth/tokens")
    token = response.headers.get("X-Subject-Token")
    if not token:
        raise RetryableError("TOKEN_MISSING")
    return token, _expires_epoch(response.json()["token"]["expires_at"])


def token(project_id: str | None = None) -> str:
    """Token scoped al proyecto (por defecto, el proyecto admin)."""
    key = project_id or config.ADMIN_PROJECT_ID
    with _token_lock:
        cached = _tokens.get(key)
        if cached and cached[1] - time.time() > _TOKEN_MARGIN_S:
            return cached[0]
    issued = _issue_token(key)
    with _token_lock:
        _tokens[key] = issued
    return issued[0]


def forget_token(project_id: str | None = None) -> None:
    with _token_lock:
        _tokens.pop(project_id or config.ADMIN_PROJECT_ID, None)


# --- API -----------------------------------------------------------------------

def request(
    service: str,
    method: str,
    path: str,
    *,
    project_id: str | None = None,
    expected: tuple[int, ...] = (200,),
    **kwargs: Any,
) -> requests.Response:
    """Llama a una API; devuelve la respuesta si el código está en `expected`."""
    headers = dict(kwargs.pop("headers", None) or {})
    if service == "compute":
        headers.setdefault("OpenStack-API-Version", f"compute {config.COMPUTE_API_VERSION}")
    for attempt in range(2):
        headers["X-Auth-Token"] = token(project_id)
        response = _send(method, url(service, path), headers=headers, **kwargs)
        if response.status_code == 401 and attempt == 0:
            forget_token(project_id)  # token revocado o vencido: uno nuevo
            continue
        break
    if response.status_code in expected:
        return response
    raise _error(response, method, path)


def get(service: str, path: str, **kwargs: Any) -> dict:
    return request(service, "GET", path, **kwargs).json()


def post(service: str, path: str, body: dict | None = None, **kwargs: Any) -> requests.Response:
    kwargs.setdefault("expected", (200, 201, 202, 204))
    return request(service, "POST", path, json=body, **kwargs)


def put(service: str, path: str, body: dict | None = None, **kwargs: Any) -> requests.Response:
    kwargs.setdefault("expected", (200, 201, 202, 204))
    return request(service, "PUT", path, json=body, **kwargs)


def delete(service: str, path: str, **kwargs: Any) -> bool:
    """DELETE idempotente: True si existía, False si ya no estaba (404)."""
    kwargs.setdefault("expected", (200, 202, 204))
    try:
        request(service, "DELETE", path, **kwargs)
    except NotFoundError:
        return False
    return True


def check() -> list[str]:
    """Prueba de conectividad; también lista lo que hace falta para config."""
    lines = [f"tunnel: {'on' if config.TUNNEL else 'off'} -> {config.ACCESS_IP}"]
    token()
    lines.append("keystone: admin token OK")
    hypervisors = get("compute", "/os-hypervisors").get("hypervisors", [])
    names = ", ".join(h.get("hypervisor_hostname", "?") for h in hypervisors)
    lines.append(f"nova: hypervisors = {names or '(none)'}")
    networks = get("network", "/networks", params={"name": config.EXTERNAL_NETWORK})["networks"]
    lines.append(f"neutron: '{config.EXTERNAL_NETWORK}' {'found' if networks else 'NOT FOUND'}")
    images = get("image", "/images").get("images", [])
    lines.append(f"glance: images = {', '.join(i.get('name') or '?' for i in images) or '(none)'}")
    lines.append(f"config: OS_COMPUTE_HOSTS = {config.COMPUTE_HOSTS or '(empty)'}")
    return lines


__all__ = [
    "NonRetryableError",
    "check",
    "close",
    "delete",
    "forget_token",
    "get",
    "post",
    "put",
    "request",
    "token",
    "url",
]
