"""Idempotent Linux/OVS network operations.

Modelo:
  - br-sl-<slice>: OVS del slice. Los puertos de VM son access con C-tag.
  - br-provider:   OVS provider. Unica salida del host (uplink ens4).
  - veth vx-<slice> <-> vw-<slice>: cable entre ambos OVS, solo si el slice
    cruza hosts.
      vx: en br-sl-<slice>, trunk (sin tag, lleva los C-tag).
      vw: en br-provider, dot1q-tunnel con tag = S-tag del slice.
  - Puertos publicos: access directo en br-provider con tag 4000 (sin C-tag).
"""

import hashlib
import ipaddress
import base64
import json
import logging
import re
import threading
import time

from contracts.adapter import AdapterActionInput, AdapterHost

from . import config, host, libvirt_vm
from .cloudinit import render_network_config
from .errors import NonRetryableError, RetryableError

PROVIDER_BRIDGE = "br-provider"
STAG_MIN = 100
STAG_MAX = 3999  # 4000 reservado para trafico publico
GUEST_COMMAND_TIMEOUT_S = 30
logger = logging.getLogger(__name__)


_trunk_locks: dict[tuple[str, str], threading.Lock] = {}
_trunk_locks_guard = threading.Lock()


def _trunk_lock(ip: str, slice_id) -> threading.Lock:
    """Un lock por (host, slice): los create_link del mismo slice corren en paralelo."""
    key = (ip, str(slice_id))
    with _trunk_locks_guard:
        return _trunk_locks.setdefault(key, threading.Lock())


def _param(request: AdapterActionInput, name: str):
    return request.action.params.get(name)


def _publico(request: AdapterActionInput) -> bool:
    if "publico" not in request.action.params:
        raise NonRetryableError("PUBLICO_MISSING")
    value = request.action.params["publico"]
    if not isinstance(value, bool):
        raise NonRetryableError("PUBLICO_INVALID")
    return value


def _tap_name(port_id: str) -> str:
    return "tp-" + hashlib.sha1(port_id.encode()).hexdigest()[:10]


def _uplink() -> str:
    uplink = getattr(config, "PROVIDER_UPLINK", "ens4")
    mgmt = getattr(config, "MGMT_IFACE", "ens3")
    if uplink == mgmt:
        raise NonRetryableError("UPLINK_IS_MGMT")
    return uplink


def _link_exists(ip: str, name: str) -> bool:
    try:
        host.run(ip, ["ip", "link", "show", name])
        return True
    except RuntimeError:
        return False


def _link_exists_checked(ip: str, name: str) -> bool:
    links = json.loads(host.run(ip, ["ip", "-j", "link", "show"]))
    return any(link.get("ifname") == name for link in links)


def _guest_mode(request: AdapterActionInput, *, attach: bool) -> str:
    mode = _param(request, "guest")
    if mode is None and attach:
        return "prepare"
    if mode not in {"prepare", "cleanup", "live"}:
        raise NonRetryableError("GUEST_MODE_INVALID")
    if (attach and mode == "cleanup") or (not attach and mode == "prepare"):
        raise NonRetryableError("GUEST_MODE_INVALID")
    return mode


def _cloud_init_enabled(request: AdapterActionInput) -> bool:
    value = _param(request, "cloud_init")
    if not isinstance(value, bool):
        raise NonRetryableError("IMAGE_CLOUD_INIT_INVALID")
    return value


def _domain_for_port(request: AdapterActionInput) -> str:
    node_id = request.action.target.node_id
    if node_id is None:
        raise NonRetryableError("PORT_PARAMS_MISSING")
    return "vm-" + str(node_id)[:8]


def _require_guest_mode_state(
    address: str, vm_name: str, mode: str
) -> bool:
    exists = libvirt_vm.domain_exists(address, vm_name)
    if mode in {"prepare", "cleanup"}:
        if exists:
            raise NonRetryableError("GUEST_MODE_MISMATCH")
        return False
    if not exists or libvirt_vm._virsh(address, "domstate", vm_name).strip() != "running":
        raise NonRetryableError("VM_STATE_INVALID")
    return True


