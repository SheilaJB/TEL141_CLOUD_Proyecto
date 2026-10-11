"""Action handlers for the Linux adapter.

The handlers are intentionally unimplemented in this phase. They are the only
boundary where host execution will be added later.
"""

from contracts.adapter import AdapterActionInput

from .destroy import destroy_slice_host
from .network import attach_port, create_link
from .vm import create_vm

__all__ = ["attach_port", "create_link", "create_vm", "destroy_slice_host"]
