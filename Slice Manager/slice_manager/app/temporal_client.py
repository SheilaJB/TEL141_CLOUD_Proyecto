import asyncio

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import TemporalError, WorkflowAlreadyStartedError

from contracts.queues import ORCHESTRATOR_QUEUE
from slice_manager.app.config import Settings
from slice_manager.app.domain.errors import WorkflowStartError


class TemporalWorkflowClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Client | None = None
        self._connect_lock = asyncio.Lock()

    async def start_deploy(self, deployment_id: int, workflow_id: str) -> None:
        client = await self._get_client()
        try:
            await client.start_workflow(
                "DeployWorkflow",
                deployment_id,
                id=workflow_id,
                task_queue=self._settings.temporal_task_queue or ORCHESTRATOR_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            return
        except (TemporalError, TimeoutError, OSError) as exc:
            raise WorkflowStartError("Temporal could not start the workflow") from exc

    async def signal_approval(
        self,
        workflow_id: str,
        approved: bool,
        operator_id: int,
        reason: str | None,
    ) -> None:
        client = await self._get_client()
        try:
            handle = client.get_workflow_handle(workflow_id)
            await handle.signal(
                "approval_decision",
                approved,
                operator_id,
                reason,
            )
        except (TemporalError, TimeoutError, OSError) as exc:
            raise WorkflowStartError("Temporal could not deliver approval") from exc

    async def _get_client(self) -> Client:
        if self._client is not None:
            return self._client
        async with self._connect_lock:
            if self._client is None:
                try:
                    self._client = await Client.connect(
                        self._settings.temporal_address,
                        namespace=self._settings.temporal_namespace,
                    )
                except (TemporalError, TimeoutError, OSError) as exc:
                    raise WorkflowStartError("Temporal is unavailable") from exc
        if self._client is None:
            raise WorkflowStartError("Temporal client was not initialized")
        return self._client
