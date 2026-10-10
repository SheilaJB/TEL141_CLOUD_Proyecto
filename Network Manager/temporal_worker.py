import asyncio
import logging
import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from app.temporal_worker import NETWORK_ALLOCATE, NETWORK_ROLLBACK, NetworkActivities, create_pool


async def run() -> None:
    client = await Client.connect(
        os.getenv("TEMPORAL_ADDRESS", "temporal:7233"),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
        data_converter=pydantic_data_converter,
    )
    pool = await create_pool()
    activities = NetworkActivities(pool)
    worker = Worker(
        client,
        task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "orchestrator-queue"),
        activities=[activities.network_allocate, activities.network_rollback],
    )
    try:
        await worker.run()
    finally:
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
