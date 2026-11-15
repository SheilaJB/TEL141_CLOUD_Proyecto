"""Temporal worker entrypoint for the OpenStack adapter."""

import asyncio
import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from contracts.queues import OPENSTACK_QUEUE

from . import client, config
from .activities import ADAPTER_ACTIVITIES


async def run_worker() -> None:
    config.require()
    # Falla rápido si no hay túnel o credenciales, antes de tomar tareas.
    await asyncio.to_thread(client.check)
    temporal = await Client.connect(
        os.getenv("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
        data_converter=pydantic_data_converter,
    )
    worker = Worker(
        temporal,
        task_queue=os.getenv("OPENSTACK_ADAPTER_TEMPORAL_TASK_QUEUE", OPENSTACK_QUEUE),
        activities=ADAPTER_ACTIVITIES,
        # Cada actividad bloquea un hilo mientras espera a Nova/Neutron.
        max_concurrent_activities=config.MAX_CONCURRENT_ACTIVITIES,
    )
    try:
        await worker.run()
    finally:
        client.close()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
