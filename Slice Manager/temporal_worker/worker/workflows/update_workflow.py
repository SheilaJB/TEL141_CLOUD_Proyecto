import asyncio
from datetime import timedelta
from uuid import UUID

from contracts.adapter import AdapterActionInput, AdapterActionOutput
from contracts.compute import ComputeAllocation, ComputePlacement
from contracts.errors import ErrorCode, ErrorResponse
from contracts.network import NetworkAllocation
from contracts.placement import (
    PlacementReserveResponse,
    PlacementResult,
    PlacementStatus,
)
from contracts.plan import Action, ActionOp, DeploymentPlan, Executor, IntentKind, PlanType
from contracts.queues import (
    ACTIVITY_BUILD_ADAPTER_ACTIONS,
    ACTIVITY_COMMIT_UPDATE,
    ACTIVITY_COMPUTE_ALLOCATE,
    ACTIVITY_FINALIZE_PLACEMENT,
    ACTIVITY_GET_APPROVAL_STATE,
    ACTIVITY_GET_PLAN,
    ACTIVITY_GET_SERVER_DIRECTORY,
    ACTIVITY_NETWORK_ALLOCATE,
    ACTIVITY_RESERVE_PLACEMENT,
    ACTIVITY_START_DEPLOYMENT,
    COMPUTE_QUEUE,
    LINUX_QUEUE,
    NETWORK_QUEUE,
    ORCHESTRATOR_QUEUE,
    PLACEMENT_QUEUE,
)
from contracts.registry import (
    ApprovalState,
    ApprovalStatus,
    CommitUpdate,
    DeploymentContext,
    DeploymentOutcome,
)
from contracts.servers import ServerHost
from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

from temporal_worker.worker.workflows.common import (
    ADAPTER_ACTIVITY_TIMEOUT,
    ADAPTER_RETRY_POLICY,
    LONG_ADAPTER_ACTIVITY_TIMEOUT,
    PLACEMENT_ACTIVITY_TIMEOUT,
    PLACEMENT_RETRY_POLICY,
    REGISTRY_ACTIVITY_TIMEOUT,
    REGISTRY_RETRY_POLICY,
    DeploymentStepFailed,
    WorkflowHelpers,
)

ADAPTER_ACTIVITIES = {
    ActionOp.CREATE_VM: "create_vm",
    ActionOp.DELETE_VM: "delete_vm",
    ActionOp.RESIZE_VM: "resize_vm",
    ActionOp.CREATE_LINK: "create_link",
    ActionOp.DELETE_LINK: "delete_link",
    ActionOp.ATTACH_PORT: "attach_port",
    ActionOp.DETACH_PORT: "detach_port",
}
LONG_ACTIONS = {ActionOp.CREATE_VM, ActionOp.DELETE_VM, ActionOp.RESIZE_VM}


