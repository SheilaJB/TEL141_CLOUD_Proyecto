import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from contracts.adapter import AdapterActionOutput
from contracts.errors import ErrorCode, ErrorResponse
from contracts.plan import Action
from contracts.queues import (
    ACTIVITY_FINISH_DEPLOYMENT,
    ACTIVITY_RECORD_ROUND_FACTS,
    ORCHESTRATOR_QUEUE,
)
from contracts.registry import (
    ActionFact,
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


class DeploymentStepFailed(Exception):
    def __init__(self, error: ErrorResponse, *, compensable: bool = True) -> None:
        self.error = error
        self.compensable = compensable
        super().__init__(error.message)


@dataclass
class RoundsProgress:
    reversibility_reached: bool = False


class WorkflowHelpers:
    async def run_rounds(
        self,
        *,
        deployment_id: int,
        rounds: list[list[Action]],
        dispatch: Callable[[Action], Awaitable[AdapterActionOutput]],
        point_of_no_return: int | None = None,
        record_facts: bool = False,
        on_round_success: Callable[[list[Action]], None] | None = None,
        progress: RoundsProgress | None = None,
        compensable: bool = True,
    ) -> None:
        for round_num, actions in enumerate(rounds, start=1):
            results = await asyncio.gather(
                *[dispatch(action) for action in actions],
                return_exceptions=True,
            )
            facts: list[ActionFact] = []
            failures: list[ErrorResponse] = []
            succeeded: list[Action] = []
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
                    succeeded.append(action)
            if on_round_success is not None:
                on_round_success(succeeded)
            if record_facts:
                record_result: RoundFactsResult = await self._execute_activity(
                    ACTIVITY_RECORD_ROUND_FACTS,
                    deployment_id,
                    RoundFacts(
                        event_id=f"{deployment_id}:r{round_num}",
                        round_num=round_num,
                        actions=facts,
                    ),
                    result_type=RoundFactsResult,
                    task_queue=ORCHESTRATOR_QUEUE,
                )
                for warning in record_result.warnings:
                    workflow.logger.warning(
                        "Deployment facts were not fully persisted",
                        extra={"deployment_id": deployment_id, "warning": warning},
                    )
            if (progress is not None
                and point_of_no_return is not None
                and round_num >= point_of_no_return):
                progress.reversibility_reached = True
            if failures:
                raise DeploymentStepFailed(failures[0], compensable=compensable)

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
            args=list(args),
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
