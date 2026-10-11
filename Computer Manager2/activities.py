"""Temporal activities for Compute Manager decisions."""

from contracts.compute import ComputeAllocation, ComputePlacement
from temporalio import activity

from service import compute_allocate, rollback_compute


@activity.defn(name="compute_allocate")
async def compute_allocate_activity(
    deployment_id: int,
    placements: list[ComputePlacement],
) -> list[ComputeAllocation]:
    server_map = {item.node_id: item.server_id for item in placements}
    return compute_allocate(deployment_id, server_map)


@activity.defn(name="rollback_compute")
async def rollback_compute_activity(deployment_id: int) -> int:
    return rollback_compute(deployment_id)


ACTIVITIES = [compute_allocate_activity, rollback_compute_activity]
