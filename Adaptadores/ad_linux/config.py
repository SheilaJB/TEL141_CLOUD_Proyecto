"""Local configuration for a Linux adapter worker."""

import os
from pathlib import Path

SSH_USER = os.getenv("ADAPTER_SSH_USER", "ubuntu")
SSH_KEY = os.getenv("ADAPTER_SSH_KEY")
KNOWN_HOSTS = os.getenv("ADAPTER_KNOWN_HOSTS")
SSH_TIMEOUT = int(os.getenv("ADAPTER_SSH_TIMEOUT", "30"))
COMMAND_TIMEOUT = int(os.getenv("ADAPTER_COMMAND_TIMEOUT", "60"))
STORAGE_ROOT = Path(os.getenv("ADAPTER_STORAGE_ROOT", "/home/ubuntu/storage"))
RUN_ROOT = Path(os.getenv("ADAPTER_RUN_ROOT", "/home/ubuntu/run/vms"))
PUBLIC_VID = int(os.getenv("ADAPTER_PUBLIC_VID", "4000"))
# "libvirt" (virsh) o "qemu" (QEMU lanzado a mano, modo anterior)
VM_BACKEND = os.getenv("ADAPTER_VM_BACKEND", "qemu").strip().lower()
if VM_BACKEND not in {"libvirt", "qemu"}:
    raise ValueError(f"ADAPTER_VM_BACKEND invalido: {VM_BACKEND!r}")
