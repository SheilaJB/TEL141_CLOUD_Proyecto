"""VMs gestionadas con libvirt (virsh) en lugar de lanzar QEMU a mano."""

import hashlib
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from . import config, host
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


def interface_xml(*, port_id: str, mac: str) -> str:
    return (
        "<interface type='ethernet'>\n"
        f"  <mac address={quoteattr(mac)}/>\n"
        f"  <target dev={quoteattr(_tap_name(port_id))} managed='no'/>\n"
        "  <model type='virtio'/>\n"
        "</interface>\n"
    )


def domain_exists(address: str, vm_name: str) -> bool:
    names = _virsh(address, "list", "--all", "--name").splitlines()
    return vm_name in names


def domain_interfaces(xml: str) -> dict[str, str]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise NonRetryableError("DOMAIN_XML_INVALID") from exc
    result: dict[str, str] = {}
    for interface in root.findall("./devices/interface"):
        target = interface.find("target")
        mac = interface.find("mac")
        if target is not None and target.get("dev") and mac is not None and mac.get("address"):
            result[target.get("dev", "")] = mac.get("address", "")
    return result


def change_device(
    address: str, vm_name: str, xml: str, *, attach: bool
) -> None:
    operation = "attach" if attach else "detach"
    remote = f"/tmp/ad-libvirt-{operation}-{vm_name}.xml"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".xml", delete=False) as file:
        file.write(xml)
        local = Path(file.name)
    try:
        host.upload(address, local, remote)
        _virsh(
            address,
            f"{operation}-device",
            vm_name,
            remote,
            "--live",
            "--config",
        )
    finally:
        local.unlink(missing_ok=True)
        host.run(address, ["rm", "-f", remote])


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
    max_memory_mb: int | None = None,
    max_vcpus: int | None = None,
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
    for nic in sorted(nics, key=lambda item: int(item["nic_index"])):
        port_id = str(nic.get("port_id") or nic.get("id"))
        # type=ethernet + managed=no: usa el TAP que ya creo attach_port
        interface = interface_xml(port_id=port_id, mac=str(nic["mac"]))
        interfaces.append("\n".join(f"    {line}" for line in interface.splitlines()))
    memory_limit = max(ram_mb, max_memory_mb or config.HEADROOM_MEMORY_MAX_MB)
    vcpu_limit = max(vcpus, max_vcpus or config.HEADROOM_VCPU_MAX)
    devices = "\n".join(
        [
            *disks,
            *interfaces,
            f"    <graphics type='vnc' port='{QEMU_VNC_BASE_PORT + vnc_display}' "
            "autoport='no' listen='0.0.0.0'/>",
            "    <channel type='unix'>\n"
            "      <target type='virtio' name='org.qemu.guest_agent.0'/>\n"
            "    </channel>",
            "    <video>\n      <model type='vga'/>\n    </video>",
            "    <serial type='pty'/>",
            "    <console type='pty'/>",
        ]
    )
    return (
        "<domain type='kvm'>\n"
        f"  <name>{escape(vm_name)}</name>\n"
        f"  <uuid>{escape(vm_id)}</uuid>\n"
        f"  <memory unit='MiB'>{int(memory_limit)}</memory>\n"
        f"  <currentMemory unit='MiB'>{int(ram_mb)}</currentMemory>\n"
        f"  <vcpu placement='static' current='{int(vcpus)}'>{int(vcpu_limit)}</vcpu>\n"
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
    return domain_exists(address, vm_name) and _virsh(address, "domstate", vm_name).strip() == "running"


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
    if domain_exists(address, vm_name) and _virsh(address, "domstate", vm_name).strip() != "shut off":
        _virsh(address, "destroy", vm_name)
        if _virsh(address, "domstate", vm_name).strip() != "shut off":
            raise RetryableError("VM_STILL_RUNNING", vm_name)


def undefine(address: str, vm_name: str) -> None:
    if domain_exists(address, vm_name):
        _virsh(address, "undefine", vm_name)
