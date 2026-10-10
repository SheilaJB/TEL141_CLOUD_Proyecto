import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import TemporalError

from contracts.queues import ORCHESTRATOR_QUEUE
from temporal_worker.worker.activities.registry import DeploymentActivities
from temporal_worker.worker.activities.slice_manager_client import SliceManagerClient
from temporal_worker.worker.config import load_settings
from temporal_worker.worker.workflows.deploy_workflow import DeployWorkflow

logger = logging.getLogger(__name__)


async def run_worker() -> None:
    settings = load_settings()
    if not settings.internal_service_token:
        raise RuntimeError("ORCHESTRATOR_INTERNAL_SERVICE_TOKEN must be configured")

    try:
        temporal_client = await Client.connect(
            settings.temporal_address,
            namespace=settings.temporal_namespace,
            data_converter=pydantic_data_converter,
        )
    except (TemporalError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"Could not connect to Temporal at {settings.temporal_address}"
        ) from exc

    slice_manager = SliceManagerClient(
        settings.slice_manager_url,
        settings.internal_service_token,
    )
    activities = DeploymentActivities(slice_manager=slice_manager)
    worker = Worker(
        temporal_client,
        task_queue=settings.temporal_task_queue or ORCHESTRATOR_QUEUE,
        workflows=[DeployWorkflow],
        activities=[
            activities.get_plan,
            activities.get_approval_state,
            activities.start_deployment,
            activities.record_round_facts,
            activities.finish_deployment,
            activities.reserve,
            activities.rollback_reservation,
            activities.build_adapter_actions,
        ],
    )
    try:
        await worker.run()
    finally:
        await slice_manager.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
