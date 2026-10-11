"""Shared, side-effect-free contracts for the Orchestrator."""

from contracts.errors import ErrorCode, ErrorResponse
from contracts.plan import Action, ActionOp, DeploymentPlan, Intent, PlanType
from contracts.placement import (
    PlacementAllocation,
    PlacementResult,
    PlacementReserveRequest,
    PlacementReserveResponse,
    PlacementRollbackRequest,
    PlacementRollbackResponse,
)
from contracts.network import (
    NetworkAllocation,
    NetworkLinkAllocation,
    NetworkPortAllocation,
    NetworkRollbackResult,
)
from contracts.slices import (
    CreateSliceRequest,
    CreateVersionRequest,
    PlanPreview,
    SliceCreated,
    ValidationPreview,
    VersionCreated,
)
from contracts.compute import ComputeAllocation, ComputePlacement
from contracts.servers import ServerHost

__all__ = [
    "Action",
    "ActionOp",
    "CreateSliceRequest",
    "CreateVersionRequest",
    "ComputeAllocation",
    "ComputePlacement",
    "DeploymentPlan",
    "ErrorCode",
    "ErrorResponse",
    "Intent",
    "PlacementAllocation",
    "PlacementResult",
    "PlacementReserveRequest",
    "PlacementReserveResponse",
    "PlacementRollbackRequest",
    "PlacementRollbackResponse",
    "NetworkAllocation",
    "NetworkLinkAllocation",
    "NetworkPortAllocation",
    "NetworkRollbackResult",
    "PlanPreview",
    "PlanType",
    "SliceCreated",
    "ValidationPreview",
    "VersionCreated",
    "ServerHost",
]
