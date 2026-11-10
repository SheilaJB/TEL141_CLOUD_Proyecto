"""Idempotent VM and cloud-init host operations."""

import hashlib
import json
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from contracts.adapter import AdapterActionInput

from . import config, host, libvirt_vm
from .cloudinit import render_meta_data, render_network_config, render_seed_user_data
from .destroy import _stop_vm
from .errors import NonRetryableError, RetryableError

# QEMU abre el VNC en 5900 + display (-vnc <ip>:N). Es el puerto REAL de escucha.
QEMU_VNC_BASE_PORT = 5900
# Valor que se reporta como fact "vnc_port" (convención del proyecto, no es
# donde escucha QEMU).
VNC_FACT_BASE_PORT = 30000
START_TIMEOUT_S = 10


def _param(request: AdapterActionInput, name: str):
    return request.action.params.get(name)


def _require_host(request: AdapterActionInput):
    if request.host is None:
        raise NonRetryableError("HOST_MISSING")
    return request.host


def create_vm(request: AdapterActionInput) -> dict:
    target = _require_host(request)
    address = host.endpoint(target)
    params = request.action.params
    vm_id = str(request.action.target.id)
    vm_name = "vm-" + vm_id[:8]
    image = params.get("image") or {}
    flavor = params.get("flavor") or {}
    # 1. Validar params (sin tocar el host).
    try:
        vnc_display = int(params["vnc_display"])
        ram_mb = int(flavor["ram_mb"])
        vcpus = int(flavor["vcpu"])
        disk_mb = int(flavor["disk_mb"])
    except (KeyError, TypeError, ValueError) as exc:
        raise NonRetryableError("VM_PARAMS_INCOMPLETE") from exc
    if not 1 <= vnc_display <= 99 or ram_mb <= 0 or vcpus <= 0 or disk_mb <= 0:
        raise NonRetryableError("VM_PARAMS_INVALID")
    base_ref = image.get("reference")
    if not base_ref:
        raise NonRetryableError("BASE_MISSING")
    base = str(config.STORAGE_ROOT / "base" / str(base_ref))
    overlay = str(config.STORAGE_ROOT / "overlay" / f"{vm_id}.qcow2")
    # Ruta del seed solo si aplica a esta VM; sin seed no hay ISO ni hash.
    use_seed = image.get("cloud_init")
    if not isinstance(use_seed, bool):
        raise NonRetryableError("IMAGE_CLOUD_INIT_INVALID")
    print(f"Creating VM {vm_name} on host {address} with params: {params}")
    print(f"Image: {image}. Base path: {config.STORAGE_ROOT / 'base' / str(image.get('reference'))}")
    print(f"Flavor details - RAM: {flavor.get('ram_mb')} MB, vCPUs: {flavor.get('vcpu')}, Disk: {flavor.get('disk_mb')} MB")
    print(f"VM {vm_name}: cloud_init={use_seed}")
    seed = str(config.STORAGE_ROOT / "seeds" / f"{vm_id}.iso") if use_seed else None

    # 2. Ya corre: éxito sin tocar overlay, seed ni taps.
    if _running(address, vm_name):
        return {
            "pid": _pid(address, vm_name),
            "vnc_port": _vnc_port(vnc_display),
            "state": "RUNNING",
            "overlay_ref": overlay,
            "seed_sha256": _stored_seed_hash(address, seed) if seed else None,
            "_already_existed": True,
        }

    # 3. No corre (VM nueva o intento previo a medias): crear lo que falte
    #    y reutilizar lo que exista.
    if not _exists(address, base):
        raise NonRetryableError("BASE_MISSING")
    _ensure_overlay(address, base, overlay, disk_mb)
    seed_hash = None
    if seed:
        seed_hash = _ensure_seed(address, seed, params, vm_id, hostname=params.get("hostname"))
    _ensure_taps(address, params.get("nics") or [])

    # 4. Arrancar y verificar.
    libvirt_vm.start(
        address,
        vm_name=vm_name,
        vm_id=vm_id,
        overlay=overlay,
        seed=seed,
        nics=params.get("nics") or [],
        ram_mb=ram_mb,
        vcpus=vcpus,
        vnc_display=vnc_display,
    )
    _wait_for_start(address, vm_name, vnc_display)
    return {
        "pid": _pid(address, vm_name),
        "vnc_port": _vnc_port(vnc_display),
        "state": "RUNNING",
        "overlay_ref": overlay,
        "seed_sha256": seed_hash,
    }