@workflow.defn(name="UpdateWorkflow")
class UpdateWorkflow(WorkflowHelpers):
    def __init__(self) -> None:
        self._approval: tuple[bool, int | None, str | None] | None = None

    @workflow.signal
    def approval_decision(
        self,
        approved: bool,
        operator_id: int | None,
        reason: str | None,
    ) -> None:
        self._approval = (approved, operator_id, reason)

    @workflow.run
    async def run(self, deployment_id: int) -> str:
        context: DeploymentContext | None = None
        started = False
        applied: list[Action] = []

        try:
            approval = await self._execute_activity(
                ACTIVITY_GET_APPROVAL_STATE,
                deployment_id,
                result_type=ApprovalState,
                task_queue=ORCHESTRATOR_QUEUE,
            )
            if approval.status == ApprovalStatus.PENDING:
                try:
                    await workflow.wait_condition(
                        lambda: self._approval is not None,
                        timeout=timedelta(seconds=approval.timeout_seconds),
                    )
                except asyncio.TimeoutError:
                    await self._finish(
                        deployment_id,
                        DeploymentOutcome.REJECTED,
                        ErrorResponse(
                            code=ErrorCode.CONFLICT,
                            message="Manual approval expired",
                            retryable=False,
                        ),
                    )
                    return DeploymentOutcome.REJECTED.value
                if self._approval is not None and not self._approval[0]:
                    await self._finish(
                        deployment_id,
                        DeploymentOutcome.REJECTED,
                        ErrorResponse(
                            code=ErrorCode.CONFLICT,
                            message=self._approval[2] or "Deployment rejected by operator",
                            retryable=False,
                        ),
                        operator_id=self._approval[1],
                    )
                    return DeploymentOutcome.REJECTED.value
            elif approval.status != ApprovalStatus.APPROVED:
                await self._finish(
                    deployment_id,
                    DeploymentOutcome.REJECTED,
                    ErrorResponse(
                        code=ErrorCode.CONFLICT,
                        message=approval.reason or "Deployment approval is not valid",
                        retryable=False,
                    ),
                )
                return DeploymentOutcome.REJECTED.value

            context = await self._execute_activity(
                ACTIVITY_GET_PLAN,
                deployment_id,
                result_type=DeploymentContext,
                task_queue=ORCHESTRATOR_QUEUE,
            )
            await self._execute_activity(
                ACTIVITY_START_DEPLOYMENT,
                deployment_id,
                task_queue=ORCHESTRATOR_QUEUE,
            )
            started = True
            if context.plan.type != PlanType.UPDATE:
                failure = ErrorResponse(
                    code=ErrorCode.CONFLICT,
                    message=f"UpdateWorkflow received a {context.plan.type.value} plan",
                    retryable=False,
                )
                await self._finish(deployment_id, DeploymentOutcome.FAILED, failure)
                return DeploymentOutcome.FAILED.value

            reserve_task = None
            if context.plan.reservation.allocate or context.plan.reservation.resize:
                reserve_task = self._execute_activity(
                    ACTIVITY_RESERVE_PLACEMENT,
                    deployment_id,
                    result_type=PlacementReserveResponse,
                    task_queue=PLACEMENT_QUEUE,
                    start_to_close_timeout=PLACEMENT_ACTIVITY_TIMEOUT,
                    retry_policy=PLACEMENT_RETRY_POLICY,
                )
            network_task = self._execute_activity(
                ACTIVITY_NETWORK_ALLOCATE,
                deployment_id,
                result_type=NetworkAllocation,
                task_queue=NETWORK_QUEUE,
                start_to_close_timeout=REGISTRY_ACTIVITY_TIMEOUT,
                retry_policy=REGISTRY_RETRY_POLICY,
            )
            if reserve_task is None:
                reservation = None
                network_allocation = await network_task
            else:
                reservation, network_allocation = await asyncio.gather(
                    reserve_task, network_task
                )

            allocation_by_node: dict[UUID, int] = {}
            if reservation is not None:
                if (
                    reservation.deployment_id != deployment_id
                    or reservation.status != PlacementStatus.RESERVED
                ):
                    raise ApplicationError(
                        "Placement returned a reservation for the wrong deployment or status",
                        type=ErrorCode.CONFLICT.value,
                        non_retryable=True,
                    )
                placement_result = PlacementResult.from_reserve_response(reservation)
                allocation_by_node = placement_result.server_by_node
                required_nodes = {
                    item.node_id for item in context.plan.reservation.allocate
                }
                if (
                    required_nodes != set(allocation_by_node)
                    or len(reservation.allocations) != len(required_nodes)
                ):
                    raise ApplicationError(
                        "Placement response does not allocate every planned node",
                        type=ErrorCode.CONFLICT.value,
                        non_retryable=True,
                    )
            else:
                placement_result = PlacementResult(
                    deployment_id=deployment_id,
                    server_by_node={},
                )
            if network_allocation is None:
                raise ApplicationError(
                    "network allocation was not returned",
                    type=ErrorCode.CONFLICT.value,
                    non_retryable=True,
                )

            vnc_by_node: dict[str, int] = {}
            if context.plan.reservation.allocate:
                compute_allocations = await self._execute_activity(
                    ACTIVITY_COMPUTE_ALLOCATE,
                    deployment_id,
                    [
                        ComputePlacement(
                            node_id=item.node_id,
                            server_id=allocation_by_node[item.node_id],
                        )
                        for item in context.plan.reservation.allocate
                    ],
                    result_type=list[ComputeAllocation],
                    task_queue=COMPUTE_QUEUE,
                    start_to_close_timeout=REGISTRY_ACTIVITY_TIMEOUT,
                    retry_policy=PLACEMENT_RETRY_POLICY,
                )
                vnc_by_node = {
                    str(item.node_id): item.vnc_display
                    for item in compute_allocations
                }

            server_directory = await self._execute_activity(
                ACTIVITY_GET_SERVER_DIRECTORY,
                context.slice_id,
                result_type=list[ServerHost],
                task_queue=ORCHESTRATOR_QUEUE,
                retry_policy=REGISTRY_RETRY_POLICY,
            )
            prepared = await self._execute_activity(
                ACTIVITY_BUILD_ADAPTER_ACTIONS,
                deployment_id,
                context.plan,
                placement_result,
                network_allocation,
                server_directory,
                vnc_by_node,
                result_type=list[AdapterActionInput],
                task_queue=ORCHESTRATOR_QUEUE,
                start_to_close_timeout=REGISTRY_ACTIVITY_TIMEOUT,
                retry_policy=REGISTRY_RETRY_POLICY,
            )
            prepared_actions = {item.action.id: item for item in prepared}

            await self.run_rounds(
                deployment_id=deployment_id,
                rounds=context.plan.rounds,
                dispatch=lambda action: self._dispatch_action(
                    prepared_actions.get(action.id)
                ),
                point_of_no_return=context.plan.point_of_no_return_round,
                record_facts=True,
                on_round_success=lambda succeeded: applied.extend(succeeded),
                compensable=False,
            )

            await self._execute_activity(
                ACTIVITY_COMMIT_UPDATE,
                deployment_id,
                self._build_commit_update(context.plan, applied, complete=True),
                task_queue=ORCHESTRATOR_QUEUE,
                retry_policy=REGISTRY_RETRY_POLICY,
            )
            await self._execute_activity(
                ACTIVITY_FINALIZE_PLACEMENT,
                deployment_id,
                task_queue=PLACEMENT_QUEUE,
                start_to_close_timeout=PLACEMENT_ACTIVITY_TIMEOUT,
                retry_policy=PLACEMENT_RETRY_POLICY,
            )
            await self._finish(deployment_id, DeploymentOutcome.COMPLETED)
            return DeploymentOutcome.COMPLETED.value
        except (ActivityError, ApplicationError, DeploymentStepFailed) as exc:
            failure = (
                exc.error
                if isinstance(exc, DeploymentStepFailed)
                else self._error_from_exception(exc)
            )
            if started and context is not None and context.plan.type == PlanType.UPDATE:
                try:
                    await self._execute_activity(
                        ACTIVITY_COMMIT_UPDATE,
                        deployment_id,
                        self._build_commit_update(
                            context.plan,
                            applied,
                            complete=False,
                        ),
                        task_queue=ORCHESTRATOR_QUEUE,
                        retry_policy=REGISTRY_RETRY_POLICY,
                    )
                except (ActivityError, ApplicationError) as commit_exc:
                    workflow.logger.warning(
                        "Could not persist partial update facts before failing the deployment",
                        extra={
                            "deployment_id": deployment_id,
                            "error": str(commit_exc),
                        },
                    )
            await self._finish(deployment_id, DeploymentOutcome.FAILED, failure)
            return DeploymentOutcome.FAILED.value

    async def _dispatch_action(
        self,
        prepared: AdapterActionInput | None,
    ) -> AdapterActionOutput:
        if prepared is None:
            raise ApplicationError(
                "The update plan has an action without a prepared adapter request",
                type=ErrorCode.CONFLICT.value,
                non_retryable=True,
            )
        action = prepared.action
        if action.executor != Executor.ADAPTER:
            raise ApplicationError(
                "Update supports adapter actions only",
                type=ErrorCode.CONFLICT.value,
                non_retryable=True,
            )
        activity_name = ADAPTER_ACTIVITIES.get(action.op)
        if activity_name is None:
            raise ApplicationError(
                f"No adapter activity is defined for {action.op.value}",
                type=ErrorCode.CONFLICT.value,
                non_retryable=True,
            )
        workflow.logger.info(
            "Dispatching update action",
            extra={
                "deployment_id": prepared.deployment_id,
                "workflow_id": workflow.info().workflow_id,
                "idempotency_key": prepared.idempotency_key,
            },
        )
        return await self._execute_activity(
            activity_name,
            prepared,
            result_type=AdapterActionOutput,
            task_queue=LINUX_QUEUE,
            start_to_close_timeout=(
                LONG_ADAPTER_ACTIVITY_TIMEOUT
                if action.op in LONG_ACTIONS
                else ADAPTER_ACTIVITY_TIMEOUT
            ),
            retry_policy=ADAPTER_RETRY_POLICY,
        )

    @staticmethod
    def _build_commit_update(
        plan: DeploymentPlan,
        applied: list[Action],
        *,
        complete: bool,
    ) -> CommitUpdate:
        remove_node_ids = {
            intent.target.id
            for intent in plan.intents
            if intent.target.table == "vms" and intent.kind == IntentKind.REMOVE
        }
        removed: list[UUID] = []
        updated: list[UUID] = []
        detached: list[UUID] = []
        for action in applied:
            if action.op == ActionOp.DELETE_VM and action.target.id in remove_node_ids:
                removed.append(action.target.id)
            elif action.op in {ActionOp.CREATE_VM, ActionOp.RESIZE_VM}:
                updated.append(action.target.id)
            elif action.op == ActionOp.DETACH_PORT:
                detached.append(action.target.id)
        return CommitUpdate(
            removed_node_ids=removed,
            updated_node_ids=updated,
            detached_port_ids=detached,
            complete=complete,
        )