def _guest_agent_command(
    address: str, vm_name: str, execute: str, arguments: dict | None = None
) -> dict:
    payload: dict[str, object] = {"execute": execute}
    if arguments is not None:
        payload["arguments"] = arguments
    raw = libvirt_vm._virsh(
        address, "qemu-agent-command", vm_name, json.dumps(payload)
    )
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NonRetryableError("GUEST_AGENT_RESPONSE_INVALID") from exc
    if not isinstance(result, dict):
        raise NonRetryableError("GUEST_AGENT_RESPONSE_INVALID")
    if "error" in result:
        raise NonRetryableError("GUEST_AGENT_COMMAND_FAILED", str(result["error"])[:500])
    value = result.get("return")
    if not isinstance(value, dict):
        return {}
    return value


def _guest_exec(
    address: str,
    vm_name: str,
    path: str,
    arguments: list[str] | None = None,
    *,
    allow_failure: bool = False,
) -> tuple[int, str]:
    result = _guest_agent_command(
        address,
        vm_name,
        "guest-exec",
        {
            "path": path,
            "arg": arguments or [],
            "capture-output": True,
        },
    )
    process_id = result.get("pid")
    if not isinstance(process_id, int):
        raise NonRetryableError("GUEST_AGENT_RESPONSE_INVALID")
    deadline = time.monotonic() + GUEST_COMMAND_TIMEOUT_S
    while time.monotonic() < deadline:
        status = _guest_agent_command(
            address, vm_name, "guest-exec-status", {"pid": process_id}
        )
        if status.get("exited"):
            exit_code = status.get("exitcode")
            if not isinstance(exit_code, int):
                raise NonRetryableError("GUEST_AGENT_RESPONSE_INVALID")
            output = status.get("out-data", "")
            try:
                decoded = base64.b64decode(output).decode("utf-8", errors="replace")
                error_output = base64.b64decode(
                    status.get("err-data", "")
                ).decode("utf-8", errors="replace")
            except (ValueError, TypeError) as exc:
                raise NonRetryableError("GUEST_AGENT_RESPONSE_INVALID") from exc
            if exit_code != 0 and not allow_failure:
                raise NonRetryableError(
                    "GUEST_COMMAND_FAILED", (error_output or decoded)[:500]
                )
            return exit_code, decoded
        time.sleep(0.2)
    raise RetryableError("GUEST_COMMAND_TIMEOUT")


def _guest_ping(address: str, vm_name: str) -> None:
    _guest_agent_command(address, vm_name, "guest-ping")


def _log_guest_error(operation: str, vm_name: str, mac: str, exc: Exception) -> None:
    if isinstance(exc, RetryableError) and exc.code != "GUEST_COMMAND_TIMEOUT":
        raise exc
    if isinstance(exc, NonRetryableError) and exc.code == "LIBVIRT_UNAVAILABLE":
        raise exc
    logger.warning("Guest network %s failed for %s (%s): %s", operation, vm_name, mac, exc)


def _hot_netplan_path(mac: str) -> str:
    normalized = re.sub(r"[:-]", "", mac).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", normalized):
        raise NonRetryableError("NIC_INCOMPLETE")
    return f"/etc/netplan/90-hot-{normalized}.yaml"

def _hot_netplan(network_config: str) -> str:
    # Netplan necesita la clave raíz `network:`; network-config de cloud-init no la incluye.
    body = "\n".join(f"  {line}" if line else line for line in network_config.splitlines())
    return "network:\n" + body + "\n"

def _guest_read_file(address: str, vm_name: str, path: str) -> str | None:
    script = (
        "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
        "sys.exit(44) if not p.exists() else sys.stdout.write(p.read_text())"
    )
    code, content = _guest_exec(
        address, vm_name, "/usr/bin/python3", ["-c", script, path],
        allow_failure=True,
    )
    if code == 44:
        return None
    if code != 0:
        raise NonRetryableError("GUEST_COMMAND_FAILED", content[:500])
    return content


def _guest_write_file(address: str, vm_name: str, path: str, content: str) -> None:
    script = (
        "import base64,pathlib,sys; p=pathlib.Path(sys.argv[1]); "
        "tmp=p.with_suffix(p.suffix+'.tmp'); "
        "tmp.write_bytes(base64.b64decode(sys.argv[2])); tmp.replace(p)"
    )
    _guest_exec(
        address,
        vm_name,
        "/usr/bin/python3",
        ["-c", script, path, base64.b64encode(content.encode()).decode()],
    )


