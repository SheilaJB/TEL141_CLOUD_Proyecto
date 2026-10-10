"""Temporal activities for persistent network allocation."""

from __future__ import annotations

import os
from uuid import UUID

import asyncpg
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.network_allocator import LinkInput, NetworkAllocationError, NetworkAllocator


NETWORK_ALLOCATE = "network_allocate"
NETWORK_ROLLBACK = "network_rollback"


class NetworkActivities:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @activity.defn(name=NETWORK_ALLOCATE)
    async def network_allocate(self, deployment_id: int) -> dict:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                deployment = await connection.fetchrow(
                    """
                    SELECT d.slice_id, s.cluster_id, s.vlan_stag
                    FROM slices.deployment d
                    JOIN slices.slice s ON s.id = d.slice_id
                    WHERE d.id = $1 AND d.tipo = 'DEPLOY'
                    FOR UPDATE OF d, s
                    """,
                    deployment_id,
                )
                if deployment is None:
                    raise NetworkAllocationError(
                        "RESOURCE_NOT_FOUND", "deployment was not found"
                    )
                links = await connection.fetch(
                    """
                    SELECT id, public
                    FROM slices.slice_enlace
                    WHERE deployment_id = $1 AND estado <> 'DELETED'
                    ORDER BY public, name, id
                    """,
                    deployment_id,
                )
                ports = await connection.fetch(
                    """
                    SELECT id, enlace_id, nodo_id
                    FROM slices.slice_enlace_puerto
                    WHERE deployment_id = $1 AND estado <> 'DELETED'
                    ORDER BY public, enlace_id, nodo_id, id
                    """,
                    deployment_id,
                )
                if deployment["vlan_stag"] is None:
                    await connection.execute(
                        "SELECT pg_advisory_xact_lock($1)", deployment["cluster_id"]
                    )
                    stag = await connection.fetchval(
                        """
                        SELECT value
                        FROM generate_series(100, 999) AS value
                        WHERE NOT EXISTS (
                            SELECT 1 FROM slices.slice other
                            WHERE other.cluster_id = $1
                              AND other.vlan_stag = value
                              AND other.estado <> 'ELIMINATED'
                        )
                        ORDER BY value
                        LIMIT 1
                        """,
                        deployment["cluster_id"],
                    )
                    if stag is None:
                        raise NetworkAllocationError(
                            "NET_VLAN_S_EXHAUSTED", "no S-TAG is available"
                        )
                    await connection.execute(
                        """
                        UPDATE slices.slice
                        SET vlan_stag = $2, red_deployment_id = $3
                        WHERE id = $1
                        """,
                        deployment["slice_id"],
                        stag,
                        deployment_id,
                    )
                else:
                    stag = deployment["vlan_stag"]

                allocator = NetworkAllocator(
                    private_base=os.getenv("PRIVATE_P2P_BASE", "192.168.1.0/24"),
                    public_base="10.60.5.0/24",
                    public_vid=int(os.getenv("PUBLIC_VID", "4000")),
                )
                link_inputs = [
                    LinkInput(
                        link_id=row["id"],
                        public=row["public"],
                        node_ids=tuple(
                            port["nodo_id"]
                            for port in ports
                            if port["enlace_id"] == row["id"]
                        ),
                    )
                    for row in links
                ]
                used_cidrs = await connection.fetch(
                    """
                    SELECT cidr::text AS cidr
                    FROM slices.slice_enlace
                    WHERE slice_id = $1 AND cidr IS NOT NULL
                      AND estado <> 'DELETED' AND deployment_id IS DISTINCT FROM $2
                    """,
                    deployment["slice_id"],
                    deployment_id,
                )
                used_ctags = await connection.fetch(
                    """
                    SELECT vlan_ctag FROM slices.slice_enlace
                    WHERE slice_id = $1 AND vlan_ctag IS NOT NULL
                      AND estado <> 'DELETED' AND deployment_id IS DISTINCT FROM $2
                    """,
                    deployment["slice_id"],
                    deployment_id,
                )
                used_ips = await connection.fetch(
                    """
                    SELECT ip::text AS ip FROM slices.slice_enlace_puerto
                    WHERE ip IS NOT NULL AND estado <> 'DELETED'
                      AND deployment_id IS DISTINCT FROM $1
                    """,
                    deployment_id,
                )
                used_macs = await connection.fetch(
                    """
                    SELECT mac::text AS mac FROM slices.slice_enlace_puerto
                    WHERE mac IS NOT NULL AND estado <> 'DELETED'
                      AND deployment_id IS DISTINCT FROM $1
                    """,
                    deployment_id,
                )
                try:
                    allocated_links, allocated_ports = allocator.allocate(
                        slice_id=deployment["slice_id"],
                        vlan_stag=stag,
                        links=link_inputs,
                        used_private_cidrs=[row["cidr"] for row in used_cidrs],
                        used_private_ctags=[row["vlan_ctag"] for row in used_ctags],
                        used_public_ips=[row["ip"] for row in used_ips],
                        used_macs=[row["mac"] for row in used_macs],
                    )
                except NetworkAllocationError as exc:
                    raise ApplicationError(
                        str(exc), type=exc.code, non_retryable=True
                    ) from exc
                port_by_key = {(item["enlace_id"], item["nodo_id"]): item for item in ports}
                for link in allocated_links:
                    await connection.execute(
                        """
                        UPDATE slices.slice_enlace
                        SET cidr = $2::cidr, vlan_ctag = $3
                        WHERE id = $1 AND deployment_id = $4
                        """,
                        link.link_id,
                        str(link.cidr) if link.cidr else None,
                        link.vlan_ctag,
                        deployment_id,
                    )
                for port in allocated_ports:
                    db_port = port_by_key[(port.link_id, port.node_id)]
                    await connection.execute(
                        """
                        UPDATE slices.slice_enlace_puerto
                        SET ip = $2::inet, mac = $3::macaddr, nic_index = $4
                        WHERE id = $1 AND deployment_id = $5
                        """,
                        db_port["id"],
                        str(port.ip),
                        port.mac,
                        port.nic_index,
                        deployment_id,
                    )
                return {
                    "deployment_id": deployment_id,
                    "slice_id": deployment["slice_id"],
                    "public_vid": allocator.public_vid,
                    "links": [
                        {
                            "link_id": item.link_id,
                            "vlan_stag": item.vlan_stag,
                            "vlan_ctag": item.vlan_ctag,
                            "cidr": str(item.cidr) if item.cidr else None,
                            "public": item.public,
                        }
                        for item in allocated_links
                    ],
                    "ports": [
                        {
                            "port_id": port_by_key[(item.link_id, item.node_id)]["id"],
                            "link_id": item.link_id,
                            "node_id": item.node_id,
                            "public": item.public,
                            "ip": str(item.ip),
                            "mac": item.mac,
                            "nic_index": item.nic_index,
                        }
                        for item in allocated_ports
                    ],
                }

    @activity.defn(name=NETWORK_ROLLBACK)
    async def network_rollback(self, deployment_id: int) -> dict:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                links = await connection.execute(
                    """
                    UPDATE slices.slice_enlace
                    SET cidr = NULL, vlan_ctag = NULL
                    WHERE deployment_id = $1 AND public = FALSE
                    """,
                    deployment_id,
                )
                ports = await connection.execute(
                    """
                    UPDATE slices.slice_enlace_puerto
                    SET ip = NULL, mac = NULL, nic_index = NULL
                    WHERE deployment_id = $1
                    """,
                    deployment_id,
                )
                await connection.execute(
                    """
                    UPDATE slices.slice
                    SET vlan_stag = NULL, red_deployment_id = NULL
                    WHERE red_deployment_id = $1
                    """,
                    deployment_id,
                )
                return {
                    "deployment_id": deployment_id,
                    "cleared_links": int(links.split()[-1]),
                    "cleared_ports": int(ports.split()[-1]),
                }


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(os.environ["DATABASE_URL"])
