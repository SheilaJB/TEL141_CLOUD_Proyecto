from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from contracts.placement import (
    PlacementReserveRequest,
    PlacementReserveResponse,
    PlacementRollbackRequest,
    PlacementRollbackResponse,
)
from contracts.registry import (
    ApprovalState,
    DeploymentContext,
    FinishDeployment,
    RoundFacts,
    RoundFactsResult,
    StartDeployment,
)
from slice_manager.app.api.dependencies import get_slice_service, require_internal_service
from slice_manager.app.domain.slice_service import SliceService

router = APIRouter(dependencies=[Depends(require_internal_service)])


@router.post(
    "/placements/reserve",
    response_model=PlacementReserveResponse,
)
async def reserve_placement(
    body: PlacementReserveRequest,
    service: Annotated[SliceService, Depends(get_slice_service)],
) -> PlacementReserveResponse:
    return await service.reserve_placement(body)


@router.post(
    "/placements/rollback",
    response_model=PlacementRollbackResponse,
)
async def rollback_placement(
    body: PlacementRollbackRequest,
    service: Annotated[SliceService, Depends(get_slice_service)],
) -> PlacementRollbackResponse:
    return await service.rollback_placement(body)


@router.get("/deployments/{deployment_id}/plan", response_model=DeploymentContext)
async def get_plan(
    deployment_id: int,
    service: Annotated[SliceService, Depends(get_slice_service)],
) -> DeploymentContext:
    return await service.get_plan(deployment_id)


@router.get(
    "/deployments/{deployment_id}/approval",
    response_model=ApprovalState,
)
async def get_approval_state(
    deployment_id: int,
    service: Annotated[SliceService, Depends(get_slice_service)],
) -> ApprovalState:
    return await service.get_approval_state(deployment_id)


@router.post("/deployments/{deployment_id}/start", status_code=204)
async def start_deployment(
    deployment_id: int,
    body: StartDeployment,
    service: Annotated[SliceService, Depends(get_slice_service)],
) -> None:
    await service.start_deployment(deployment_id, body)


@router.post(
    "/deployments/{deployment_id}/rounds/{round_num}/facts",
    response_model=RoundFactsResult,
)
async def record_round_facts(
    deployment_id: int,
    round_num: int,
    body: RoundFacts,
    service: Annotated[SliceService, Depends(get_slice_service)],
) -> RoundFactsResult:
    if body.round_num != round_num:
        raise HTTPException(status_code=422, detail="round number does not match payload")
    return await service.record_round_facts(deployment_id, body)


@router.post("/deployments/{deployment_id}/finish", status_code=204)
async def finish_deployment(
    deployment_id: int,
    body: FinishDeployment,
    service: Annotated[SliceService, Depends(get_slice_service)],
) -> None:
    await service.finish_deployment(deployment_id, body)
