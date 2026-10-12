"""Idempotent VM and cloud-init host operations."""

import hashlib
import json
import tempfile
import time
from pathlib import Path

from contracts.adapter import AdapterActionInput

from . import config, host
from .cloudinit import render_network_config
from .errors import NonRetryableError, RetryableError

# Flag fijo: False = ninguna VM usa seed cloud-init.
# True = se genera el ISO "cidata" SOLO con network-config (sin hostname ni
# credenciales; cada imagen conserva sus usuarios por defecto) y solo para
# imagenes que lo soportan (ver _seed_enabled).
USE_SEED = True
# Imagenes sin cloud-init real: no reciben seed. Se detectan por el nombre del
# archivo base, porque "cloud_init_net" no llega hoy en params["image"].
NO_SEED_IMAGE_HINTS = ("cirros-0.6.2-x86_64-disk.img",)

# QEMU abre el VNC en 5900 + display (-vnc <ip>:N). Es el puerto REAL de escucha.
QEMU_VNC_BASE_PORT = 5900
# Valor que se reporta como fact "vnc_port" (convención del proyecto, no es
# donde escucha QEMU).
VNC_FACT_BASE_PORT = 30000
START_TIMEOUT_S = 10
ERR_DETAIL_MAX = 500


def _param(request: AdapterActionInput, name: str):
    return request.action.params.get(name)


def _require_host(request: AdapterActionInput):
    if request.host is None:
        raise NonRetryableError("HOST_MISSING")
    return request.host


def _run_path(name: str) -> str:
    return str(config.RUN_ROOT / name)


def create_vm(request: AdapterActionInput) -> dict:
    target = _require_host(request)
    address = target.management_ip
    params = request.action.params
    vm_id = str(request.action.target.id)
    vm_name = "vm-" + vm_id[:8]
    image = params.get("image") or {}
    flavor = params.get("flavor") or {}
    ## ---
    print(f"Creating VM {vm_name} on host {address} with params: {params}")
    print(f"Image: {image}. Base path: {config.STORAGE_ROOT / 'base' / str(image.get('reference'))}")
    print(f"Flavor details - RAM: {flavor.get('ram_mb')} MB, vCPUs: {flavor.get('vcpu')}, Disk: {flavor.get('disk_mb')} MB")
    ## ---

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
    use_seed = _seed_enabled(params, base_ref)
    print(f"use_seed={use_seed}")
    seed = str(config.STORAGE_ROOT / "seeds" / f"{vm_id}.iso") if use_seed else None

    # 2. Ya corre: éxito sin tocar overlay, seed ni taps (QEMU los tiene abiertos).
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
    _start_qemu(address, vm_name, overlay, seed, params, address, ram_mb, vcpus)
    _wait_for_start(address, vm_name, vnc_display)
    return {
        "pid": _pid(address, vm_name),
        "vnc_port": _vnc_port(vnc_display),
        "state": "RUNNING",
        "overlay_ref": overlay,
        "seed_sha256": seed_hash,
    }


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


def _seed_enabled(params: dict, base_ref) -> bool:
    """El seed (solo network-config) aplica si hay NICs y la imagen lo soporta."""
    if not USE_SEED:
        return False
    if not (params.get("nics") or []):
        return False
    image = params.get("image") or {}
    if image.get("cloud_init_net") is False:
        return False
    name = str(base_ref).lower()
    return not any(hint in name for hint in NO_SEED_IMAGE_HINTS)


def _ensure_seed(
    address: str,
    seed: str,
    params: dict,
    vm_id: str,
    hostname: str | None = "ubuntu",
) -> str:
    nics = params.get("nics") or []
    network = render_network_config(nics, cloud_init_net=True)
    user = (
        "#cloud-config\n"
        f"hostname: {hostname}\n"
        "users:\n"
        "  - default\n"
        "ssh_pwauth: true\n"
        "chpasswd:\n"
        "  expire: false\n"
        "  list: |\n"
        "    ubuntu:ubuntu\n"
    )
    meta = f"instance-id: {vm_id}\n"
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


def _start_qemu(
    address: str,
    vm_name: str,
    overlay: str,
    seed: str | None,
    params: dict,
    management_ip: str,
    ram_mb: int,
    vcpus: int,
) -> None:
    nics = params.get("nics") or []
    args = [
        "qemu-system-x86_64", "-name", vm_name, "-enable-kvm", "-cpu", "host",
        "-m", str(ram_mb), "-smp", str(vcpus),
        "-drive", f"file={overlay},format=qcow2,if=virtio",
    ]
    if seed:
        args.extend(["-drive", f"file={seed},format=raw,media=cdrom,readonly=on"])
    for nic in nics:
        port_id = str(nic.get("port_id") or nic.get("id"))
        tap_name = "tp-" + hashlib.sha1(port_id.encode()).hexdigest()[:10]
        args.extend(
            ["-netdev", f"tap,id=n{nic['nic_index']},ifname={tap_name},script=no,downscript=no"]
        )
        args.extend(
            [
                "-device",
                f"virtio-net-pci,netdev=n{nic['nic_index']},mac={nic['mac']}",
            ]
        )
    args.extend(
        [
            "-vnc",
            f"0.0.0.0:{params['vnc_display']}",
            "-vga",
            "std",
            "-daemonize",
            "-pidfile",
            _run_path(vm_name),
        ]
    )
    print(f"Starting QEMU for {vm_name}: {' '.join(args)}")
    try:
        host.run(address, args)
    except (RetryableError, NonRetryableError):
        raise  # SSH caído, timeout, etc.: se propagan tal cual
    except RuntimeError as exc:
        _raise_start_error(vm_name, str(exc))


def _raise_start_error(vm_name: str, stderr: str) -> None:
    """Clasifica el stderr de QEMU. Config del entorno = no reintentable."""
    detail = stderr.strip()[:ERR_DETAIL_MAX]
    print(f"QEMU start failed for {vm_name}: {detail}")
    low = detail.lower()
    if "could not access kvm" in low:
        raise NonRetryableError("KVM_UNAVAILABLE")
    if "command not found" in low or "qemu-system-x86_64: no such file" in low:
        raise NonRetryableError("QEMU_BINARY_MISSING")
    if "address already in use" in low or "failed to bind" in low:
        raise NonRetryableError("VNC_PORT_IN_USE")
    if "device or resource busy" in low:
        raise NonRetryableError("TAP_BUSY")
    raise RetryableError("QEMU_START_FAILED", detail)


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
    """Pidfile existe, el proceso vive y es esta VM (evita pid reciclado)."""
    try:
        pid = host.run(address, ["cat", _run_path(vm_name)]).strip()
        if not pid.isdigit():
            return False
        host.run(address, ["kill", "-0", pid])
        host.run(address, ["grep", "-qa", vm_name, f"/proc/{pid}/cmdline"])
    except RuntimeError:
        return False
    return True


def _pid(address: str, vm_name: str) -> int:
    return int(host.run(address, ["cat", _run_path(vm_name)]))


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