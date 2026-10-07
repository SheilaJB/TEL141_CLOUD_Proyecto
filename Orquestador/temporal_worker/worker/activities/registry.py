from contracts.placement import (
    PlacementReserveRequest,
    PlacementRollbackRequest,
    PlacementReserveResponse,
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
from temporalio import activity
from contracts.queues import (
    ACTIVITY_FINISH_DEPLOYMENT,
    ACTIVITY_GET_APPROVAL_STATE,
    ACTIVITY_GET_PLAN,
    ACTIVITY_RECORD_ROUND_FACTS,
    ACTIVITY_RESERVE_PLACEMENT,
    ACTIVITY_ROLLBACK_PLACEMENT,
    ACTIVITY_START_DEPLOYMENT,
)
from temporal_worker.worker.activities.slice_manager_client import SliceManagerClient


class DeploymentActivities:
    def __init__(
        self,
        slice_manager: SliceManagerClient,
    ) -> None:
        self._slice_manager = slice_manager

    @activity.defn(name=ACTIVITY_GET_PLAN)
    async def get_plan(self, deployment_id: int) -> DeploymentContext:
        activity.logger.info(
            "Loading frozen deployment plan",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": activity.info().workflow_id,
                "idempotency_key": deployment_id,
            },
        )
        return await self._slice_manager.get_plan(deployment_id)

    @activity.defn(name=ACTIVITY_GET_APPROVAL_STATE)
    async def get_approval_state(self, deployment_id: int) -> ApprovalState:
        return await self._slice_manager.get_approval_state(deployment_id)

    @activity.defn(name=ACTIVITY_START_DEPLOYMENT)
    async def start_deployment(self, deployment_id: int) -> None:
        activity.logger.info(
            "Starting deployment state transition",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": activity.info().workflow_id,
                "idempotency_key": f"{deployment_id}:start",
            },
        )
        await self._slice_manager.start_deployment(
            deployment_id,
            StartDeployment(event_id=f"{deployment_id}:start"),
        )

    @activity.defn(name=ACTIVITY_RECORD_ROUND_FACTS)
    async def record_round_facts(
        self, deployment_id: int, facts: RoundFacts
    ) -> RoundFactsResult:
        activity.logger.info(
            "Recording deployment round results",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": activity.info().workflow_id,
                "idempotency_key": facts.event_id,
            },
        )
        result = await self._slice_manager.record_round_facts(deployment_id, facts)
        for warning in result.warnings:
            activity.logger.warning(
                "Deployment facts were not fully persisted",
                extra={"deployment_id": deployment_id, "warning": warning},
            )
        return result

    @activity.defn(name=ACTIVITY_FINISH_DEPLOYMENT)
    async def finish_deployment(
        self, deployment_id: int, finish: FinishDeployment
    ) -> None:
        activity.logger.info(
            "Finishing deployment state transition",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": activity.info().workflow_id,
                "idempotency_key": finish.event_id,
            },
        )
        await self._slice_manager.finish_deployment(deployment_id, finish)

    @activity.defn(name=ACTIVITY_RESERVE_PLACEMENT)
    async def reserve(self, deployment_id: int) -> PlacementReserveResponse:
        activity.logger.info(
            "Requesting local Placement reservation",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": activity.info().workflow_id,
                "idempotency_key": deployment_id,
            },
        )
        return await self._slice_manager.reserve_placement(
            PlacementReserveRequest(deployment_id=deployment_id)
        )

    @activity.defn(name=ACTIVITY_ROLLBACK_PLACEMENT)
    async def rollback_reservation(
        self, deployment_id: int
    ) -> PlacementRollbackResponse:
        activity.logger.info(
            "Requesting local Placement rollback",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": activity.info().workflow_id,
                "idempotency_key": deployment_id,
            },
        )
        return await self._slice_manager.rollback_placement(
            PlacementRollbackRequest(deployment_id=deployment_id)
        )
