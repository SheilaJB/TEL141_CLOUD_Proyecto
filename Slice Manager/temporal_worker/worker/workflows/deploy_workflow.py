import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID

from contracts.adapter import AdapterActionInput, AdapterActionOutput
from contracts.errors import ErrorCode, ErrorResponse
from contracts.plan import Action, ActionOp, Executor
from contracts.network import NetworkAllocation
from contracts.placement import PlacementReserveResponse, PlacementStatus, PlacementResult
from contracts.queues import (
    ACTIVITY_FINISH_DEPLOYMENT,
    ACTIVITY_GET_APPROVAL_STATE,
    ACTIVITY_GET_PLAN,
    ACTIVITY_RECORD_ROUND_FACTS,
    ACTIVITY_RESERVE_PLACEMENT,
    ACTIVITY_ROLLBACK_PLACEMENT,
    ACTIVITY_START_DEPLOYMENT,
    ACTIVITY_NETWORK_ALLOCATE,
    ACTIVITY_NETWORK_ROLLBACK,
    ACTIVITY_BUILD_ADAPTER_ACTIONS,
    ORCHESTRATOR_QUEUE,
)
from contracts.registry import (
    ActionFact,
    ApprovalState,
    ApprovalStatus,
    DeploymentContext,
    DeploymentOutcome,
    FinishDeployment,
    RoundFacts,
    RoundFactsResult,
)
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError


REGISTRY_ACTIVITY_TIMEOUT = timedelta(seconds=30)
PLACEMENT_ACTIVITY_TIMEOUT = timedelta(seconds=45)
ADAPTER_ACTIVITY_TIMEOUT = timedelta(seconds=60)
LONG_ADAPTER_ACTIVITY_TIMEOUT = timedelta(minutes=5)
REGISTRY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=10,
    non_retryable_error_types=[
        ErrorCode.INVALID_SPEC.value,
        ErrorCode.RESOURCE_NOT_FOUND.value,
        ErrorCode.CONFLICT.value,
    ],
)
PLACEMENT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=15),
    maximum_attempts=3,
    non_retryable_error_types=[
        ErrorCode.INVALID_SPEC.value,
        ErrorCode.CAPACITY_EXCEEDED.value,
        ErrorCode.RESOURCE_NOT_FOUND.value,
        ErrorCode.CONFLICT.value,
    ],
)
ADAPTER_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    non_retryable_error_types=[
        ErrorCode.INVALID_SPEC.value,
        ErrorCode.CAPACITY_EXCEEDED.value,
        ErrorCode.RESOURCE_NOT_FOUND.value,
        ErrorCode.CONFLICT.value,
    ],
)
COMPENSATION_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=10,
    non_retryable_error_types=[
        ErrorCode.INVALID_SPEC.value,
        ErrorCode.CAPACITY_EXCEEDED.value,
        ErrorCode.RESOURCE_NOT_FOUND.value,
        ErrorCode.CONFLICT.value,
    ],
)
ADAPTER_ACTIVITIES = {
    ActionOp.CREATE_VM: "create_vm",
    ActionOp.DELETE_VM: "delete_vm",
    ActionOp.START_VM: "start_vm",
    ActionOp.STOP_VM: "stop_vm",
    ActionOp.RESIZE_VM: "resize_vm",
    ActionOp.CREATE_LINK: "create_link",
    ActionOp.DELETE_LINK: "delete_link",
    ActionOp.ATTACH_PORT: "attach_port",
    ActionOp.DETACH_PORT: "detach_port",
}
INVERSE_OPERATIONS = {
    ActionOp.CREATE_VM: ActionOp.DELETE_VM,
    ActionOp.CREATE_LINK: ActionOp.DELETE_LINK,
    ActionOp.ATTACH_PORT: ActionOp.DETACH_PORT,
    ActionOp.START_VM: ActionOp.STOP_VM,
    ActionOp.STOP_VM: ActionOp.START_VM,
}


class DeploymentStepFailed(Exception):
    def __init__(self, error: ErrorResponse) -> None:
        self.error = error
        super().__init__(error.message)


