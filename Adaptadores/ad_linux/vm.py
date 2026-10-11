"""Idempotent VM and cloud-init host operations."""

import hashlib
import json
import tempfile
import time
from pathlib import Path

from contracts.adapter import AdapterActionInput

from . import config, host
from .cloudinit import render_meta_data, render_network_config, render_user_data
from .errors import NonRetryableError, RetryableError


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
    params = request.action.params
    vm_id = str(request.action.target.id)
    vm_name = "vm-" + vm_id[:8]
    image = params.get("image") or {}
    flavor = params.get("flavor") or {}
    ## ---
    print(f"Creating VM {vm_name} on host {target.management_ip} with params: {params}")
    print(f"Image: {image}. Base path: {config.STORAGE_ROOT / 'base' / str(image.get('reference'))}")
    print(f"Flavor details - RAM: {flavor.get('ram_mb')} MB, vCPUs: {flavor.get('vcpu')}, Disk: {flavor.get('disk_mb')} MB")
    ## ---
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
    seed = str(config.STORAGE_ROOT / "seeds" / f"{vm_id}.iso")
    if not _exists(target.management_ip, base):
        raise NonRetryableError("BASE_MISSING")
    _ensure_overlay(target.management_ip, base, overlay, disk_mb)
    seed_hash = _ensure_seed(
        target.management_ip,
        seed,
        params,
        vm_id,
        str(params.get("hostname") or vm_name),
    )
    if _running(target.management_ip, vm_name):
        return {
            "pid": _pid(target.management_ip, vm_name),
            "vnc_port": _vnc_port(vnc_display),
            "state": "RUNNING",
            "overlay_ref": overlay,
            "seed_sha256": seed_hash,
            "_already_existed": True,
        }
    _ensure_taps(target.management_ip, params.get("nics") or [])
    _start_qemu(
        target.management_ip,
        vm_name,
        overlay,
        seed,
        params,
        target.management_ip,
        ram_mb,
        vcpus,
    )
    if not _wait_for_start(target.management_ip, vm_name, vnc_display):
        raise RetryableError("QEMU_START_FAILED")
    return {
        "pid": _pid(target.management_ip, vm_name),
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
        info = json.loads(
            host.run(address, ["qemu-img", "info", "--output=json", overlay])
        )
        if info.get("backing-filename") != base:
            raise NonRetryableError("OVERLAY_CONFLICT")
        if int(info.get("virtual-size", 0)) < disk_mb * 1024 * 1024:
            raise NonRetryableError("DISK_TOO_SMALL")
        return
    base_info = json.loads(
        host.run(address, ["qemu-img", "info", "--output=json", base])
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
    hostname: str,
) -> str:
    nics = params.get("nics") or []
    image = params.get("image") or {}
    network = render_network_config(nics, cloud_init_net=image.get("cloud_init_net", True))
    user = render_user_data(hostname)
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


def _start_qemu(
    address: str,
    vm_name: str,
    overlay: str,
    seed: str,
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
        "-drive", f"file={seed},format=raw,media=cdrom,readonly=on",
    ]
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
            f"{management_ip}:{params['vnc_display']}",
            "-vga",
            "std",
            "-daemonize",
            "-pidfile",
            _run_path(vm_name),
        ]
    )
    host.run(address, args)


def _wait_for_start(address: str, vm_name: str, display: int) -> bool:
    deadline = time.monotonic() + 10
    expected_port = 30000 + display
    while time.monotonic() < deadline:
        if _running(address, vm_name):
            sockets = host.run(address, ["ss", "-ltn"])
            if f":{expected_port} " in sockets or f":{expected_port}\n" in sockets:
                return True
        time.sleep(0.5)
    return False


def _running(address: str, vm_name: str) -> bool:
    try:
        pid = host.run(address, ["cat", _run_path(vm_name)])
        host.run(address, ["kill", "-0", pid])
    except RuntimeError:
        return False
    return True


def _pid(address: str, vm_name: str) -> int:
    return int(host.run(address, ["cat", _run_path(vm_name)]))


def _vnc_port(display: int) -> int:
    return 30000 + display


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
