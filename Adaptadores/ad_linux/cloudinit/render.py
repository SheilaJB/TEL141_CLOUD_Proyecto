"""Pure cloud-init text renderers.

Inputs are already resolved by SM/NM. These functions do not inspect files,
environment variables, databases, or host state.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from ..errors import NonRetryableError


def _required_nic_fields(nic: Mapping[str, Any]) -> tuple[str, str, int]:
    mac = nic.get("mac")
    ip = nic.get("ip")
    nic_index = nic.get("nic_index")
    if not mac or not ip or nic_index is None:
        raise NonRetryableError("NIC_INCOMPLETE")
    address = str(ip)
    if "/" not in address:
        prefix = nic.get("prefijo")
        if prefix is None:
            raise NonRetryableError("NIC_INCOMPLETE")
        address = f"{address}/{prefix}"
    return str(mac), address, int(nic_index)


def _yaml_scalar(value: object) -> str:
    text = str(value).replace("'", "''")
    return f"'{text}'"


def render_network_config(
    nics: Sequence[Mapping[str, Any]],
) -> str:
    """Render netplan v2 config with one static interface per resolved NIC."""
    if not nics:
        return "version: 2\nethernets: {}\n"
    resolved = [(nic, _required_nic_fields(nic)) for nic in nics]
    lines = ["version: 2", "ethernets:"]
    for nic, (mac, ip, nic_index) in sorted(resolved, key=lambda item: item[1][2]):
        lines.extend(
            [
                f"  nic{nic_index}:",
                "    match:",
                f"      macaddress: {_yaml_scalar(mac)}",
                "    dhcp4: false",
                "    addresses:",
                f"      - {_yaml_scalar(ip)}",
            ]
        )
        if nic.get("publico") is True:
            lines.append("    mtu: 1400")
            gateway = nic.get("gateway")
            if gateway:
                lines.extend([f"    gateway4: {_yaml_scalar(gateway)}"])
            lines.extend(
                [
                    "    nameservers:",
                    "      addresses:",
                    "        - 8.8.8.8",
                    "        - 1.1.1.1",
                ]
            )
    return "\n".join(lines) + "\n"


def render_user_data(
    hostname: str,
    *,
    username: str | None = None,
    password: str | None = None,
) -> str:
    """Render cloud-config without implicit credentials."""
    lines = ["#cloud-config", f"hostname: {_yaml_scalar(hostname)}"]
    if username is not None or password is not None:
        if not username or password is None:
            raise NonRetryableError("CREDENTIALS_INCOMPLETE")
        lines.extend(
            [
                "users:",
                f"  - name: {_yaml_scalar(username)}",
                "    lock_passwd: false",
                "chpasswd:",
                "  expire: false",
                "  list:",
                f"    - {_yaml_scalar(f'{username}:{password}')}",
            ]
        )
    return "\n".join(lines) + "\n"


def render_seed_user_data(hostname: str) -> str:
    return "\n".join(
        [
            "#cloud-config",
            f"hostname: {_yaml_scalar(hostname)}",
            "users:",
            "  - default",
            "ssh_pwauth: true",
            "chpasswd:",
            "  expire: false",
            "  users:",
            "    - {name: ubuntu, password: ubuntu, type: text}",
            "bootcmd:",
            '  - "systemctl mask --now apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service unattended-upgrades.service || true"',
            '  - "chmod -x /usr/lib/update-notifier/update-motd-updates-available || true"',
            "apt:",
            "  conf: |",
            '    DPkg::Lock::Timeout "120";',
            "package_update: true",
            "packages:",
            "  - qemu-guest-agent",
            "runcmd:",
            "  - systemctl start qemu-guest-agent",
            "",
        ]
    )


def render_meta_data(vm_id: str, hostname: str) -> str:
    """Render instance metadata without performing any I/O."""
    return "\n".join(
        [
            f"instance-id: {_yaml_scalar(vm_id)}",
            f"local-hostname: {_yaml_scalar(hostname)}",
            "",
        ]
    )