def _guest_remove_file(address: str, vm_name: str, path: str) -> None:
    script = "import pathlib,sys; pathlib.Path(sys.argv[1]).unlink(missing_ok=True)"
    _guest_exec(address, vm_name, "/usr/bin/python3", ["-c", script, path])


def _run_netplan(address: str, vm_name: str, operation: str) -> None:
    _guest_exec(address, vm_name, "/usr/sbin/netplan", [operation])


def _remove_tap(ip: str, bridge: str, tap: str) -> bool:
    existed = _link_exists_checked(ip, tap)
    host.run(ip, ["ovs-vsctl", "--if-exists", "del-port", bridge, tap])
    host.run(ip, ["ovs-vsctl", "--if-exists", "del-port", tap])
    if existed:
        host.run(ip, ["ip", "link", "del", tap])
    return existed


def _ensure_provider(ip: str) -> None:
    """br-provider con el uplink (solo ens4). Idempotente."""
    uplink = _uplink()
    host.run(ip, ["ovs-vsctl", "--may-exist", "add-br", PROVIDER_BRIDGE])
    host.run(ip, ["ip", "link", "set", uplink, "up"])
    host.run(ip, ["ovs-vsctl", "--may-exist", "add-port", PROVIDER_BRIDGE, uplink])


def _ensure_slice_bridge(ip: str, slice_id) -> str:
    bridge = f"br-sl-{slice_id}"
    host.run(ip, ["ovs-vsctl", "--may-exist", "add-br", bridge])
    host.run(ip, ["ip", "link", "set", bridge, "up"])
    return bridge


def _ensure_trunk(ip: str, slice_id, bridge: str, vlan_stag: int) -> None:
    """veth entre br-sl-<slice> (trunk) y br-provider (dot1q-tunnel, S-tag)."""
    vx = f"vx-{slice_id}"
    vw = f"vw-{slice_id}"
    with _trunk_lock(ip, slice_id):
        if not _link_exists(ip, vx):
            try:
                host.run(ip, ["ip", "link", "add", vx, "type", "veth", "peer", "name", vw])
            except RuntimeError:
                # carrera con otro proceso: si ya existe, seguimos; si no, error real
                if not _link_exists(ip, vx):
                    raise
        host.run(ip, ["ip", "link", "set", vx, "up"])
        host.run(ip, ["ip", "link", "set", vw, "up"])
        # lado slice: trunk, sin tag
        host.run(ip, ["ovs-vsctl", "--may-exist", "add-port", bridge, vx])
        # lado provider: empuja el S-tag del slice
        host.run(ip, ["ovs-vsctl", "--may-exist", "add-port", PROVIDER_BRIDGE, vw])
        host.run(
            ip,
            ["ovs-vsctl", "set", "port", vw, f"tag={vlan_stag}", "vlan_mode=dot1q-tunnel"],
        )


def create_link(request: AdapterActionInput) -> dict:
    if not request.hosts:
        raise NonRetryableError("HOSTS_MISSING")
    slice_id = request.slice_id
    params = request.action.params
    remote = len(request.hosts) > 1 or bool(params.get("remote"))
    vlan_stag = params.get("vlan_stag")
    publico = bool((params.get("network") or {}).get("publico"))
    ## ---
    print(f"create_link slice={slice_id} remote={remote} publico={publico} vlan_stag={vlan_stag} params={params}")
    ## ---
    if publico:
        # Enlace publico: sus puertos van directo a br-provider (tag 4000, sin
        # C-tag). No hay br-sl ni veth; solo se asegura br-provider + uplink.
        for target in request.hosts:
            _validate_host(target)
            _ensure_provider(host.endpoint(target))
        return {"tap_names": []}
    if remote:
        if vlan_stag is None:
            raise NonRetryableError("LINK_PARAMS_MISSING")
        vlan_stag = int(vlan_stag)
        if not STAG_MIN <= vlan_stag <= STAG_MAX:
            raise NonRetryableError("VLAN_STAG_INVALID")
    for target in request.hosts:
        _validate_host(target)
        ip = host.endpoint(target)
        bridge = _ensure_slice_bridge(ip, slice_id)
        if remote:
            _ensure_provider(ip)
            _ensure_trunk(ip, slice_id, bridge, vlan_stag)
    return {"tap_names": []}


