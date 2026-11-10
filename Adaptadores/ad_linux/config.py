"""Local configuration for a Linux adapter worker."""

import os
from pathlib import Path

SSH_USER = os.getenv("ADAPTER_SSH_USER", "ubuntu")
GATEWAY_IP = os.getenv("ADAPTER_GATEWAY_IP", "10.20.11.250")
SSH_KEY = os.getenv("ADAPTER_SSH_KEY")
KNOWN_HOSTS = os.getenv("ADAPTER_KNOWN_HOSTS")
SSH_TIMEOUT = int(os.getenv("ADAPTER_SSH_TIMEOUT", "30"))
COMMAND_TIMEOUT = int(os.getenv("ADAPTER_COMMAND_TIMEOUT", "60"))
STORAGE_ROOT = Path(os.getenv("ADAPTER_STORAGE_ROOT", "/home/ubuntu/storage"))
PUBLIC_VID = int(os.getenv("ADAPTER_PUBLIC_VID", "4000"))
HEADROOM_VCPU_MAX = int(os.environ["HEADROOM_VCPU_MAX"])
HEADROOM_MEMORY_MAX_MB = int(os.environ["HEADROOM_MEMORY_MAX_MB"])
if HEADROOM_VCPU_MAX <= 0 or HEADROOM_MEMORY_MAX_MB <= 0:
    raise ValueError("headroom ceilings must be positive")
