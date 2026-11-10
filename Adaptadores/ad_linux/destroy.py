"""Idempotent slice-host destruction using libvirt."""

from contracts.adapter import AdapterActionInput

from . import config, host, libvirt_vm
from .errors import NonRetryableError


def _stop_vm(address: str, vm_name: str) -> None:
    libvirt_vm.destroy(address, vm_name)


def destroy_slice_host(request: AdapterActionInput) -> dict:
    if request.host is None:
        raise NonRetryableError("HOST_MISSING")
    address = host.endpoint(request.host)
    params = request.action.params
    destroyed_vms = []
    for vm in params.get("vms", []):
        vm_id = str(vm["vm_id"])
        vm_name = "vm-" + vm_id[:8]
        _stop_vm(address, vm_name)
        libvirt_vm.undefine(address, vm_name)
        for path in (
            str(config.STORAGE_ROOT / "overlay" / f"{vm_id}.qcow2"),
            str(config.STORAGE_ROOT / "seeds" / f"{vm_id}.iso"),
            str(config.STORAGE_ROOT / "seeds" / f"{vm_id}.iso.sha256"),
            str(config.STORAGE_ROOT / "seeds" / f"{vm_id}.iso.input.sha256"),
        ):
            host.run(address, ["rm", "-f", path])
        destroyed_vms.append(vm_id)
    for tap in params.get("tap_names", []):
        host.run(address, ["ovs-vsctl", "--if-exists", "del-port", str(tap)])
        try:
            host.run(address, ["ip", "link", "del", str(tap)])
        except RuntimeError:
            pass
    slice_id = request.slice_id
    vx = f"vx-{slice_id}"
    vw = f"vw-{slice_id}"
    host.run(address, ["ovs-vsctl", "--if-exists", "del-port", vx])
    host.run(address, ["ovs-vsctl", "--if-exists", "del-port", vw])
    try:
        host.run(address, ["ip", "link", "del", vx])
    except RuntimeError:
        pass
    host.run(address, ["ovs-vsctl", "--if-exists", "del-br", f"br-sl-{slice_id}"])
    return {"destroyed_vms": destroyed_vms, "destroyed_bridge": True}