@workflow.defn(name="DeployWorkflow")
class DeployWorkflow:
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
        applied: list[Action] = []
        reservation_attempted = False
        context: DeploymentContext | None = None
        allocation_by_node: dict[UUID, int] = {}
        network_allocation: NetworkAllocation | None = None
        prepared_actions: dict[str, AdapterActionInput] = {}
        reversibility_reached = False

        try:
            approval = await self._get_approval_state(deployment_id)
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
            elif approval.status not in {ApprovalStatus.APPROVED}:
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

            await self._execute_activity(
                ACTIVITY_START_DEPLOYMENT,
                deployment_id,
                task_queue=ORCHESTRATOR_QUEUE,
            )
            context = await self._execute_activity(
                ACTIVITY_GET_PLAN,
                deployment_id,
                result_type=DeploymentContext,
                task_queue=ORCHESTRATOR_QUEUE,
            )
            reserve_task = None
            if context.plan.reservation.allocate:
                reservation_attempted = True
                reserve_task = self._execute_activity(
                    ACTIVITY_RESERVE_PLACEMENT,
                    deployment_id,
                    result_type=PlacementReserveResponse,
                    task_queue=ORCHESTRATOR_QUEUE,
                    start_to_close_timeout=PLACEMENT_ACTIVITY_TIMEOUT,
                    retry_policy=PLACEMENT_RETRY_POLICY,
                )
            network_task = self._execute_activity(
                ACTIVITY_NETWORK_ALLOCATE,
                deployment_id,
                result_type=NetworkAllocation,
                task_queue=ORCHESTRATOR_QUEUE,
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
            prepared = await self._execute_activity(
                ACTIVITY_BUILD_ADAPTER_ACTIONS,
                deployment_id,
                context.plan,
                placement_result,
                network_allocation,
                result_type=list[AdapterActionInput],
                task_queue=ORCHESTRATOR_QUEUE,
                start_to_close_timeout=REGISTRY_ACTIVITY_TIMEOUT,
                retry_policy=REGISTRY_RETRY_POLICY,
            )
            prepared_actions = {item.action.id: item for item in prepared}

            for round_num, actions in enumerate(context.plan.rounds, start=1):
                facts, compensation_candidates, failures = await self._execute_round(
                    context,
                    actions,
                    round_num,
                    allocation_by_node,
                    prepared_actions,
                )
                applied.extend(compensation_candidates)
                record_result = await self._execute_activity(
                    ACTIVITY_RECORD_ROUND_FACTS,
                    deployment_id,
                    facts,
                    result_type=RoundFactsResult,
                    task_queue=ORCHESTRATOR_QUEUE,
                )
                for warning in record_result.warnings:
                    workflow.logger.warning(
                        "Deployment facts were not fully persisted",
                        extra={"deployment_id": deployment_id, "warning": warning},
                    )
                if failures:
                    raise DeploymentStepFailed(failures[0])
                if (
                    context.plan.point_of_no_return_round is not None
                    and round_num >= context.plan.point_of_no_return_round
                ):
                    reversibility_reached = True

            await self._finish(deployment_id, DeploymentOutcome.COMPLETED)
            return DeploymentOutcome.COMPLETED.value
        except (ActivityError, ApplicationError, DeploymentStepFailed) as exc:
            failure = (
                exc.error
                if isinstance(exc, DeploymentStepFailed)
                else self._error_from_exception(exc)
            )
            if reversibility_reached:
                await self._finish(
                    deployment_id,
                    DeploymentOutcome.FAILED,
                    failure,
                )
                return DeploymentOutcome.FAILED.value
            compensation_error = await self._compensate(
                context=context,
                deployment_id=deployment_id,
                applied=applied,
                allocation_by_node=allocation_by_node,
                reservation_attempted=reservation_attempted,
                prepared_actions=prepared_actions,
            )
            if compensation_error is not None:
                await self._finish(
                    deployment_id,
                    DeploymentOutcome.FAILED,
                    compensation_error,
                )
                return DeploymentOutcome.FAILED.value
            await self._finish(
                deployment_id,
                DeploymentOutcome.COMPENSATED,
                failure,
            )
            return DeploymentOutcome.COMPENSATED.value

    async def _get_approval_state(self, deployment_id: int) -> ApprovalState:
        return await self._execute_activity(
            ACTIVITY_GET_APPROVAL_STATE,
            deployment_id,
            result_type=ApprovalState,
            task_queue=ORCHESTRATOR_QUEUE,
        )

    async def _execute_round(
        self,
        context: DeploymentContext,
        actions: list[Action],
        round_num: int,
        allocation_by_node: dict[UUID, int],
        prepared_actions: dict[str, AdapterActionInput],
    ) -> tuple[RoundFacts, list[Action], list[ErrorResponse]]:
        results = await asyncio.gather(
            *[
                self._dispatch_action(
                    context,
                    action,
                    allocation_by_node,
                    prepared_action=prepared_actions.get(action.id),
                )
                for action in actions
            ],
            return_exceptions=True,
        )
        facts: list[ActionFact] = []
        failures: list[ErrorResponse] = []
        for action, result in zip(actions, results, strict=True):
            if isinstance(result, Exception):
                error = self._error_from_exception(result)
                facts.append(
                    ActionFact(
                        action_id=action.id,
                        op=action.op,
                        succeeded=False,
                        target_id=action.target.id,
                        error=error,
                    )
                )
                failures.append(error)
            else:
                facts.append(
                    ActionFact(
                        action_id=action.id,
                        op=action.op,
                        succeeded=True,
                        target_id=action.target.id,
                        facts=result.facts,
                    )
                )
        successful_actions = [
            prepared_actions[action.id].action
            for action, result in zip(actions, results, strict=True)
            if not isinstance(result, Exception)
        ]
        return (
            RoundFacts(
                event_id=f"{context.deployment_id}:r{round_num}",
                round_num=round_num,
                actions=facts,
            ),
            successful_actions,
            failures,
        )

    async def _dispatch_action(
        self,
        context: DeploymentContext,
        action: Action,
        allocation_by_node: dict[UUID, int],
        *,
        prepared_action: AdapterActionInput | None = None,
        compensating: bool = False,
    ) -> AdapterActionOutput:
        if action.executor != Executor.ADAPTER:
            raise ApplicationError(
                "Initial Deploy supports adapter actions only",
                type=ErrorCode.CONFLICT.value,
                non_retryable=True,
            )
        if prepared_action is not None:
            action = prepared_action.action
        server_id = None
        if action.op == ActionOp.CREATE_VM:
            server_id = allocation_by_node.get(action.target.id)
            if server_id is None:
                raise ApplicationError(
                    f"No Placement allocation exists for node {action.target.id}",
                    type=ErrorCode.CONFLICT.value,
                    non_retryable=True,
                )
        elif action.op == ActionOp.ATTACH_PORT:
            server_id = allocation_by_node.get(action.target.node_id)
            if server_id is None:
                raise ApplicationError(
                    f"No Placement allocation exists for node {action.target.node_id}",
                    type=ErrorCode.CONFLICT.value,
                    non_retryable=True,
                )
        queue_name = f"{context.cluster_name}-queue"
        idempotency_suffix = f"compensate:{action.id}" if compensating else action.id
        request = AdapterActionInput(
            idempotency_key=f"{context.deployment_id}:{idempotency_suffix}",
            deployment_id=context.deployment_id,
            slice_id=context.slice_id,
            action=action,
            server_id=server_id,
        )
        activity_name = ADAPTER_ACTIVITIES[action.op]
        workflow.logger.info(
            "Dispatching deployment action",
            extra={
                "deployment_id": context.deployment_id,
                "workflow_id": workflow.info().workflow_id,
                "idempotency_key": request.idempotency_key,
            },
        )
        return await self._execute_activity(
            activity_name,
            request,
            result_type=AdapterActionOutput,
            task_queue=queue_name,
            start_to_close_timeout=(
                LONG_ADAPTER_ACTIVITY_TIMEOUT
                if action.op in {ActionOp.CREATE_VM, ActionOp.DELETE_VM}
                else ADAPTER_ACTIVITY_TIMEOUT
            ),
            retry_policy=(
                COMPENSATION_RETRY_POLICY if compensating else ADAPTER_RETRY_POLICY
            ),
        )

    async def _compensate(
        self,
        *,
        context: DeploymentContext | None,
        deployment_id: int,
        applied: list[Action],
        allocation_by_node: dict[UUID, int],
        reservation_attempted: bool,
        prepared_actions: dict[str, AdapterActionInput],
    ) -> ErrorResponse | None:
        compensation_error: ErrorResponse | None = None
        if context is not None:
            for action in reversed(applied):
                inverse = self._inverse_action(action)
                if inverse is None:
                    if compensation_error is None:
                        compensation_error = ErrorResponse(
                            code=ErrorCode.CONFLICT,
                            message=f"Action {action.op.value} has no Deploy compensation",
                            retryable=False,
                        )
                    continue
                try:
                    await self._dispatch_action(
                        context,
                        inverse,
                        allocation_by_node,
                        compensating=True,
                        prepared_action=prepared_actions.get(action.id),
                    )
                except (ActivityError, ApplicationError) as exc:
                    if compensation_error is None:
                        compensation_error = self._error_from_exception(exc)
        if context is not None:
            try:
                await self._execute_activity(
                    ACTIVITY_NETWORK_ROLLBACK,
                    deployment_id,
                    task_queue=ORCHESTRATOR_QUEUE,
                    start_to_close_timeout=REGISTRY_ACTIVITY_TIMEOUT,
                    retry_policy=COMPENSATION_RETRY_POLICY,
                )
            except (ActivityError, ApplicationError) as exc:
                if compensation_error is None:
                    compensation_error = self._error_from_exception(exc)
        if reservation_attempted:
            try:
                await self._execute_activity(
                    ACTIVITY_ROLLBACK_PLACEMENT,
                    deployment_id,
                    task_queue=ORCHESTRATOR_QUEUE,
                    retry_policy=COMPENSATION_RETRY_POLICY,
                )
            except (ActivityError, ApplicationError) as exc:
                if compensation_error is None:
                    compensation_error = self._error_from_exception(exc)
        return compensation_error

    @staticmethod
    def _inverse_action(action: Action) -> Action | None:
        inverse_op = INVERSE_OPERATIONS.get(action.op)
        if inverse_op is None:
            return None
        inverse_params: dict[str, Any] = action.params
        inverse_after = action.before
        return action.model_copy(
            update={
                "op": inverse_op,
                "params": inverse_params,
                "before": action.after,
                "after": inverse_after,
            }
        )

    async def _finish(
        self,
        deployment_id: int,
        outcome: DeploymentOutcome,
        error: ErrorResponse | None = None,
        *,
        operator_id: int | None = None,
    ) -> None:
        await self._execute_activity(
            ACTIVITY_FINISH_DEPLOYMENT,
            deployment_id,
            FinishDeployment(
                event_id=f"{deployment_id}:finish",
                outcome=outcome,
                operator_id=operator_id,
                error=error,
            ),
            task_queue=ORCHESTRATOR_QUEUE,
        )

    async def _execute_activity(
        self,
        activity_name: str,
        *args: Any,
        result_type: type[Any] | None = None,
        task_queue: str,
        start_to_close_timeout: timedelta = REGISTRY_ACTIVITY_TIMEOUT,
        retry_policy: RetryPolicy = REGISTRY_RETRY_POLICY,
    ) -> Any:
        return await workflow.execute_activity(
            activity_name,
            *args,
            result_type=result_type,
            task_queue=task_queue,
            start_to_close_timeout=start_to_close_timeout,
            retry_policy=retry_policy,
        )

    @staticmethod
    def _error_from_exception(error: Exception) -> ErrorResponse:
        current: Exception = error
        while isinstance(current, ActivityError) and isinstance(current.cause, Exception):
            current = current.cause
        raw_code = current.type if isinstance(current, ApplicationError) else None
        try:
            code = ErrorCode(raw_code) if raw_code is not None else ErrorCode.BACKEND_UNAVAILABLE
        except ValueError:
            code = ErrorCode.BACKEND_UNAVAILABLE
        retryable = not (
            isinstance(current, ApplicationError) and current.non_retryable
        )
        return ErrorResponse(code=code, message=str(current), retryable=retryable)
