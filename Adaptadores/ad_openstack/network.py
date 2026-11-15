"""Idempotent Neutron operations: links (networks) and ports.

Modelo (equivalente al de Linux):
  - Enlace privado -> red Neutron link-<link_id> en el proyecto del slice.
    Tipo VLAN de tenant: Neutron asigna el VLAN del rango de physnet2. No se
    usa el vlan_ctag del Network Manager porque es único solo dentro del
    slice (en Linux lo aísla el S-tag); en OpenStack chocaría entre slices.
    Subred con el CIDR asignado, sin DHCP ni gateway (como en los labs).
  - Enlace público -> la red flat `external` ya existente; no se crea nada.
  - Puerto -> puerto Neutron port-<port_id> con la MAC y la IP del Network
    Manager y port-security desactivado.
"""

import logging
import time

from contracts.adapter import AdapterActionInput

from . import client, config, resources
from .errors import NonRetryableError, RetryableError

logger = logging.getLogger(__name__)
DETACH_TIMEOUT_S = 40  # ADAPTER_ACTIVITY_TIMEOUT del Slice Manager es 60 s


def _param(request: AdapterActionInput, name: str):
    return request.action.params.get(name)


def _publico(request: AdapterActionInput) -> bool:
    if "publico" not in request.action.params:
        raise NonRetryableError("PUBLICO_MISSING")
    value = request.action.params["publico"]
    if not isinstance(value, bool):
        raise NonRetryableError("PUBLICO_INVALID")
    return value


def _guest_mode(request: AdapterActionInput, *, attach: bool) -> str:
    mode = _param(request, "guest")
    if mode is None and attach:
        return "prepare"
    if mode not in {"prepare", "cleanup", "live"}:
        raise NonRetryableError("GUEST_MODE_INVALID")
    if (attach and mode == "cleanup") or (not attach and mode == "prepare"):
        raise NonRetryableError("GUEST_MODE_INVALID")
    return mode


# --- Enlaces -------------------------------------------------------------------

def create_link(request: AdapterActionInput) -> dict:
    link = _param(request, "network") or {}
    link_id = request.action.target.id
    if bool(link.get("publico")):
        return {"tap_names": [], "network_id": resources.external_network_id()}

    project_id = resources.ensure_project(request.slice_id)
    network = resources.find_network(project_id, link_id)
    already_existed = network is not None
    if network is None:
        body = {"network": {
            "name": resources.network_name(link_id),
            "project_id": project_id,
            "admin_state_up": True,
            "port_security_enabled": False,
            "provider:network_type": "vlan",
            "provider:physical_network": config.VLAN_PHYSNET,
        }}
        network = client.post("network", "/networks", body, expected=(201,)).json()["network"]

    cidr = link.get("cidr")
    if cidr:
        subnets = client.get(
            "network", "/subnets", params={"network_id": network["id"]},
        )["subnets"]
        if not any(subnet["cidr"] == cidr for subnet in subnets):
            if subnets:
                raise NonRetryableError("SUBNET_CONFLICT", f"{network['id']} has {subnets[0]['cidr']}")
            already_existed = False
            client.post("network", "/subnets", {"subnet": {
                "name": f"subnet-{link_id}",
                "network_id": network["id"],
                "project_id": project_id,
                "ip_version": 4,
                "cidr": cidr,
                "enable_dhcp": False,
                "gateway_ip": None,
            }}, expected=(201,))

    return {
        "tap_names": [],
        "project_id": project_id,
        "network_id": network["id"],
        "_already_existed": already_existed,
    }


def delete_link(request: AdapterActionInput) -> dict:
    if _publico(request):
        return {"tap_names": []}
    project_id = resources.find_project(request.slice_id)
    network = (
        resources.find_network(project_id, request.action.target.id)
        if project_id else None
    )
    if network is None:
        return {"tap_names": [], "_already_absent": True}
    # Las subredes caen con la red. Si aún hay puertos de VM, Neutron
    # responde 409 y Temporal reintenta (espera a los detach pendientes).
    existed = client.delete("network", f"/networks/{network['id']}")
    return {"tap_names": [], "_already_absent": not existed}


# --- Puertos -------------------------------------------------------------------