def delete_vm(request: AdapterActionInput) -> dict:
    target = _require_host(request)
    address = host.endpoint(target)
    vm_id = str(_param(request, "vm_id") or request.action.target.id)
    vm_name = "vm-" + vm_id[:8]

    was_running = _running(address, vm_name)
    overlay = str(config.STORAGE_ROOT / "overlay" / f"{vm_id}.qcow2")
    had_overlay = _exists(address, overlay)

    _stop_vm(address, vm_name)
    libvirt_vm.undefine(address, vm_name)

    for path in (
        overlay,
        str(config.STORAGE_ROOT / "seeds" / f"{vm_id}.iso"),
        str(config.STORAGE_ROOT / "seeds" / f"{vm_id}.iso.sha256"),
        str(config.STORAGE_ROOT / "seeds" / f"{vm_id}.iso.input.sha256"),
    ):
        host.run(address, ["rm", "-f", path])

    already_absent = not was_running and not had_overlay
    return {"_already_absent": already_absent}


def _resize_flavor(params: dict, key: str) -> tuple[int, int, int]:
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


def _memory_mib(element: ET.Element | None) -> int:
    if element is None or element.text is None:
        raise NonRetryableError("DOMAIN_XML_INVALID")
    units = {"b": 1, "bytes": 1, "kib": 1024, "kb": 1000, "mib": 1024**2, "mb": 1000**2, "gib": 1024**3, "gb": 1000**3}
    unit = element.get("unit", "KiB").lower()
    if unit not in units:
        raise NonRetryableError("DOMAIN_XML_INVALID")
    try:
        byte_count = int(element.text) * units[unit]
    except ValueError as exc:
        raise NonRetryableError("DOMAIN_XML_INVALID") from exc
    if byte_count % (1024**2):
        raise NonRetryableError("DOMAIN_XML_INVALID")
    return byte_count // (1024**2)


def _domain_resources(xml: str) -> tuple[int, int, int, int | None]:
    try:
        root = ET.fromstring(xml)
        cpu = root.find("vcpu")
        if cpu is None or cpu.text is None:
            raise NonRetryableError("DOMAIN_XML_INVALID")
        maximum_cpu = int(cpu.text)
        current_cpu = int(cpu.get("current", cpu.text))
        memory = root.find("currentMemory")
        if memory is None:
            memory = root.find("memory")
        current_memory = _memory_mib(memory)
        max_memory_element = root.find("maxMemory")
        if max_memory_element is None:
            max_memory_element = root.find("memory")
        max_memory = _memory_mib(max_memory_element) if max_memory_element is not None else None
    except (ET.ParseError, ValueError) as exc:
        raise NonRetryableError("DOMAIN_XML_INVALID") from exc
    return current_cpu, current_memory, maximum_cpu, max_memory


def _define_existing_xml(address: str, vm_name: str, xml: str) -> None:
    remote = f"/tmp/ad-libvirt-resize-{vm_name}.xml"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".xml", delete=False) as file:
        file.write(xml)
        local = Path(file.name)
    try:
        host.upload(address, local, remote)
        libvirt_vm._virsh(address, "define", remote)
    finally:
        local.unlink(missing_ok=True)
        host.run(address, ["rm", "-f", remote])


