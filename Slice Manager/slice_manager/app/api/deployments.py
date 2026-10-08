from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from contracts.base import ContractModel
from pydantic import Field

from contracts.registry import ApprovalDecision, DeploymentCreated
from slice_manager.app.api.dependencies import get_current_user_id, get_slice_service
from slice_manager.app.domain.slice_service import SliceService

router = APIRouter()


class ApprovalRequest(ContractModel):
    approved: bool
    reason: str | None = Field(default=None, max_length=2000)


@router.post(
    "/slices/{slice_id}/versions/{version_number}/deploy",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeploymentCreated,
)
async def request_deploy(
    slice_id: int,
    version_number: int,
    response: Response,
    user_id: Annotated[int, Depends(get_current_user_id)],
    service: Annotated[SliceService, Depends(get_slice_service)],
) -> DeploymentCreated:
    created = await service.request_initial_deploy(
        slice_id=slice_id,
        version_number=version_number,
        requester_id=user_id,
    )
    if created.state == "REJECTED":
        response.status_code = status.HTTP_409_CONFLICT
    return created


@router.post("/deployments/{deployment_id}/approve")
async def decide_deployment_approval(
    deployment_id: int,
    body: ApprovalRequest,
    operator_id: Annotated[int, Depends(get_current_user_id)],
    service: Annotated[SliceService, Depends(get_slice_service)],
) -> dict[str, str]:
    decision = ApprovalDecision(
        approved=body.approved,
        operator_id=operator_id,
        reason=body.reason,
    )
    result = await service.approve_deployment(
        deployment_id=deployment_id,
        decision=decision,
    )
    return {"deployment_id": str(deployment_id), "status": result.value}
