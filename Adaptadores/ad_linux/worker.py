"""Temporal worker entrypoint for the Linux adapter."""

import asyncio
import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from contracts.queues import LINUX_QUEUE

from .activities import ADAPTER_ACTIVITIES


async def run_worker() -> None:
    client = await Client.connect(
        os.getenv("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
        data_converter=pydantic_data_converter,
    )
    worker = Worker(
        client,
        task_queue=os.getenv("LINUX_ADAPTER_TEMPORAL_TASK_QUEUE", LINUX_QUEUE),
        activities=ADAPTER_ACTIVITIES,
    )
    await worker.run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
