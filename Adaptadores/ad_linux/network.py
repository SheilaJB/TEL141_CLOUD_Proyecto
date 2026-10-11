"""Idempotent Linux/OVS network operations."""

import hashlib
import ipaddress

from contracts.adapter import AdapterActionInput, AdapterHost

from . import config, host
from .errors import NonRetryableError


def _param(request: AdapterActionInput, name: str):
    return request.action.params.get(name)


def _tap_name(port_id: str) -> str:
    return "tp-" + hashlib.sha1(port_id.encode()).hexdigest()[:10]


def create_link(request: AdapterActionInput) -> dict:
    if not request.hosts:
        raise NonRetryableError("HOSTS_MISSING")
    slice_id = request.slice_id
    bridge = f"br-sl-{slice_id}"
    params = request.action.params
    remote = len(request.hosts) > 1 or bool(params.get("remote"))
    vlan_stag = params.get("vlan_stag")
    if remote and vlan_stag is None:
        raise NonRetryableError("LINK_PARAMS_MISSING")
    for target in request.hosts:
        _validate_host(target)
        host.run(target.management_ip, ["ovs-vsctl", "--may-exist", "add-br", "br-provider"])
        host.run(target.management_ip, ["ovs-vsctl", "--may-exist", "add-br", bridge])
        if remote:
            vx = f"vx-{slice_id}"
            vw = f"vw-{slice_id}"
            try:
                peer = next(
                    (candidate for candidate in request.hosts if candidate.server_id != target.server_id),
                    None,
                )
                if peer is None:
                    raise NonRetryableError("HOSTS_MISSING")
                host.run(
                    target.management_ip,
                    ["ip", "link", "add", vx, "type", "veth", "peer", "name", vw],
                )
            except RuntimeError:
                pass
            host.run(target.management_ip, ["ip", "link", "set", vx, "up"])
            host.run(target.management_ip, ["ip", "link", "set", vw, "up"])
            host.run(
                target.management_ip,
                ["ovs-vsctl", "--may-exist", "add-port", bridge, vx],
            )
            host.run(
                target.management_ip,
                [
                    "ovs-vsctl",
                    "--may-exist",
                    "add-port",
                    "br-provider",
                    vw,
                    "--",
                    "set",
                    "interface",
                    vw,
                    "type=dot1q-tunnel",
                    f"options:remote_ip={peer.management_ip}",
                    f"options:key={vlan_stag}",
                ],
            )
    return {"tap_names": []}


def attach_port(request: AdapterActionInput) -> dict:
    target = _require_host(request.host)
    port_id = _param(request, "port_id") or str(request.action.target.id)
    tap = _tap_name(str(port_id))
    bridge = "br-provider" if _param(request, "publico") else f"br-sl-{request.slice_id}"
    if _param(request, "publico"):
        tag = str(_param(request, "public_vid") or config.PUBLIC_VID)
    else:
        vlan = _param(request, "vlan_ctag")
        if vlan is None:
            raise NonRetryableError("PORT_PARAMS_MISSING")
        tag = str(vlan)
    try:
        host.run(target.management_ip, ["ip", "link", "show", tap])
        already_existed = True
    except RuntimeError:
        host.run(target.management_ip, ["ip", "tuntap", "add", "dev", tap, "mode", "tap"])
        already_existed = False
    host.run(target.management_ip, ["ovs-vsctl", "--may-exist", "add-port", bridge, tap])
    host.run(target.management_ip, ["ovs-vsctl", "set", "port", tap, "tag=" + tag])
    host.run(target.management_ip, ["ip", "link", "set", tap, "up"])
    return {"tap_names": [tap], "_already_existed": already_existed}


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
