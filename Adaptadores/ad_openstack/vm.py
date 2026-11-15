"""Idempotent Nova operations: create, delete, resize and slice-host destroy.

Equivalencias con el adaptador Linux:
  - overlay qcow2 + base     -> imagen de Glance (buscada por nombre)
  - vcpu/ram/disk del flavor -> flavor de Nova g3-<vcpu>c-<ram>m-<disk>g
  - host elegido por Placement -> availability_zone "nova:<computeN>"
  - seed ISO de cloud-init   -> config drive + user_data (la red estática la
                                lee cloud-init de los puertos de Neutron)
  - resize hot/hot_disk/cold -> solo cold (resize + confirmResize de Nova)
"""

import base64
import math
import time

from contracts.adapter import AdapterActionInput

from . import client, config, resources
from .errors import NonRetryableError, NotFoundError, RetryableError

# Deben caber en LONG_ADAPTER_ACTIVITY_TIMEOUT (5 min) del Slice Manager. Si
# una espera vence, se lanza RetryableError y el reintento retoma la espera.
BUILD_TIMEOUT_S = 240
DELETE_TIMEOUT_S = 120
RESIZE_TIMEOUT_S = 120  # por fase (VERIFY_RESIZE y luego ACTIVE)
POLL_S = 3


def _flavor(params: dict, key: str) -> tuple[int, int, int]:
    flavor = params.get(key)
    if not isinstance(flavor, dict):
        raise NonRetryableError("VM_PARAMS_INCOMPLETE")
    try:
        values = (int(flavor["vcpu"]), int(flavor["ram_mb"]), int(flavor["disk_mb"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise NonRetryableError("VM_PARAMS_INCOMPLETE") from exc
    if any(value <= 0 for value in values):
        raise NonRetryableError("VM_PARAMS_INVALID")
    return values


def _disk_gb(disk_mb: int) -> int:
    # Nova mide el disco en GB enteros: se redondea hacia arriba.
    return math.ceil(disk_mb / 1024)


# --- Glance / flavors ----------------------------------------------------------

def _image_id(reference: str) -> str:
    images = client.get("image", "/images", params={"name": reference})["images"]
    if not images:
        raise NonRetryableError("BASE_MISSING", f"image {reference!r} not in Glance")
    if len(images) > 1:
        raise NonRetryableError("OPENSTACK_DUPLICATE", f"{len(images)} images named {reference!r}")
    return images[0]["id"]


def _find_flavor(name: str) -> str | None:
    flavors = client.get(
        "compute", "/flavors/detail", params={"is_public": "None"},
    )["flavors"]
    return next((flavor["id"] for flavor in flavors if flavor["name"] == name), None)


def _ensure_flavor(vcpu: int, ram_mb: int, disk_mb: int) -> str:
    name = f"g3-{vcpu}c-{ram_mb}m-{_disk_gb(disk_mb)}g"
    flavor_id = _find_flavor(name)
    if flavor_id:
        return flavor_id
    response = client.post("compute", "/flavors", {"flavor": {
        "name": name, "vcpus": vcpu, "ram": ram_mb, "disk": _disk_gb(disk_mb),
        "os-flavor-access:is_public": True,
    }}, expected=(200, 409))
    if response.status_code == 200:
        return response.json()["flavor"]["id"]
    flavor_id = _find_flavor(name)  # creado en paralelo por otra actividad
    if flavor_id is None:
        raise RetryableError("FLAVOR_RACE", name)
    return flavor_id


# --- Nova helpers --------------------------------------------------------------

def _server(project_id: str, server_id: str) -> dict:
    return client.get("compute", f"/servers/{server_id}", project_id=project_id)["server"]


def _fault(server: dict) -> str:
    fault = server.get("fault") or {}
    return f"{server.get('name')}: {fault.get('message') or server.get('status')}"[:300]


def _wait_status(project_id: str, server_id: str, wanted: set[str], timeout_s: int) -> dict:
    deadline = time.monotonic() + timeout_s
    server: dict = {}
    while time.monotonic() < deadline:
        server = _server(project_id, server_id)
        status = server.get("status")
        if status in wanted:
            return server
        if status == "ERROR":
            raise NonRetryableError("VM_BUILD_FAILED", _fault(server))
        time.sleep(POLL_S)
    # El siguiente intento retoma la espera sobre la misma VM.
    raise RetryableError("VM_STATE_TIMEOUT", f"{server.get('name')} is {server.get('status')}")


def _wait_deleted(project_id: str, server_id: str) -> None:
    deadline = time.monotonic() + DELETE_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            _server(project_id, server_id)
        except NotFoundError:
            return
        time.sleep(POLL_S)
    raise RetryableError("VM_DELETE_TIMEOUT", server_id)


def _host(server: dict) -> str | None:
    return server.get("OS-EXT-SRV-ATTR:hypervisor_hostname") or server.get("OS-EXT-SRV-ATTR:host")


def _user_data(hostname: str) -> str:
    # Mismo usuario/clave que el seed de Linux; la red la pone el config drive.
    text = "\n".join([
        "#cloud-config",
        f"hostname: '{hostname}'",
        "users:",
        "  - default",
        "ssh_pwauth: true",
        "chpasswd:",
        "  expire: false",
        "  users:",
        "    - {name: ubuntu, password: ubuntu, type: text}",
        "",
    ])
    return base64.b64encode(text.encode()).decode()


def _facts(server: dict, vcpu: int, ram_mb: int, disk_mb: int) -> dict:
    return {
        "state": "RUNNING" if server.get("status") == "ACTIVE" else server.get("status"),
        "server_id": server["id"],
        "compute_host": _host(server),
        "vcpu": vcpu,
        "ram_mb": ram_mb,
        "disk_mb": disk_mb,
    }


# --- CREATE_VM -----------------------------------------------------------------

def _port_ids(project_id: str, nics: list[dict]) -> list[str]:
    """Puertos ya creados por ATTACH_PORT(prepare), en orden de nic_index."""
    ordered = sorted(nics, key=lambda nic: (nic.get("nic_index") is None, nic.get("nic_index") or 0))
    port_ids = []
    for nic in ordered:
        if not nic.get("port_id"):
            raise NonRetryableError("NIC_INCOMPLETE")
        port = resources.find_port(project_id, nic["port_id"])
        if port is None:
            raise RetryableError("PORT_MISSING", resources.port_name(nic["port_id"]))
        port_ids.append(port["id"])
    return port_ids


def create_vm(request: AdapterActionInput) -> dict:
    params = request.action.params
    node_id = request.action.target.id
    image = params.get("image") or {}
    vcpu, ram_mb, disk_mb = _flavor(params, "flavor")
    if not image.get("reference"):
        raise NonRetryableError("BASE_MISSING")
    use_cloud_init = image.get("cloud_init")
    if not isinstance(use_cloud_init, bool):
        raise NonRetryableError("IMAGE_CLOUD_INIT_INVALID")
    compute = config.compute_host(request.server_id)
    project_id = resources.ensure_project(request.slice_id)

    existing = resources.find_server(project_id, node_id)
    if existing is not None:
        # Intento previo: esperar a que termine de construirse.
        server = _wait_built(project_id, existing["id"])
        return {**_facts(server, vcpu, ram_mb, disk_mb), "_already_existed": True}

    server_body = {
        "name": resources.server_name(node_id),
        "imageRef": _image_id(str(image["reference"])),
        "flavorRef": _ensure_flavor(vcpu, ram_mb, disk_mb),
        "networks": [{"port": port_id} for port_id in _port_ids(project_id, params.get("nics") or [])] or "none",
        # Respeta la decisión de Placement (requiere rol admin en el proyecto).
        "availability_zone": f"{config.COMPUTE_AZ}:{compute}",
        "config_drive": True,
        "metadata": {"slice_id": str(request.slice_id), "node_id": str(node_id)},
    }
    if use_cloud_init:
        server_body["user_data"] = _user_data(params.get("hostname") or "ubuntu")
    created = client.post(
        "compute", "/servers", {"server": server_body},
        project_id=project_id, expected=(202,),
    ).json()["server"]
    return _facts(_wait_built(project_id, created["id"]), vcpu, ram_mb, disk_mb)


def _wait_built(project_id: str, server_id: str) -> dict:
    try:
        return _wait_status(project_id, server_id, {"ACTIVE"}, BUILD_TIMEOUT_S)
    except NonRetryableError:
        # VM en ERROR: se borra para no dejar basura (los puertos se conservan)
        # y el error llega al orquestador.
        client.delete("compute", f"/servers/{server_id}", project_id=project_id)
        raise


# --- DELETE_VM -----------------------------------------------------------------

def _delete_server(project_id: str, server_id: str) -> bool:
    existed = client.delete("compute", f"/servers/{server_id}", project_id=project_id)
    _wait_deleted(project_id, server_id)
    return existed


def delete_vm(request: AdapterActionInput) -> dict:
    node_id = request.action.params.get("vm_id") or request.action.target.id
    project_id = resources.find_project(request.slice_id)
    server = resources.find_server(project_id, node_id) if project_id else None
    if server is None:
        return {"_already_absent": True}
    # Los puertos (creados aparte) quedan libres; DETACH_PORT los borra.
    existed = _delete_server(project_id, server["id"])
    return {"server_id": server["id"], "_already_absent": not existed}


# --- RESIZE_VM -----------------------------------------------------------------

def _current_flavor(server: dict) -> tuple[int, int, int]:
    # Microversión >= 2.47: el flavor viene embebido en el servidor.
    flavor = server.get("flavor") or {}
    return int(flavor.get("vcpus", 0)), int(flavor.get("ram", 0)), int(flavor.get("disk", 0))


def resize_vm(request: AdapterActionInput) -> dict:
    params = request.action.params
    hot_resize = params.get("hot_resize")
    if not isinstance(hot_resize, bool):
        raise NonRetryableError("VM_PARAMS_INVALID")
    if hot_resize:
        # P63: nunca se convierte en frío en silencio; Nova no tiene resize en caliente.
        raise NonRetryableError("HOT_RESIZE_UNSUPPORTED")
    before = _flavor(params, "before_flavor")
    after = _flavor(params, "flavor")
    if after[2] < before[2]:
        raise NonRetryableError("DISK_SHRINK")
    target = (after[0], after[1], _disk_gb(after[2]))
    project_id = resources.find_project(request.slice_id)
    server = resources.find_server(project_id, request.action.target.id) if project_id else None
    if server is None:
        raise NonRetryableError("VM_STATE_INVALID", "server not found")
    facts_args = (after[0], after[1], after[2])

    status = server.get("status")
    if status == "VERIFY_RESIZE":
        # Un intento previo ya redimensionó: solo falta confirmar.
        if _current_flavor(server) != target:
            raise NonRetryableError("VM_STATE_INVALID", "pending resize to another flavor")
    elif status in {"ACTIVE", "SHUTOFF"}:
        if _current_flavor(server) == target:
            return {**_facts(server, *facts_args), "path": "cold", "_already_existed": True}
        if _current_flavor(server)[2] > target[2]:
            raise NonRetryableError("DISK_SHRINK")
        client.post(
            "compute", f"/servers/{server['id']}/action",
            {"resize": {"flavorRef": _ensure_flavor(*after)}},
            project_id=project_id, expected=(202,),
        )
        server = _wait_status(project_id, server["id"], {"VERIFY_RESIZE"}, RESIZE_TIMEOUT_S)
    elif status in {"RESIZE", "BUILD"}:
        server = _wait_status(project_id, server["id"], {"VERIFY_RESIZE", "ACTIVE"}, RESIZE_TIMEOUT_S)
        if server["status"] == "ACTIVE":
            raise RetryableError("RESIZE_NOT_APPLIED", server["name"])
    else:
        raise NonRetryableError("VM_STATE_INVALID", f"{server['name']} is {status}")

    client.post(
        "compute", f"/servers/{server['id']}/action", {"confirmResize": None},
        project_id=project_id, expected=(204,),
    )
    server = _wait_status(project_id, server["id"], {"ACTIVE", "SHUTOFF"}, RESIZE_TIMEOUT_S)
    if _current_flavor(server) != target:
        raise RetryableError("RESIZE_NOT_APPLIED", f"{server['name']} flavor={_current_flavor(server)}")
    return {**_facts(server, *facts_args), "path": "cold"}


# --- DESTROY_SLICE_HOST --------------------------------------------------------

def _cleanup_slice_network(project_id: str) -> bool:
    """Si el slice ya no tiene VMs, borra puertos, redes y el proyecto."""
    remaining = client.get(
        "compute", "/servers", project_id=project_id,
    )["servers"]
    if remaining:
        return False  # otro host del slice aún está destruyendo sus VMs
    for port in client.get("network", "/ports", params={"project_id": project_id})["ports"]:
        if not port.get("device_owner", "").startswith("network:"):
            client.delete("network", f"/ports/{port['id']}")
    for network in client.get("network", "/networks", params={"project_id": project_id})["networks"]:
        client.delete("network", f"/networks/{network['id']}")
    client.delete("identity", f"/projects/{project_id}")
    client.forget_token(project_id)
    return True


def destroy_slice_host(request: AdapterActionInput) -> dict:
    project_id = resources.find_project(request.slice_id)
    destroyed_vms: list[str] = []
    pending: list[tuple[str, list[dict]]] = []
    # Primero se piden todos los borrados y luego se espera: Nova los procesa
    # en paralelo y el total cabe en el timeout de la actividad.
    for vm in request.action.params.get("vms", []):
        vm_id = str(vm["vm_id"])
        server = resources.find_server(project_id, vm_id) if project_id else None
        if server is not None:
            ports = client.get(
                "network", "/ports", params={"device_id": server["id"]},
            )["ports"]
            client.delete("compute", f"/servers/{server['id']}", project_id=project_id)
            pending.append((server["id"], ports))
        destroyed_vms.append(vm_id)
    for server_id, ports in pending:
        _wait_deleted(project_id, server_id)
        for port in ports:
            client.delete("network", f"/ports/{port['id']}")
    destroyed_network = False
    if project_id:
        destroyed_network = _cleanup_slice_network(project_id)
        if destroyed_network:
            resources.forget_project(request.slice_id)
    return {"destroyed_vms": destroyed_vms, "destroyed_bridge": destroyed_network}