def resize_vm(request: AdapterActionInput) -> dict:
    address = host.endpoint(_require_host(request))
    params = request.action.params
    before = _resize_flavor(params, "before_flavor")
    after = _resize_flavor(params, "flavor")
    hot_resize = params.get("hot_resize", False)
    if not isinstance(hot_resize, bool):
        raise NonRetryableError("VM_PARAMS_INVALID")
    if after[2] < before[2]:
        raise NonRetryableError("DISK_SHRINK")
    if after[0] > config.HEADROOM_VCPU_MAX or after[1] > config.HEADROOM_MEMORY_MAX_MB:
        raise NonRetryableError("HOT_CEILING_EXCEEDED")
    vm_id = str(request.action.target.id)
    vm_name = "vm-" + vm_id[:8]
    overlay = str(config.STORAGE_ROOT / "overlay" / f"{vm_id}.qcow2")
    virsh = libvirt_vm._virsh
    state = virsh(address, "domstate", vm_name).strip()
    running = state == "running"
    if state not in {"running", "shut off"}:
        raise NonRetryableError("VM_STATE_INVALID", state)
    persistent_xml = virsh(address, "dumpxml", vm_name, "--inactive")
    persistent = _domain_resources(persistent_xml)
    live = _domain_resources(virsh(address, "dumpxml", vm_name)) if running else None
    info = json.loads(host.run(address, ["qemu-img", "info", "--force-share", "--output=json", overlay]))
    virtual_size = int(info["virtual-size"])
    target_size = after[2] * 1024 * 1024
    if virtual_size > target_size:
        raise NonRetryableError("DISK_SHRINK")
    if before[2] == after[2] and virtual_size != target_size:
        raise NonRetryableError("DISK_SIZE_MISMATCH")
    path = "hot" if hot_resize and before[2] == after[2] else "cold"
    facts = {"state": "RUNNING", "path": path, "vcpu": after[0], "ram_mb": after[1], "disk_mb": after[2]}
    if running and live is not None and live[:2] == after[:2] and persistent[:2] == after[:2] and virtual_size == target_size:
        return {**facts, "_already_existed": True}

    if path == "hot":
        if not running or live is None:
            raise NonRetryableError("VM_STATE_INVALID", state)
        if any(resources[2] < after[0] or resources[3] is None or resources[3] < after[1] for resources in (live, persistent)):
            raise NonRetryableError("HOT_HEADROOM_MISSING")
        virsh(address, "setvcpus", vm_name, str(after[0]), "--live", "--config")
        virsh(address, "setmem", vm_name, f"{after[1]}M", "--live", "--config")
    else:
        _stop_vm(address, vm_name)
        if virsh(address, "domstate", vm_name).strip() != "shut off":
            raise RetryableError("VM_STILL_RUNNING", vm_name)
        if virtual_size < target_size:
            host.run(address, ["qemu-img", "resize", overlay, f"{after[2]}M"])
        if persistent[2] < after[0]:
            virsh(address, "setvcpus", vm_name, str(after[0]), "--maximum", "--config")
        virsh(address, "setvcpus", vm_name, str(after[0]), "--config")
        root = ET.fromstring(persistent_xml)
        maximum_memory = _memory_mib(root.find("memory"))
        if maximum_memory < after[1]:
            virsh(address, "setmaxmem", vm_name, f"{after[1]}M", "--config")
        virsh(address, "setmem", vm_name, f"{after[1]}M", "--config")
        _define_existing_xml(address, vm_name, virsh(address, "dumpxml", vm_name, "--inactive"))
        virsh(address, "start", vm_name)
    if virsh(address, "domstate", vm_name).strip() != "running":
        raise RetryableError("QEMU_START_FAILED", vm_name)
    final_live = _domain_resources(virsh(address, "dumpxml", vm_name))
    final_config = _domain_resources(virsh(address, "dumpxml", vm_name, "--inactive"))
    final_disk = json.loads(host.run(address, ["qemu-img", "info", "--force-share", "--output=json", overlay]))
    if final_live[:2] != after[:2] or final_config[:2] != after[:2] or int(final_disk["virtual-size"]) != target_size:
        raise RetryableError("RESIZE_NOT_APPLIED", vm_name)
    return facts


def _exists(address: str, path: str) -> bool:
    try:
        host.run(address, ["test", "-s", path])
    except RuntimeError:
        return False
    return True


def _ensure_overlay(address: str, base: str, overlay: str, disk_mb: int) -> None:
    host.run(
        address,
        [
            "install", "-d",
            "-o", config.SSH_USER, "-g", config.SSH_USER, "-m", "0755",
            str(Path(overlay).parent),
        ],
    )
    if _exists(address, overlay):
        # --force-share: no falla si otro proceso tiene el overlay abierto.
        info = json.loads(
            host.run(
                address,
                ["qemu-img", "info", "--force-share", "--output=json", overlay],
            )
        )
        if info.get("backing-filename") != base:
            raise NonRetryableError("OVERLAY_CONFLICT")
        if int(info.get("virtual-size", 0)) < disk_mb * 1024 * 1024:
            raise NonRetryableError("DISK_TOO_SMALL")
        return
    base_info = json.loads(
        host.run(
            address,
            ["qemu-img", "info", "--force-share", "--output=json", base],
        )
    )
    if int(base_info.get("virtual-size", 0)) > disk_mb * 1024 * 1024:
        raise NonRetryableError("DISK_TOO_SMALL")
    host.run(
        address,
        ["qemu-img", "create", "-f", "qcow2", "-b", base, "-F", "qcow2", overlay, f"{disk_mb}M"],
    )


