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


class PlacementResult(ContractModel):
    deployment_id: int = Field(gt=0)
    status: PlacementStatus = PlacementStatus.RESERVED
    server_by_node: dict[UUID, int] = Field(default_factory=dict)

    @classmethod
    def from_reserve_response(
        cls, response: PlacementReserveResponse
    ) -> "PlacementResult":
        return cls(
            deployment_id=response.deployment_id,
            status=response.status,
            server_by_node={
                allocation.node_id: allocation.server_id
                for allocation in response.allocations
            },
        )


class PlacementRollbackRequest(ContractModel):
    deployment_id: int = Field(gt=0)


class PlacementRollbackResponse(ContractModel):
    deployment_id: int = Field(gt=0)
    status: PlacementStatus = PlacementStatus.RELEASED