def _port_network_id(project_id: str, request: AdapterActionInput) -> str:
    if bool(_param(request, "publico")):
        return resources.external_network_id()
    link_id = request.action.target.link_id
    network = resources.find_network(project_id, link_id)
    if network is None:
        # CREATE_LINK va en una ronda anterior; si falta, reintentar.
        raise RetryableError("NETWORK_MISSING", resources.network_name(link_id))
    return network["id"]


def _ensure_port(project_id: str, request: AdapterActionInput) -> tuple[dict, bool]:
    port_id = request.action.target.id
    existing = resources.find_port(project_id, port_id)
    if existing is not None:
        return existing, True
    allocation = _param(request, "network")
    if not isinstance(allocation, dict):
        raise NonRetryableError("PORT_PARAMS_MISSING")
    network_id = _port_network_id(project_id, request)
    port = {
        "name": resources.port_name(port_id),
        "network_id": network_id,
        "project_id": project_id,
        "admin_state_up": True,
        "port_security_enabled": False,
    }
    if allocation.get("mac"):
        port["mac_address"] = allocation["mac"]
    if allocation.get("ip"):
        port["fixed_ips"] = [{
            "ip_address": allocation["ip"],
            "subnet_id": resources.subnet_for_ip(network_id, allocation["ip"]),
        }]
    created = client.post("network", "/ports", {"port": port}, expected=(201,)).json()["port"]
    return created, False


def _require_server(project_id: str, node_id) -> dict:
    server = resources.find_server(project_id, node_id)
    if server is None:
        raise NonRetryableError("VM_STATE_INVALID", f"{resources.server_name(node_id)} not found")
    if server.get("status") not in {"ACTIVE", "SHUTOFF"}:
        raise RetryableError("VM_STATE_INVALID", f"{server['name']} is {server.get('status')}")
    return server


def attach_port(request: AdapterActionInput) -> dict:
    mode = _guest_mode(request, attach=True)
    project_id = resources.ensure_project(request.slice_id)
    port, already_existed = _ensure_port(project_id, request)
    facts = {"tap_names": [], "port_id": port["id"], "project_id": project_id}

    if mode == "prepare":
        # La VM aún no existe: CREATE_VM la creará con este puerto.
        return {**facts, "_already_existed": already_existed}

    # live: hot-plug en una VM existente.
    server = _require_server(project_id, request.action.target.node_id)
    if port.get("device_id") == server["id"]:
        return {**facts, "_already_existed": True}
    if port.get("device_id"):
        raise NonRetryableError("PORT_IN_USE", f"{port['id']} -> {port['device_id']}")
    client.post(
        "compute", f"/servers/{server['id']}/os-interface",
        {"interfaceAttachment": {"port_id": port["id"]}},
        project_id=project_id,
    )
    if _param(request, "cloud_init"):
        # Sin agente en el guest no se puede aplicar netplan en caliente.
        logger.warning("VM %s: puerto %s sin configurar en el guest", server["name"], port["id"])
    return facts


def _wait_unbound(port_id: str) -> None:
    deadline = time.monotonic() + DETACH_TIMEOUT_S
    while time.monotonic() < deadline:
        port = client.get("network", f"/ports/{port_id}")["port"]
        if not port.get("device_id"):
            return
        time.sleep(2)
    raise RetryableError("DETACH_NOT_APPLIED", port_id)


def detach_port(request: AdapterActionInput) -> dict:
    _publico(request)
    mode = _guest_mode(request, attach=False)
    project_id = resources.find_project(request.slice_id)
    port = resources.find_port(project_id, request.action.target.id) if project_id else None
    if port is None:
        return {"tap_names": [], "_already_absent": True}

    server_id = port.get("device_id")
    if server_id and port.get("device_owner", "").startswith("compute:"):
        if mode == "cleanup":
            # cleanup es para puertos que nunca llegaron a una VM.
            raise NonRetryableError("GUEST_MODE_MISMATCH", f"{port['id']} -> {server_id}")
        client.delete(
            "compute", f"/servers/{server_id}/os-interface/{port['id']}",
            project_id=project_id,
        )
        _wait_unbound(port["id"])
    client.delete("network", f"/ports/{port['id']}")
    return {"tap_names": [], "port_id": port["id"]}
