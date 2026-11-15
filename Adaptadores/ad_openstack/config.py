"""Local configuration for an OpenStack adapter worker.

Conexión (fase 2): el contenedor corre en `app`, que solo ve el gateway. Se
abre un túnel SSH al headnode a través del gateway (mismo esquema que el
adaptador Linux: ssh -p <puerto> ubuntu@<gateway>) y las APIs se consumen en
puertos locales, igual que en lab6.py.
"""

import os

from .errors import NonRetryableError


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# --- Túnel SSH (app -> gateway -> headnode) ---------------------------------
TUNNEL = _bool("OS_TUNNEL", True)
SSH_USER = os.getenv("ADAPTER_SSH_USER", "ubuntu")
GATEWAY_IP = os.getenv("ADAPTER_GATEWAY_IP", "10.20.11.250")
SSH_KEY = os.getenv("ADAPTER_SSH_KEY")
KNOWN_HOSTS = os.getenv("ADAPTER_KNOWN_HOSTS")
SSH_TIMEOUT = int(os.getenv("ADAPTER_SSH_TIMEOUT", "30"))
HEADNODE_SSH_PORT = int(os.getenv("OS_HEADNODE_SSH_PORT", "5821"))
# Destino de los -L vistos desde el headnode (las APIs escuchan ahí).
REMOTE_API_HOST = os.getenv("OS_REMOTE_API_HOST", "127.0.0.1")
TUNNEL_START_TIMEOUT = int(os.getenv("OS_TUNNEL_START_TIMEOUT", "20"))

# --- Endpoints (lado local del túnel; mismos defaults que lab6.py) ----------
# Con OS_TUNNEL=false se usan tal cual (p. ej. un túnel abierto a mano).
ACCESS_IP = os.getenv("OS_ACCESS_IP", "127.0.0.1")
KEYSTONE_PORT = int(os.getenv("OS_KEYSTONE_PORT", "55000"))
NOVA_PORT = int(os.getenv("OS_NOVA_PORT", "58774"))
NEUTRON_PORT = int(os.getenv("OS_NEUTRON_PORT", "59696"))
GLANCE_PORT = int(os.getenv("OS_GLANCE_PORT", "59292"))
# Puertos reales de las APIs en el headnode.
REMOTE_PORTS = {
    "identity": 5000,
    "compute": 8774,
    "network": 9696,
    "image": 9292,
}
LOCAL_PORTS = {
    "identity": KEYSTONE_PORT,
    "compute": NOVA_PORT,
    "network": NEUTRON_PORT,
    "image": GLANCE_PORT,
}
API_PATHS = {
    "identity": "/v3",
    "compute": "/v2.1",
    "network": "/v2.0",
    "image": "/v2",
}
COMPUTE_API_VERSION = os.getenv("OS_COMPUTE_API_VERSION", "2.87")  # máx. de Victoria
HTTP_TIMEOUT = int(os.getenv("OS_HTTP_TIMEOUT", "30"))

# --- Credenciales (dominio Cloud / usuario cloud_admin, como el .env del lab) -
DOMAIN_ID = os.getenv("OS_DOMAIN_ID", "")
ADMIN_PROJECT_ID = os.getenv("OS_ADMIN_PROJECT_ID", "")
ADMIN_USER_ID = os.getenv("OS_ADMIN_USER_ID", "")
ADMIN_USER_PASSWORD = os.getenv("OS_ADMIN_USER_PASSWORD", "")
ADMIN_ROLE_ID = os.getenv("OS_ADMIN_ROLE_ID", "")

# --- Red ---------------------------------------------------------------------
EXTERNAL_NETWORK = os.getenv("OS_EXTERNAL_NETWORK", "external")  # flat, ya creada
FLAT_PHYSNET = os.getenv("OS_FLAT_PHYSNET", "physnet1")
VLAN_PHYSNET = os.getenv("OS_VLAN_PHYSNET", "physnet2")

# --- Cómputo -----------------------------------------------------------------
# server_id (BD, el que elige Placement) -> hypervisor de Nova.
# Formato: "5:compute1,6:compute2,7:compute3".
COMPUTE_HOSTS_RAW = os.getenv("OS_COMPUTE_HOSTS", "")
COMPUTE_AZ = os.getenv("OS_COMPUTE_AZ", "nova")
MAX_CONCURRENT_ACTIVITIES = int(os.getenv("OS_MAX_CONCURRENT_ACTIVITIES", "8"))

_REQUIRED = {
    "OS_DOMAIN_ID": DOMAIN_ID,
    "OS_ADMIN_PROJECT_ID": ADMIN_PROJECT_ID,
    "OS_ADMIN_USER_ID": ADMIN_USER_ID,
    "OS_ADMIN_USER_PASSWORD": ADMIN_USER_PASSWORD,
    "OS_ADMIN_ROLE_ID": ADMIN_ROLE_ID,
}


def _parse_compute_hosts(raw: str) -> dict[int, str]:
    hosts: dict[int, str] = {}
    for item in filter(None, (part.strip() for part in raw.split(","))):
        server_id, sep, hostname = item.partition(":")
        if not sep or not hostname.strip():
            raise ValueError(f"OS_COMPUTE_HOSTS: invalid entry {item!r}")
        hosts[int(server_id)] = hostname.strip()
    return hosts


COMPUTE_HOSTS = _parse_compute_hosts(COMPUTE_HOSTS_RAW)


def require() -> None:
    """Valida la configuración obligatoria; se llama al arrancar el worker."""
    missing = [name for name, value in _REQUIRED.items() if not value]
    if missing:
        raise ValueError(f"missing OpenStack settings: {', '.join(missing)}")
    if TUNNEL and not SSH_KEY:
        raise ValueError("ADAPTER_SSH_KEY is required when OS_TUNNEL is enabled")


def compute_host(server_id: int | None) -> str:
    """Hypervisor de Nova para el server_id que eligió Placement."""
    if server_id is None:
        raise NonRetryableError("SERVER_ID_MISSING")
    try:
        return COMPUTE_HOSTS[server_id]
    except KeyError as exc:
        raise NonRetryableError("COMPUTE_HOST_UNKNOWN", f"server_id={server_id}") from exc
