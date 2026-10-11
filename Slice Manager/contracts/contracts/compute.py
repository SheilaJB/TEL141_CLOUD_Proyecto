from uuid import UUID

from contracts.base import ContractModel


class ComputePlacement(ContractModel):
    node_id: UUID
    server_id: int


class ComputeAllocation(ContractModel):
    node_id: UUID
    vnc_display: int
