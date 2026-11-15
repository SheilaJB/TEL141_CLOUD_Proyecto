"""Action handlers for the OpenStack adapter.

Each handler receives a complete AdapterActionInput and returns a dict of
facts (plus the optional `_already_existed` / `_already_absent` markers),
exactly like the Linux adapter.
"""

from contracts.adapter import AdapterActionInput

from .errors import NonRetryableError
from .network import attach_port, create_link, delete_link, detach_port


def _not_implemented(request: AdapterActionInput) -> dict:
    raise NonRetryableError("NOT_IMPLEMENTED", f"{request.action.op} is not implemented yet")


create_vm = _not_implemented
delete_vm = _not_implemented
resize_vm = _not_implemented
destroy_slice_host = _not_implemented

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