def attach_port(request: AdapterActionInput) -> dict:
    target = _require_host(request.host)
    ip = host.endpoint(target)
    port_id = _param(request, "port_id") or str(request.action.target.id)
    tap = _tap_name(str(port_id))
    vm_name = _domain_for_port(request)
    mode = _guest_mode(request, attach=True)
    is_live = _require_guest_mode_state(ip, vm_name, mode)
    nic: dict | None = None
    mac = ""
    netplan_path = ""
    netplan_contents = ""
    interfaces: dict[str, str] = {}
    cloud_init = False
    if is_live:
        cloud_init = _cloud_init_enabled(request)
        logger.info("VM %s: cloud_init=%s", vm_name, cloud_init)
        network = _param(request, "network")
        if not isinstance(network, dict):
            raise NonRetryableError("PORT_PARAMS_MISSING")
        nic = dict(network)
        nic["publico"] = bool(_param(request, "publico"))
        mac = str(nic.get("mac") or "")
        if cloud_init:
            netplan_path = _hot_netplan_path(mac)
            netplan_contents = _hot_netplan(render_network_config([nic]))
        interfaces = libvirt_vm.domain_interfaces(
            libvirt_vm._virsh(ip, "dumpxml", vm_name)
        )
        if tap in interfaces and interfaces[tap].lower() != mac.lower():
            raise NonRetryableError("TAP_DOMAIN_CONFLICT")
    if _param(request, "publico"):
        _ensure_provider(ip)
        bridge = PROVIDER_BRIDGE
        tag = str(_param(request, "public_vid") or config.PUBLIC_VID)
    else:
        vlan = _param(request, "vlan_ctag")
        if vlan is None:
            raise NonRetryableError("PORT_PARAMS_MISSING")
        bridge = _ensure_slice_bridge(ip, request.slice_id)
        tag = str(vlan)
    already_existed = _link_exists_checked(ip, tap)
    if not already_existed:
        host.run(ip, ["ip", "tuntap", "add", "dev", tap, "mode", "tap"])
    host.run(ip, ["ovs-vsctl", "--may-exist", "add-port", bridge, tap])
    host.run(ip, ["ovs-vsctl", "set", "port", tap, "tag=" + tag])
    host.run(ip, ["ip", "link", "set", tap, "up"])

    if mode == "prepare":
        return {"tap_names": [tap], "_already_existed": already_existed}
    if tap not in interfaces:
        libvirt_vm.change_device(
            ip,
            vm_name,
            libvirt_vm.interface_xml(port_id=str(port_id), mac=mac),
            attach=True,
        )
    if cloud_init:
        try:
            _guest_ping(ip, vm_name)
            existing_contents = _guest_read_file(ip, vm_name, netplan_path)
            if existing_contents is not None and existing_contents != netplan_contents:
                raise NonRetryableError("NETPLAN_FILE_CONFLICT")
            if existing_contents is None:
                _guest_write_file(ip, vm_name, netplan_path, netplan_contents)
            _run_netplan(ip, vm_name, "generate")
            _run_netplan(ip, vm_name, "apply")
        except (NonRetryableError, RetryableError, RuntimeError) as exc:
            _log_guest_error("configuration", vm_name, mac, exc)
    return {"tap_names": [tap], "_already_existed": already_existed}

