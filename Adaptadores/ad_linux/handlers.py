"""Action handlers for the Linux adapter."""

from contracts.adapter import AdapterActionInput

from .destroy import destroy_slice_host
from .network import attach_port, create_link, delete_link, detach_port
from .vm import create_vm, delete_vm, resize_vm

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
