"""Helpers compartidos por los workflows (sin lógica de negocio)."""

from datetime import timedelta
from typing import Any

from contracts.errors import ErrorCode, ErrorResponse
from contracts.queues import ACTIVITY_FINISH_DEPLOYMENT, ORCHESTRATOR_QUEUE
from contracts.registry import DeploymentOutcome, FinishDeployment
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
    def __init__(self, error: ErrorResponse) -> None:
        self.error = error
        super().__init__(error.message)


class WorkflowHelpers:
    """Mixin con el wrapper de activities, el cierre y el mapeo de errores."""

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
