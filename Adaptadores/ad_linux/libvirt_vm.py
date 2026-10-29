"""VMs gestionadas con libvirt (virsh) en lugar de lanzar QEMU a mano."""

import hashlib
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from . import host
from .errors import NonRetryableError, RetryableError

VIRSH = ["virsh", "-c", "qemu:///system"]
QEMU_VNC_BASE_PORT = 5900
ERR_DETAIL_MAX = 500


def _virsh(address: str, *args: str) -> str:
    try:
        return host.run(address, [*VIRSH, *args])
    except RuntimeError as exc:
        low = str(exc).lower()
        # libvirt no instalado o el servicio caido
        if "command not found" in low or "failed to connect to the hypervisor" in low:
            raise NonRetryableError("LIBVIRT_UNAVAILABLE", str(exc)[:ERR_DETAIL_MAX]) from exc
        raise


def _tap_name(port_id: str) -> str:
    # mismo nombre que crea attach_port en network.py
    return "tp-" + hashlib.sha1(port_id.encode()).hexdigest()[:10]


def domain_xml(
    *,
    vm_name: str,
    vm_id: str,
    overlay: str,
    seed: str | None,
    nics: list[dict],
    ram_mb: int,
    vcpus: int,
    vnc_display: int,
) -> str:
    disks = [
        "    <disk type='file' device='disk'>\n"
        "      <driver name='qemu' type='qcow2'/>\n"
        f"      <source file={quoteattr(overlay)}/>\n"
        "      <target dev='vda' bus='virtio'/>\n"
        "    </disk>"
    ]
    if seed:
        disks.append(
            "    <disk type='file' device='cdrom'>\n"
            "      <driver name='qemu' type='raw'/>\n"
            f"      <source file={quoteattr(seed)}/>\n"
            "      <target dev='hdc' bus='ide'/>\n"
            "      <readonly/>\n"
            "    </disk>"
        )
    interfaces = []
    for nic in nics:
        port_id = str(nic.get("port_id") or nic.get("id"))
        # type=ethernet + managed=no: usa el TAP que ya creo attach_port
        interfaces.append(
            "    <interface type='ethernet'>\n"
            f"      <mac address={quoteattr(str(nic['mac']))}/>\n"
            f"      <target dev={quoteattr(_tap_name(port_id))} managed='no'/>\n"
            "      <model type='virtio'/>\n"
            "    </interface>"
        )
    devices = "\n".join(
        [
            *disks,
            *interfaces,
            f"    <graphics type='vnc' port='{QEMU_VNC_BASE_PORT + vnc_display}' "
            "autoport='no' listen='0.0.0.0'/>",
            "    <video>\n      <model type='vga'/>\n    </video>",
            "    <serial type='pty'/>",
            "    <console type='pty'/>",
        ]
    )
    return (
        "<domain type='kvm'>\n"
        f"  <name>{escape(vm_name)}</name>\n"
        f"  <uuid>{escape(vm_id)}</uuid>\n"
        f"  <memory unit='MiB'>{int(ram_mb)}</memory>\n"
        f"  <vcpu>{int(vcpus)}</vcpu>\n"
        "  <os>\n    <type arch='x86_64'>hvm</type>\n    <boot dev='hd'/>\n  </os>\n"
        "  <features>\n    <acpi/>\n    <apic/>\n  </features>\n"
        "  <cpu mode='host-passthrough'/>\n"
        "  <on_poweroff>destroy</on_poweroff>\n"
        "  <on_reboot>restart</on_reboot>\n"
        "  <on_crash>destroy</on_crash>\n"
        "  <devices>\n"
        f"{devices}\n"
        "  </devices>\n"
        "</domain>\n"
    )


def running(address: str, vm_name: str) -> bool:
    try:
        return _virsh(address, "domstate", vm_name).strip() == "running"
    except RuntimeError:
        return False  # el dominio no existe


def pid(address: str, vm_name: str) -> int:
    return int(host.run(address, ["cat", f"/run/libvirt/qemu/{vm_name}.pid"]))


def start(
    address: str,
    *,
    vm_name: str,
    vm_id: str,
    overlay: str,
    seed: str | None,
    nics: list[dict],
    ram_mb: int,
    vcpus: int,
    vnc_display: int,
) -> None:
    xml = domain_xml(
        vm_name=vm_name,
        vm_id=vm_id,
        overlay=overlay,
        seed=seed,
        nics=nics,
        ram_mb=ram_mb,
        vcpus=vcpus,
        vnc_display=vnc_display,
    )
    remote = f"/tmp/ad-libvirt-{vm_name}.xml"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".xml", delete=False) as f:
        f.write(xml)
        local = Path(f.name)
    try:
        host.upload(address, local, remote)
    finally:
        local.unlink(missing_ok=True)
    try:
        # define reemplaza la config si el dominio ya existia apagado
        _virsh(address, "define", remote)
    except RuntimeError as exc:
        raise NonRetryableError("LIBVIRT_DEFINE_FAILED", str(exc)[:ERR_DETAIL_MAX]) from exc
    finally:
        try:
            host.run(address, ["rm", "-f", remote])
        except RuntimeError:
            pass
    try:
        _virsh(address, "start", vm_name)
    except RuntimeError as exc:
        if "already active" in str(exc).lower():
            return
        _raise_start_error(str(exc))


def _raise_start_error(stderr: str) -> None:
    detail = stderr.strip()[:ERR_DETAIL_MAX]
    print(f"virsh start failed: {detail}")
    low = detail.lower()
    if "could not access kvm" in low or "kvm is not available" in low:
        raise NonRetryableError("KVM_UNAVAILABLE", detail)
    if "address already in use" in low or "failed to bind" in low:
        raise NonRetryableError("VNC_PORT_IN_USE", detail)
    if "device or resource busy" in low:
        raise NonRetryableError("TAP_BUSY", detail)
    if "no such device" in low or "cannot get interface" in low:
        raise NonRetryableError("TAP_MISSING", detail)
    if "permission denied" in low:
        raise NonRetryableError("LIBVIRT_PERMISSION_DENIED", detail)
    raise RetryableError("LIBVIRT_START_FAILED", detail)


def destroy(address: str, vm_name: str) -> None:
    """Apaga a la fuerza; sirve aunque el dominio no exista o libvirt no este."""
    try:
        _virsh(address, "destroy", vm_name)
    except (RuntimeError, NonRetryableError):
        pass


def undefine(address: str, vm_name: str) -> None:
    try:
        _virsh(address, "undefine", vm_name)
    except (RuntimeError, NonRetryableError):
        pass
