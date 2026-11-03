"""Temporal activities for persistent network allocation."""

from __future__ import annotations

import ipaddress
import os
from uuid import UUID

import asyncpg
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.network_allocator import LinkInput, NetworkAllocationError, NetworkAllocator
from app.config import config


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
                    SELECT d.slice_id, d.tipo, s.cluster_id, s.vlan_stag
                    FROM slices.deployment d
                    JOIN slices.slice s ON s.id = d.slice_id
                    WHERE d.id = $1
                    FOR UPDATE OF d, s
                    """,
                    deployment_id,
                )
                if deployment is None:
                    raise ApplicationError(
                        "deployment was not found",
                        type="RESOURCE_NOT_FOUND",
                        non_retryable=True,
                    )
                if deployment["tipo"] not in ("DEPLOY", "UPDATE"):
                    raise ApplicationError(
                        f"unsupported deployment type: {deployment['tipo']}",
                        type="INVALID_DEPLOYMENT_TYPE",
                        non_retryable=True,
                    )
                links = await connection.fetch(
                    """
                    SELECT id, public, cidr::text AS cidr, vlan_ctag
                    FROM slices.slice_enlace
                    WHERE deployment_id = $1 AND estado = 'PENDING'
                    ORDER BY public, name, id
                    """,
                    deployment_id,
                )
                ports = await connection.fetch(
                    """
                    SELECT id, enlace_id, nodo_id, public,
                           host(ip) AS ip, mac::text AS mac, nic_index
                    FROM slices.slice_enlace_puerto
                    WHERE deployment_id = $1 AND estado = 'PENDING'
                    ORDER BY public, enlace_id, nodo_id, id
                    """,
                    deployment_id,
                )
                existing_links = await connection.fetch(
                    """
                    SELECT DISTINCT e.id, e.public, e.cidr::text AS cidr
                    FROM slices.slice_enlace e
                    JOIN slices.slice_enlace_puerto p ON p.enlace_id = e.id
                    WHERE p.deployment_id = $1 AND p.estado = 'PENDING'
                      AND e.deployment_id IS DISTINCT FROM $1
                      AND e.estado <> 'DELETED'
                    ORDER BY e.public, e.id
                    """,
                    deployment_id,
                )

                def partial(message: str) -> ApplicationError:
                    return ApplicationError(
                        message, type="PARTIAL_ALLOCATION", non_retryable=True
                    )

                own_links = {row["id"]: row for row in links}
                existing_by_id = {row["id"]: row for row in existing_links}
                pending_nodes: dict[UUID, list[UUID]] = {}
                assigned_ports = []
                new_ports = []
                for port in ports:
                    nulls = sum(
                        port[key] is None for key in ("ip", "mac", "nic_index")
                    )
                    if nulls == 0:
                        assigned_ports.append(port)
                    elif nulls == 3:
                        new_ports.append(port)
                        pending_nodes.setdefault(port["enlace_id"], []).append(
                            port["nodo_id"]
                        )
                    else:
                        raise partial(f"port {port['id']} is partially allocated")
                    if (
                        port["enlace_id"] not in own_links
                        and port["enlace_id"] not in existing_by_id
                    ):
                        raise ApplicationError(
                            f"link {port['enlace_id']} of port {port['id']} was not found",
                            type="LINK_NOT_FOUND",
                            non_retryable=True,
                        )
                for row in links:
                    if row["public"]:
                        continue
                    link_nulls = (row["cidr"] is None) + (row["vlan_ctag"] is None)
                    if link_nulls == 1:
                        raise partial(f"link {row['id']} is partially allocated")
                    if link_nulls == 0 and row["id"] in pending_nodes:
                        raise partial(
                            f"link {row['id']} is allocated but has unallocated ports"
                        )
                    if link_nulls == 2 and any(
                        port["enlace_id"] == row["id"] for port in assigned_ports
                    ):
                        raise partial(
                            f"link {row['id']} is unallocated but has allocated ports"
                        )
                for row in existing_links:
                    if (
                        not row["public"]
                        and row["cidr"] is None
                        and row["id"] in pending_nodes
                    ):
                        raise partial(f"existing link {row['id']} has no CIDR")

                if deployment["vlan_stag"] is None:
                    if deployment["tipo"] != "DEPLOY":
                        raise ApplicationError(
                            "slice has no S-TAG",
                            type="VLAN_STAG_MISSING",
                            non_retryable=True,
                        )
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
                        raise ApplicationError(
                            "no S-TAG is available",
                            type="NET_VLAN_S_EXHAUSTED",
                            non_retryable=True,
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
                existing_private_ids = [
                    row["id"]
                    for row in existing_links
                    if not row["public"] and row["id"] in pending_nodes
                ]
                existing_ips_by_link: dict[UUID, set] = {}
                if existing_private_ids:
                    for row in await connection.fetch(
                        """
                        SELECT enlace_id, host(ip) AS ip
                        FROM slices.slice_enlace_puerto
                        WHERE enlace_id = ANY($1) AND ip IS NOT NULL
                          AND estado <> 'DELETED'
                        """,
                        existing_private_ids,
                    ):
                        existing_ips_by_link.setdefault(row["enlace_id"], set()).add(
                            ipaddress.ip_address(row["ip"])
                        )
                link_inputs = [
                    LinkInput(
                        link_id=row["id"],
                        public=row["public"],
                        node_ids=tuple(pending_nodes.get(row["id"], ())),
                    )
                    for row in links
                    if row["public"] or row["cidr"] is None
                ]
                for row in existing_links:
                    if row["id"] not in pending_nodes:
                        continue
                    link_inputs.append(
                        LinkInput(
                            link_id=row["id"],
                            public=row["public"],
                            node_ids=tuple(pending_nodes[row["id"]]),
                            existing_cidr=(
                                ipaddress.ip_network(row["cidr"])
                                if row["cidr"] is not None
                                else None
                            ),
                            existing_used_ips=frozenset(
                                existing_ips_by_link.get(row["id"], ())
                            ),
                            is_existing=True,
                        )
                    )
                total_private_links = await connection.fetchval(
                    """
                    SELECT count(*) FROM slices.slice_enlace
                    WHERE slice_id = $1 AND public = FALSE AND estado <> 'DELETED'
                    """,
                    deployment["slice_id"],
                )
                new_private_count = sum(
                    1
                    for item in link_inputs
                    if not item.public and not item.is_existing
                )
                used_cidrs = await connection.fetch(
                    """
                    SELECT cidr::text AS cidr
                    FROM slices.slice_enlace
                    WHERE slice_id = $1 AND cidr IS NOT NULL
                      AND estado <> 'DELETED'
                    """,
                    deployment["slice_id"],
                )
                used_ctags = await connection.fetch(
                    """
                    SELECT vlan_ctag FROM slices.slice_enlace
                    WHERE slice_id = $1 AND vlan_ctag IS NOT NULL
                      AND estado <> 'DELETED'
                    """,
                    deployment["slice_id"],
                )
                used_ips = await connection.fetch(
                    """
                    SELECT host(ip) AS ip FROM slices.slice_enlace_puerto
                    WHERE ip IS NOT NULL AND estado <> 'DELETED'
                    """
                )
                used_macs = await connection.fetch(
                    """
                    SELECT mac::text AS mac FROM slices.slice_enlace_puerto
                    WHERE mac IS NOT NULL AND estado <> 'DELETED'
                    """
                )
                floor_nodes = sorted({port["nodo_id"] for port in new_ports})
                nic_floor: dict[UUID, int] = {}
                if floor_nodes:
                    nic_floor = {
                        row["nodo_id"]: row["max_nic"]
                        for row in await connection.fetch(
                            """
                            SELECT nodo_id, MAX(nic_index) AS max_nic
                            FROM slices.slice_enlace_puerto
                            WHERE nodo_id = ANY($1) AND nic_index IS NOT NULL
                            GROUP BY nodo_id
                            """,
                            floor_nodes,
                        )
                    }
                try:
                    allocated_links, allocated_ports = allocator.allocate(
                        slice_id=deployment["slice_id"],
                        vlan_stag=stag,
                        links=link_inputs,
                        used_private_cidrs=[row["cidr"] for row in used_cidrs],
                        used_private_ctags=[row["vlan_ctag"] for row in used_ctags],
                        used_public_ips=[row["ip"] for row in used_ips],
                        used_macs=[row["mac"] for row in used_macs],
                        nic_index_floor=nic_floor,
                        existing_private_link_count=total_private_links
                        - new_private_count,
                    )
                except NetworkAllocationError as exc:
                    raise ApplicationError(
                        str(exc), type=exc.code, non_retryable=True
                    ) from exc
                port_by_key = {
                    (item["enlace_id"], item["nodo_id"]): item for item in new_ports
                }
                for link in allocated_links:
                    await connection.execute(
                        """
                        UPDATE slices.slice_enlace
                        SET cidr = $2::cidr, vlan_ctag = $3
                        WHERE id = $1 AND deployment_id = $4 AND estado = 'PENDING'
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
                        WHERE id = $1 AND deployment_id = $5 AND estado = 'PENDING'
                        """,
                        db_port["id"],
                        str(port.ip),
                        port.mac,
                        port.nic_index,
                        deployment_id,
                    )

                allocated_link_by_id = {item.link_id: item for item in allocated_links}
                response_links = []
                cidr_by_link: dict[UUID, ipaddress.IPv4Network] = {}
                for row in links:
                    item = allocated_link_by_id.get(row["id"])
                    if item is not None:
                        response_links.append(
                            {
                                "link_id": item.link_id,
                                "vlan_stag": item.vlan_stag,
                                "vlan_ctag": item.vlan_ctag,
                                "cidr": str(item.cidr) if item.cidr else None,
                                "public": item.public,
                            }
                        )
                        if item.cidr is not None:
                            cidr_by_link[item.link_id] = item.cidr
                    else:
                        response_links.append(
                            {
                                "link_id": row["id"],
                                "vlan_stag": None if row["public"] else stag,
                                "vlan_ctag": row["vlan_ctag"],
                                "cidr": row["cidr"],
                                "public": row["public"],
                            }
                        )
                        if row["cidr"] is not None:
                            cidr_by_link[row["id"]] = ipaddress.ip_network(row["cidr"])
                for row in existing_links:
                    if row["cidr"] is not None:
                        cidr_by_link[row["id"]] = ipaddress.ip_network(row["cidr"])

                link_order = {
                    row["id"]: index
                    for index, row in enumerate([*links, *existing_links])
                }
                response_ports = [
                    (
                        link_order[port.link_id],
                        port.node_id,
                        {
                            "port_id": port_by_key[(port.link_id, port.node_id)]["id"],
                            "link_id": port.link_id,
                            "node_id": port.node_id,
                            "public": port.public,
                            "ip": str(port.ip),
                            "mac": port.mac,
                            "nic_index": port.nic_index,
                        },
                    )
                    for port in allocated_ports
                ]
                response_ports.extend(
                    (
                        link_order[port["enlace_id"]],
                        port["nodo_id"],
                        {
                            "port_id": port["id"],
                            "link_id": port["enlace_id"],
                            "node_id": port["nodo_id"],
                            "public": port["public"],
                            "ip": port["ip"],
                            "mac": port["mac"],
                            "nic_index": port["nic_index"],
                        },
                    )
                    for port in assigned_ports
                )
                response_ports.sort(key=lambda entry: (entry[0], entry[1]))
                return {
                    "deployment_id": deployment_id,
                    "slice_id": deployment["slice_id"],
                    "public_vid": allocator.public_vid,
                    "links": response_links,
                    "ports": [
                        {
                            "port_id": entry["port_id"],
                            "link_id": entry["link_id"],
                            "node_id": entry["node_id"],
                            "public": entry["public"],
                            "ip": entry["ip"],
                            "prefijo": (
                                allocator.public_base.prefixlen
                                if entry["public"]
                                else cidr_by_link[entry["link_id"]].prefixlen
                            ),
                            "gateway": (
                                config.internet_gw_ip if entry["public"] else None
                            ),
                            "mac": entry["mac"],
                            "nic_index": entry["nic_index"],
                        }
                        for _, _, entry in response_ports
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
                      AND estado IN ('PENDING', 'FAILED')
                    """,
                    deployment_id,
                )
                ports = await connection.execute(
                    """
                    UPDATE slices.slice_enlace_puerto
                    SET ip = NULL, mac = NULL, nic_index = NULL
                    WHERE deployment_id = $1
                      AND estado IN ('PENDING', 'FAILED')
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
