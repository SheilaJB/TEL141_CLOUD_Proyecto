"""Compute decisions and registry operations."""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from contracts.compute import ComputeAllocation
import repository
from errors import NonRetryableError

MAX_DISPLAY = 99
MIN_DISPLAY = 1


def choose_display(occupied: set[int]) -> int:
    for display in range(MIN_DISPLAY, MAX_DISPLAY + 1):
        if display not in occupied:
            return display
    raise NonRetryableError("VNC_DISPLAY_EXHAUSTED")


def allocate_displays(
    conn: Any,
    deployment_id: int,
    server_map: Mapping[UUID, int],
) -> list[ComputeAllocation]:
    nodes = repository.list_pending_nodes(conn, deployment_id)
    missing = [node["id"] for node in nodes if UUID(str(node["id"])) not in server_map]
    if missing:
        raise NonRetryableError(
            "NODE_NOT_PLACED",
            f"Nodes missing from server_map: {', '.join(str(node) for node in missing)}",
        )

    for node in nodes:
        if node["vnc_display"] is None:
            continue
        if node["server_id"] is None:
            raise NonRetryableError(
                "PARTIAL_ALLOCATION",
                f"Node {node['id']} has vnc_display but no server_id",
            )
        expected = int(server_map[UUID(str(node["id"]))])
        if int(node["server_id"]) != expected:
            raise NonRetryableError(
                "SERVER_MISMATCH",
                f"Node {node['id']} registered server_id={node['server_id']}, "
                f"received server_id={expected}",
            )

    server_ids = [
        int(server_map[UUID(str(node["id"]))])
        for node in nodes
        if node["vnc_display"] is None
    ]
    repository.lock_servers(conn, server_ids)

    occupied_by_server: dict[int, set[int]] = {}
    result: list[ComputeAllocation] = []
    for node in nodes:
        node_id = UUID(str(node["id"]))
        server_id = int(server_map[node_id])
        if node["vnc_display"] is not None:
            result.append(
                ComputeAllocation(
                    node_id=node_id,
                    vnc_display=int(node["vnc_display"]),
                )
            )
            continue
        occupied = occupied_by_server.setdefault(
            server_id, repository.occupied_displays(conn, server_id)
        )
        display = choose_display(occupied)
        if repository.assign_display(conn, str(node_id), server_id, display) != 1:
            raise NonRetryableError(
                "NODE_NOT_ASSIGNABLE",
                f"Node {node_id} could not be assigned a display",
            )
        occupied.add(display)
        result.append(ComputeAllocation(node_id=node_id, vnc_display=display))
    return result


def compute_allocate(
    deployment_id: int,
    server_map: Mapping[UUID, int],
) -> list[ComputeAllocation]:
    import db

    with db.tx() as conn:
        return allocate_displays(conn, deployment_id, server_map)


def rollback_compute(deployment_id: int) -> int:
    import db

    with db.tx() as conn:
        return repository.rollback_pending(conn, deployment_id)
