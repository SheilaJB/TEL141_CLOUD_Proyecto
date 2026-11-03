"""Action handlers for the Linux adapter."""

from contracts.adapter import AdapterActionInput

from .destroy import destroy_slice_host
from .network import attach_port, create_link, detach_port, delete_link
from .vm import create_vm, delete_vm

__all__ = [
    "attach_port",
    "create_link",
    "create_vm",
    "destroy_slice_host",
    "delete_vm",
    "detach_port",
    "delete_link",
]
