from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from contracts.base import ContractModel


class PlacementStatus(StrEnum):
    RESERVED = "RESERVED"
    RELEASED = "RELEASED"


class PlacementReserveRequest(ContractModel):
    deployment_id: int = Field(gt=0)


class PlacementAllocation(ContractModel):
    node_id: UUID
    server_id: int = Field(gt=0)


class PlacementReserveResponse(ContractModel):
    deployment_id: int = Field(gt=0)
    status: PlacementStatus = PlacementStatus.RESERVED
    allocations: list[PlacementAllocation]

    @model_validator(mode="after")
    def validate_unique_allocations(self) -> "PlacementReserveResponse":
        node_ids = [allocation.node_id for allocation in self.allocations]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("placement response contains duplicate node allocations")
        return self


class PlacementRollbackRequest(ContractModel):
    deployment_id: int = Field(gt=0)


class PlacementRollbackResponse(ContractModel):
    deployment_id: int = Field(gt=0)
    status: PlacementStatus = PlacementStatus.RELEASED
