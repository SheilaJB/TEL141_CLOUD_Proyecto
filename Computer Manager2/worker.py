"""Temporal worker entrypoint for Compute Manager."""

import asyncio
import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from contracts.queues import COMPUTE_QUEUE

import config
import db
from activities import ACTIVITIES


async def run_worker() -> None:
    client = await Client.connect(
        config.TEMPORAL_ADDRESS,
        namespace=config.TEMPORAL_NAMESPACE,
        data_converter=pydantic_data_converter,
    )
    worker = Worker(
        client,
        task_queue=os.getenv("COMPUTE_MANAGER_TEMPORAL_TASK_QUEUE", COMPUTE_QUEUE),
        activities=ACTIVITIES,
    )
    await worker.run()


def main() -> None:
    db.open_pool()
    try:
        asyncio.run(run_worker())
    finally:
        db.close_pool()


if __name__ == "__main__":
    main()
