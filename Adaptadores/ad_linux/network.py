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
import threading

from contracts.adapter import AdapterActionInput, AdapterHost

from . import config, host
from .errors import NonRetryableError

PROVIDER_BRIDGE = "br-provider"
STAG_MIN = 100
STAG_MAX = 3999  # 4000 reservado para trafico publico


_trunk_locks: dict[tuple[str, str], threading.Lock] = {}
_trunk_locks_guard = threading.Lock()


def _trunk_lock(ip: str, slice_id) -> threading.Lock:
    """Un lock por (host, slice): los create_link del mismo slice corren en paralelo."""
    key = (ip, str(slice_id))
    with _trunk_locks_guard:
        return _trunk_locks.setdefault(key, threading.Lock())


def _param(request: AdapterActionInput, name: str):
    return request.action.params.get(name)


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
            _ensure_provider(target.management_ip)
        return {"tap_names": []}
    if remote:
        if vlan_stag is None:
            raise NonRetryableError("LINK_PARAMS_MISSING")
        vlan_stag = int(vlan_stag)
        if not STAG_MIN <= vlan_stag <= STAG_MAX:
            raise NonRetryableError("VLAN_STAG_INVALID")
    for target in request.hosts:
        _validate_host(target)
        ip = target.management_ip
        bridge = _ensure_slice_bridge(ip, slice_id)
        if remote:
            _ensure_provider(ip)
            _ensure_trunk(ip, slice_id, bridge, vlan_stag)
    return {"tap_names": []}


def attach_port(request: AdapterActionInput) -> dict:
    target = _require_host(request.host)
    ip = target.management_ip
    port_id = _param(request, "port_id") or str(request.action.target.id)
    tap = _tap_name(str(port_id))
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
    already_existed = _link_exists(ip, tap)
    if not already_existed:
        host.run(ip, ["ip", "tuntap", "add", "dev", tap, "mode", "tap"])
    host.run(ip, ["ovs-vsctl", "--may-exist", "add-port", bridge, tap])
    host.run(ip, ["ovs-vsctl", "set", "port", tap, "tag=" + tag])
    host.run(ip, ["ip", "link", "set", tap, "up"])
    return {"tap_names": [tap], "_already_existed": already_existed}


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
        ip = target.management_ip
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