def _ensure_seed(
    address: str,
    seed: str,
    params: dict,
    vm_id: str,
    hostname: str | None = "ubuntu",
) -> str:
    nics = params.get("nics") or []
    if not hostname:
        raise NonRetryableError("VM_PARAMS_INCOMPLETE")
    network = render_network_config(nics)
    user = render_seed_user_data(hostname)
    meta = render_meta_data(vm_id, hostname)
    contents = {"network-config": network, "user-data": user, "meta-data": meta}
    digest = hashlib.sha256(
        json.dumps(contents, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if _exists(address, seed + ".input.sha256") and host.run(
        address, ["cat", seed + ".input.sha256"]
    ) == digest and _exists(address, seed):
        return host.run(address, ["sha256sum", seed]).split()[0]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, value in contents.items():
            (root / name).write_text(value, encoding="utf-8")
        remote_root = f"/tmp/ad-linux-{hashlib.sha1(seed.encode()).hexdigest()[:12]}"
        host.run(
            address,
            [
                "install", "-d",
                "-o", config.SSH_USER, "-g", config.SSH_USER, "-m", "0755",
                remote_root,
                str(Path(seed).parent),
            ],
        )
        for name in contents:
            host.upload(address, root / name, f"{remote_root}/{name}")
        host.run(
            address,
            [
                "genisoimage",
                "-output",
                seed,
                "-volid",
                "cidata",
                "-joliet",
                "-rock",
                f"{remote_root}/meta-data",
                f"{remote_root}/user-data",
                f"{remote_root}/network-config",
            ],
        )
        host.run(address, ["rm", "-rf", remote_root])
    observed = host.run(address, ["sha256sum", seed]).split()[0]
    for suffix, value in ((".input.sha256", digest), (".sha256", observed)):
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False
        ) as digest_file:
            digest_file.write(value)
            digest_path = Path(digest_file.name)
        try:
            host.upload(address, digest_path, seed + suffix)
        finally:
            digest_path.unlink(missing_ok=True)
    return observed


def _stored_seed_hash(address: str, seed: str) -> str | None:
    """Hash guardado del seed (best effort) para una VM que ya corre."""
    try:
        return host.run(address, ["cat", seed + ".sha256"]) or None
    except RuntimeError:
        return None


def _wait_for_start(address: str, vm_name: str, display: int) -> None:
    """Espera proceso vivo y VNC escuchando en 5900+display; lanza si no."""
    deadline = time.monotonic() + START_TIMEOUT_S
    port = QEMU_VNC_BASE_PORT + display
    alive = False
    while time.monotonic() < deadline:
        if _running(address, vm_name):
            alive = True
            if _listening(address, port):
                return
        time.sleep(0.5)
    if alive:
        raise RetryableError("VNC_NOT_LISTENING", f"port {port}")
    raise RetryableError("QEMU_START_FAILED", f"{vm_name} is not running")


def _listening(address: str, port: int) -> bool:
    for line in host.run(address, ["ss", "-ltn"]).splitlines():
        cols = line.split()
        if len(cols) >= 4 and cols[3].endswith(f":{port}"):
            return True
    return False


def _running(address: str, vm_name: str) -> bool:
    return libvirt_vm.running(address, vm_name)


def _pid(address: str, vm_name: str) -> int:
    return libvirt_vm.pid(address, vm_name)


def _vnc_port(display: int) -> int:
    return VNC_FACT_BASE_PORT + display


def _ensure_taps(address: str, nics: list[dict]) -> None:
    for nic in nics:
        port_id = nic.get("port_id") or nic.get("id")
        if not port_id:
            raise NonRetryableError("NIC_INCOMPLETE")
        tap_name = "tp-" + hashlib.sha1(str(port_id).encode()).hexdigest()[:10]
        try:
            host.run(address, ["ip", "link", "show", tap_name])
        except RuntimeError as exc:
            raise NonRetryableError("TAP_MISSING") from exc