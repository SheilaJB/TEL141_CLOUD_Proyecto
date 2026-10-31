#Workflow de destruccion de un slice.


import asyncio
from datetime import timedelta
from uuid import UUID

from contracts.adapter import AdapterActionInput, AdapterActionOutput, AdapterHost
from contracts.errors import ErrorCode, ErrorResponse
from contracts.plan import Action, PlanType
from contracts.queues import (
    ACTIVITY_COMMIT_DESTROY,
    ACTIVITY_FINALIZE_PLACEMENT,
    ACTIVITY_GET_PLAN,
    ACTIVITY_GET_SERVER_DIRECTORY,
    ACTIVITY_START_DEPLOYMENT,
    LINUX_QUEUE,
    ORCHESTRATOR_QUEUE,
    PLACEMENT_QUEUE,
)
from contracts.registry import (
    CommitDestroy,
    DeploymentContext,
    DeploymentOutcome,
)
from contracts.servers import ServerHost
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from temporal_worker.worker.workflows.common import (
    LONG_ADAPTER_ACTIVITY_TIMEOUT,
    PLACEMENT_ACTIVITY_TIMEOUT,
    PLACEMENT_RETRY_POLICY,
    DeploymentStepFailed,
    WorkflowHelpers,
)

DESTROY_HOST_ACTIVITY = "destroy_slice_host"
DESTROY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    non_retryable_error_types=[
        ErrorCode.INVALID_SPEC.value,
        ErrorCode.RESOURCE_NOT_FOUND.value,
        ErrorCode.CONFLICT.value,
        "HOST_MISSING",
    ],
)


@workflow.defn(name="DestroyWorkflow")
class DestroyWorkflow(WorkflowHelpers):
    @workflow.run
    async def run(self, deployment_id: int) -> str:
        try:
            context: DeploymentContext = await self._execute_activity(
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
            if context.plan.type != PlanType.DESTROY:
                raise ApplicationError(
                    f"DestroyWorkflow received a {context.plan.type.value} plan",
                    type=ErrorCode.CONFLICT.value,
                    non_retryable=True,
                )

            actions = [action for round_actions in context.plan.rounds for action in round_actions]
            destroyed: list[UUID] = []
            host_failure: ErrorResponse | None = None
            if actions:
                destroyed, host_failure = await self._destroy_hosts(context, actions)

            # Única escritura de DELETED (P26); también en fallo parcial, antes de elevar el error.
            await self._execute_activity(
                ACTIVITY_COMMIT_DESTROY,
                deployment_id,
                CommitDestroy(node_ids=destroyed, complete=host_failure is None),
                task_queue=ORCHESTRATOR_QUEUE,
            )
            if host_failure is not None:
                raise DeploymentStepFailed(host_failure)

            # Secuenciales y en este orden (P11): ambos bloquean el deployment.
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
            await self._finish(deployment_id, DeploymentOutcome.FAILED, failure)
            return DeploymentOutcome.FAILED.value

    async def _destroy_hosts(
        self, context: DeploymentContext, actions: list[Action]
    ) -> tuple[list[UUID], ErrorResponse | None]:
        # Solo ejecuta en los hosts; no escribe en BD (P26). Devuelve los nodos destruidos.
        directory: list[ServerHost] = await self._execute_activity(
            ACTIVITY_GET_SERVER_DIRECTORY,
            context.slice_id,
            result_type=list[ServerHost],
            task_queue=ORCHESTRATOR_QUEUE,
        )
        hosts = {item.server_id: item for item in directory}

        requests: list[AdapterActionInput] = []
        for action in actions:
            server_id = int(action.params["server_id"])
            server = hosts.get(server_id)
            if server is None:
                raise ApplicationError(
                    f"server {server_id} is not ACTIVE in the slice zone; "
                    "its VMs cannot be destroyed until it is back",
                    type=ErrorCode.CONFLICT.value,
                    non_retryable=True,
                )
            requests.append(
                AdapterActionInput(
                    idempotency_key=f"{context.deployment_id}:{action.id}",
                    deployment_id=context.deployment_id,
                    slice_id=context.slice_id,
                    action=action,
                    server_id=server_id,
                    host=AdapterHost(
                        server_id=server.server_id,
                        management_ip=server.management_ip,
                    ),
                )
            )

        results = await asyncio.gather(
            *[
                self._execute_activity(
                    DESTROY_HOST_ACTIVITY,
                    request,
                    result_type=AdapterActionOutput,
                    task_queue=LINUX_QUEUE,
                    start_to_close_timeout=LONG_ADAPTER_ACTIVITY_TIMEOUT,
                    retry_policy=DESTROY_RETRY_POLICY,
                )
                for request in requests
            ],
            return_exceptions=True,
        )

        destroyed: list[UUID] = []
        failures: list[ErrorResponse] = []
        for result in results:
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                failures.append(self._error_from_exception(result))
            else:
                destroyed.extend(
                    UUID(str(item)) for item in result.facts.get("destroyed_vms", [])
                )
        return destroyed, (failures[0] if failures else None)