def detach_port(request: AdapterActionInput) -> dict:
    publico = _publico(request)
    target = _require_host(request.host)
    ip = host.endpoint(target)
    port_id = _param(request, "port_id") or str(request.action.target.id)
    tap = _tap_name(str(port_id))
    bridge = PROVIDER_BRIDGE if publico else f"br-sl-{request.slice_id}"
    vm_name = _domain_for_port(request)
    mode = _guest_mode(request, attach=False)
    _require_guest_mode_state(ip, vm_name, mode)

    if mode == "cleanup":
        existed = _remove_tap(ip, bridge, tap)
        return {"tap_names": [], "_already_absent": not existed}

    interfaces = libvirt_vm.domain_interfaces(
        libvirt_vm._virsh(ip, "dumpxml", vm_name)
    )
    mac = interfaces.get(tap)
    tap_existed = _link_exists_checked(ip, tap)
    if mac is None:
        _remove_tap(ip, bridge, tap)
        return {"tap_names": [], "_already_absent": not tap_existed}

    cloud_init = _cloud_init_enabled(request)
    logger.info("VM %s: cloud_init=%s", vm_name, cloud_init)
    if cloud_init:
        try:
            _guest_ping(ip, vm_name)
            netplan_path = _hot_netplan_path(mac)
            _guest_remove_file(ip, vm_name, netplan_path)
            _run_netplan(ip, vm_name, "apply")
        except (NonRetryableError, RetryableError, RuntimeError) as exc:
            _log_guest_error("cleanup", vm_name, mac, exc)
    libvirt_vm.change_device(
        ip,
        vm_name,
        libvirt_vm.interface_xml(port_id=str(port_id), mac=mac),
        attach=False,
    )
    _remove_tap(ip, bridge, tap)
    return {"tap_names": []}


def delete_link(request: AdapterActionInput) -> dict:
    publico = _publico(request)
    targets = request.hosts or ([request.host] if request.host else [])
    if not targets:
        return {"tap_names": [], "_already_absent": True}
    slice_id = request.slice_id

    if publico:
        return {"tap_names": []}

    bridge = f"br-sl-{slice_id}"
    vx = f"vx-{slice_id}"
    vw = f"vw-{slice_id}"

    for target in targets:
        _validate_host(target)
        ip = host.endpoint(target)
        with _trunk_lock(ip, slice_id):
            bridges = host.run(ip, ["ovs-vsctl", "list-br"]).splitlines()
            if bridge not in bridges:
                continue
            ports_raw = host.run(ip, ["ovs-vsctl", "list-ports", bridge])
            remaining = [p.strip() for p in ports_raw.splitlines() if p.strip()]
            if any(port not in (vx, vw) for port in remaining):
                continue
            host.run(ip, ["ovs-vsctl", "--if-exists", "del-port", PROVIDER_BRIDGE, vw])
            host.run(ip, ["ovs-vsctl", "--if-exists", "del-port", bridge, vx])
            if _link_exists_checked(ip, vx):
                host.run(ip, ["ip", "link", "del", vx])
            host.run(ip, ["ovs-vsctl", "--if-exists", "del-br", bridge])

    return {"tap_names": []}


def delete_slice_network(request: AdapterActionInput) -> dict:
    """Destroy de todo el slice: br-sl-<slice> y veth hacia provider.

    Opcional: params["port_ids"] para borrar tambien los taps (los de
    br-sl caen con el bridge pero el dispositivo tap persiste en el host;
    los publicos viven en br-provider).
    """
    if not request.hosts:
        raise NonRetryableError("HOSTS_MISSING")
    slice_id = request.slice_id
    bridge = f"br-sl-{slice_id}"
    vx = f"vx-{slice_id}"
    vw = f"vw-{slice_id}"
    port_ids = request.action.params.get("port_ids") or []
    for target in request.hosts:
        _validate_host(target)
        ip = host.endpoint(target)
        host.run(ip, ["ovs-vsctl", "--if-exists", "del-port", PROVIDER_BRIDGE, vw])
        host.run(ip, ["ovs-vsctl", "--if-exists", "del-br", bridge])
        if _link_exists(ip, vx):
            host.run(ip, ["ip", "link", "del", vx])  # borra ambos extremos
        for port_id in port_ids:
            tap = _tap_name(str(port_id))
            host.run(ip, ["ovs-vsctl", "--if-exists", "del-port", PROVIDER_BRIDGE, tap])
            if _link_exists(ip, tap):
                host.run(ip, ["ip", "link", "del", tap])
    return {"tap_names": []}


def _validate_host(target: AdapterHost) -> None:
    try:
        ipaddress.ip_address(target.management_ip)
    except ValueError as exc:
        raise NonRetryableError("HOST_INVALID") from exc


def _require_host(target: AdapterHost | None) -> AdapterHost:
    if target is None:
        raise NonRetryableError("HOST_MISSING")
    _validate_host(target)
    return target