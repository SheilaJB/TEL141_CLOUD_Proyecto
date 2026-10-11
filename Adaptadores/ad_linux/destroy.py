"""Idempotent slice-host destruction."""

from contracts.adapter import AdapterActionInput

from . import config, host
from .errors import NonRetryableError


def destroy_slice_host(request: AdapterActionInput) -> dict:
    if request.host is None:
        raise NonRetryableError("HOST_MISSING")
    address = request.host.management_ip
    params = request.action.params
    destroyed_vms = []
    for vm in params.get("vms", []):
        vm_id = str(vm["vm_id"])
        vm_name = "vm-" + vm_id[:8]
        try:
            pid = host.run(address, ["cat", str(config.RUN_ROOT / f"{vm_name}.pid")])
            host.run(address, ["kill", "-TERM", pid])
            host.run(address, ["sleep", "1"])
            try:
                host.run(address, ["kill", "-0", pid])
            except RuntimeError:
                pass
            else:
                host.run(address, ["kill", "-KILL", pid])
        except RuntimeError:
            pass
        for path in (
            config.RUN_ROOT / f"{vm_name}.pid",
            config.STORAGE_ROOT / "overlays" / f"{vm_id}.qcow2",
            config.STORAGE_ROOT / "seeds" / f"{vm_id}.iso",
        ):
            host.run(address, ["rm", "-f", str(path)])
        destroyed_vms.append(vm_id)
    for tap in params.get("tap_names", []):
        host.run(address, ["ovs-vsctl", "--if-exists", "del-port", str(tap)])
        try:
            host.run(address, ["ip", "link", "del", str(tap)])
        except RuntimeError:
            pass
    vx = f"vx-{request.slice_id}"
    vw = f"vw-{request.slice_id}"
    host.run(address, ["ovs-vsctl", "--if-exists", "del-port", vx])
    host.run(address, ["ovs-vsctl", "--if-exists", "del-port", vw])
    try:
        host.run(address, ["ip", "link", "del", vx])
    except RuntimeError:
        pass
    slice_id = request.slice_id
    host.run(address, ["ovs-vsctl", "--if-exists", "del-br", f"br-sl-{slice_id}"])
    return {"destroyed_vms": destroyed_vms, "destroyed_bridge": True}
