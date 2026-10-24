"""Idempotent slice-host destruction.

Usa las MISMAS rutas que vm.py (pidfile sin extension en RUN_ROOT y discos en
STORAGE_ROOT/overlay). Una VM solo se reporta en destroyed_vms cuando se
verifico que su proceso QEMU ya no existe; si sobrevive se lanza un error
reintentable y el Destroy no marca nada como borrado.
"""

import time

from contracts.adapter import AdapterActionInput

from . import config, host
from .errors import NonRetryableError, RetryableError
from .vm import _run_path

TERM_WAIT_SECONDS = 10
KILL_WAIT_SECONDS = 5


def _qemu_pids(address: str, vm_name: str) -> list[str]:
    """PIDs de QEMU de esta VM, por pidfile y por linea de comando.

    La busqueda por cmdline cubre pidfiles borrados o desactualizados.
    """
    pids: set[str] = set()
    try:
        pid = host.run(address, ["cat", _run_path(vm_name)]).strip()
        if pid.isdigit():
            host.run(address, ["grep", "-qa", vm_name, f"/proc/{pid}/cmdline"])
            pids.add(pid)
    except RuntimeError:
        pass
    try:
        # El espacio final evita que vm-1234abcd coincida con vm-1234abcdX.
        found = host.run(address, ["pgrep", "-f", f"qemu-system-x86_64 -name {vm_name} "])
        pids.update(item for item in found.split() if item.isdigit())
    except RuntimeError:
        pass  # pgrep sale con 1 cuando no hay coincidencias
    return sorted(pids)


def _alive(address: str, pid: str) -> bool:
    try:
        host.run(address, ["kill", "-0", pid])
    except RuntimeError:
        return False
    return True


def _wait_dead(address: str, pids: list[str], seconds: int) -> list[str]:
    deadline = time.monotonic() + seconds
    alive = list(pids)
    while alive:
        alive = [pid for pid in alive if _alive(address, pid)]
        if not alive or time.monotonic() >= deadline:
            break
        time.sleep(1)
    return alive


def _stop_vm(address: str, vm_name: str) -> None:
    pids = _qemu_pids(address, vm_name)
    if not pids:
        return
    for pid in pids:
        try:
            host.run(address, ["kill", "-TERM", pid])
        except RuntimeError:
            pass
    alive = _wait_dead(address, pids, TERM_WAIT_SECONDS)
    for pid in alive:
        try:
            host.run(address, ["kill", "-KILL", pid])
        except RuntimeError:
            pass
    alive = _wait_dead(address, alive, KILL_WAIT_SECONDS)
    if alive:
        raise RetryableError("VM_STILL_RUNNING", f"{vm_name} pids {alive}")


def destroy_slice_host(request: AdapterActionInput) -> dict:
    if request.host is None:
        raise NonRetryableError("HOST_MISSING")
    address = request.host.management_ip
    params = request.action.params
    destroyed_vms = []
    for vm in params.get("vms", []):
        vm_id = str(vm["vm_id"])
        vm_name = "vm-" + vm_id[:8]
        _stop_vm(address, vm_name)
        for path in (
            _run_path(vm_name),
            str(config.STORAGE_ROOT / "overlay" / f"{vm_id}.qcow2"),
            str(config.STORAGE_ROOT / "seeds" / f"{vm_id}.iso"),
            # Rutas antiguas de este mismo archivo; rm -f no falla si no existen.
            str(config.RUN_ROOT / f"{vm_name}.pid"),
            str(config.STORAGE_ROOT / "overlays" / f"{vm_id}.qcow2"),
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
