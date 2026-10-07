"""Disposable DEPLOY-only Placement simulation; replace with the Placement client later."""

from dataclasses import dataclass
from uuid import UUID

from contracts.placement import (
    PlacementAllocation,
    PlacementReserveRequest,
    PlacementReserveResponse,
)


@dataclass(frozen=True, slots=True)
class NodeDemand:
    node_id: UUID
    vcpu: int
    ram_mb: int
    disk_mb: int


@dataclass(frozen=True, slots=True)
class ServerCapacity:
    server_id: int
    vcpu_available: int
    ram_mb_available: int
    disk_mb_available: int


class InsufficientPlacementCapacity(Exception):
    pass


def simulate_deploy_placement(
    request: PlacementReserveRequest,
    demands: list[NodeDemand],
    servers: list[ServerCapacity],
) -> PlacementReserveResponse:
    ordered_servers = sorted(servers, key=lambda server: server.server_id)
    remaining = {
        server.server_id: [
            server.vcpu_available,
            server.ram_mb_available,
            server.disk_mb_available,
        ]
        for server in ordered_servers
    }
    allocations: list[PlacementAllocation] = []
    next_server = 0

    for demand in demands:
        for offset in range(len(ordered_servers)):
            index = (next_server + offset) % len(ordered_servers)
            server = ordered_servers[index]
            capacity = remaining[server.server_id]
            resources = (demand.vcpu, demand.ram_mb, demand.disk_mb)
            if all(available >= required for available, required in zip(capacity, resources)):
                allocations.append(
                    PlacementAllocation(
                        node_id=demand.node_id,
                        server_id=server.server_id,
                    )
                )
                remaining[server.server_id] = [
                    available - required
                    for available, required in zip(capacity, resources)
                ]
                next_server = (index + 1) % len(ordered_servers)
                break
        else:
            raise InsufficientPlacementCapacity(
                f"no active server in the slice zone has capacity for node {demand.node_id}"
            )

    return PlacementReserveResponse(
        deployment_id=request.deployment_id,
        allocations=allocations,
    )
