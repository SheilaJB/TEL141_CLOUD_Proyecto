"""Action handlers for the OpenStack adapter.

Each handler receives a complete AdapterActionInput and returns a dict of
facts (plus the optional `_already_existed` / `_already_absent` markers),
exactly like the Linux adapter.
"""

from .network import attach_port, create_link, delete_link, detach_port
from .vm import create_vm, delete_vm, destroy_slice_host, resize_vm

__all__ = [
    "attach_port",
    "create_link",
    "create_vm",
    "delete_link",
    "delete_vm",
    "destroy_slice_host",
    "detach_port",
    "resize_vm",
]
