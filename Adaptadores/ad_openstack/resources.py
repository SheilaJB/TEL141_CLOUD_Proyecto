"""Naming and idempotent lookups shared by the OpenStack handlers.

Convención de nombres (es lo que hace idempotente al adaptador: antes de
crear se busca por nombre dentro del proyecto del slice):
  proyecto Keystone : slice-<slice_id>
  red Neutron       : link-<link_id>
  puerto Neutron    : port-<port_id>
  servidor Nova     : vm-<node_id>

Neutron y Keystone se llaman con el token admin indicando project_id; Nova
usa un token scoped al proyecto del slice para que la VM le pertenezca.
"""

import ipaddress
import threading

from . import client, config
from .errors import NonRetryableError, RetryableError

_projects: dict[int, str] = {}
_projects_lock = threading.Lock()
_external_network_id: str | None = None


def project_name(slice_id: int) -> str:
    return f"slice-{slice_id}"


def network_name(link_id) -> str:
    return f"link-{link_id}"


def port_name(port_id) -> str:
    return f"port-{port_id}"


def server_name(node_id) -> str:
    return f"vm-{node_id}"


# --- Keystone ------------------------------------------------------------------

def find_project(slice_id: int) -> str | None:
    with _projects_lock:
        if slice_id in _projects:
            return _projects[slice_id]
    found = client.get(
        "identity", "/projects",
        params={"name": project_name(slice_id), "domain_id": config.DOMAIN_ID},
    )["projects"]
    return found[0]["id"] if found else None


def ensure_project(slice_id: int) -> str:
    """Proyecto del slice, creado bajo demanda, con rol admin para cloud_admin."""
    with _projects_lock:
        if slice_id in _projects:
            return _projects[slice_id]
    project_id = find_project(slice_id)
    if project_id is None:
        body = {"project": {
            "name": project_name(slice_id),
            "domain_id": config.DOMAIN_ID,
            "description": f"Slice {slice_id} (adaptador OpenStack)",
        }}
        response = client.post("identity", "/projects", body, expected=(201, 409))
        if response.status_code == 201:
            project_id = response.json()["project"]["id"]
        else:
            # Otra actividad del mismo slice lo creó en paralelo.
            project_id = find_project(slice_id)
            if project_id is None:
                raise RetryableError("PROJECT_RACE", project_name(slice_id))
    # Siempre que no está en caché: cubre un intento previo que creó el
    # proyecto pero murió antes de asignar el rol. El PUT es idempotente.
    client.put(
        "identity",
        f"/projects/{project_id}/users/{config.ADMIN_USER_ID}/roles/{config.ADMIN_ROLE_ID}",
    )
    with _projects_lock:
        _projects[slice_id] = project_id
    return project_id


def forget_project(slice_id: int) -> None:
    with _projects_lock:
        _projects.pop(slice_id, None)


# --- Neutron -------------------------------------------------------------------

def _one(items: list[dict], what: str) -> dict | None:
    if len(items) > 1:
        raise NonRetryableError("OPENSTACK_DUPLICATE", f"{len(items)} {what}")
    return items[0] if items else None


def find_network(project_id: str, link_id) -> dict | None:
    networks = client.get(
        "network", "/networks",
        params={"name": network_name(link_id), "project_id": project_id},
    )["networks"]
    return _one(networks, network_name(link_id))


def external_network_id() -> str:
    global _external_network_id
    if _external_network_id is None:
        networks = client.get(
            "network", "/networks", params={"name": config.EXTERNAL_NETWORK},
        )["networks"]
        if not networks:
            raise NonRetryableError("EXTERNAL_NETWORK_MISSING", config.EXTERNAL_NETWORK)
        _external_network_id = networks[0]["id"]
    return _external_network_id


def subnet_for_ip(network_id: str, ip: str) -> str:
    """Subred de `network_id` cuyo CIDR contiene `ip`."""
    address = ipaddress.ip_address(ip)
    subnets = client.get("network", "/subnets", params={"network_id": network_id})["subnets"]
    for subnet in subnets:
        if address in ipaddress.ip_network(subnet["cidr"], strict=False):
            return subnet["id"]
    raise NonRetryableError("SUBNET_MISSING", f"{ip} not in network {network_id}")


def find_port(project_id: str, port_id) -> dict | None:
    ports = client.get(
        "network", "/ports",
        params={"name": port_name(port_id), "project_id": project_id},
    )["ports"]
    return _one(ports, port_name(port_id))


# --- Nova ----------------------------------------------------------------------

def find_server(project_id: str, node_id) -> dict | None:
    # `name` en Nova es regex: se ancla para no confundir prefijos.
    servers = client.get(
        "compute", "/servers/detail",
        params={"name": f"^{server_name(node_id)}$"},
        project_id=project_id,
    )["servers"]
    return _one(servers, server_name(node_id))
