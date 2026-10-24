"""Plan DESTROY: funcion pura, sin acceso a BD, red ni disco.

Una sola ronda con una accion DESTROY_SLICE_HOST por servidor que aloja VMs
del slice. La destruccion no es reversible, asi que point_of_no_return_round=1:
si algo falla no se compensa, el deployment queda FAILED y se puede reintentar
(destroy_slice_host es idempotente en el adaptador).
"""

import hashlib
from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, UUID, uuid5

from contracts.plan import (
    Action,
    ActionOp,
    ActionTarget,
    DeploymentPlan,
    Disruption,
    Executor,
    Intent,
    IntentKind,
    IntentTarget,
    PlanSummary,
    PlanType,
    ReservationPlan,
    ReservationRelease,
    ResourceVector,
    TargetKind,
)

DESTROY_INTENT_ID = "destroy-slice"


@dataclass(frozen=True, slots=True)
class DestroyNode:
    node_id: UUID
    # None si el nodo nunca llego a ubicarse en un servidor.
    server_id: int | None
    port_ids: tuple[UUID, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DestroyLink:
    link_id: UUID


def tap_name_for_port(port_id: UUID | str) -> str:
    """Mismo nombre que genera el adaptador Linux (ad_linux/vm.py, network.py)."""
    return "tp-" + hashlib.sha1(str(port_id).encode()).hexdigest()[:10]


def host_target_id(slice_id: int, server_id: int) -> UUID:
    """UUID estable para el target de la accion por host."""
    return uuid5(NAMESPACE_URL, f"slice:{slice_id}:server:{server_id}")


def build_destroy_plan(
    *,
    slice_id: int,
    nodes: list[DestroyNode],
    links: list[DestroyLink],
) -> DeploymentPlan:
    intents = [
        Intent(
            id=f"rm-vm-{node.node_id}",
            target=IntentTarget(table="vms", id=node.node_id),
            kind=IntentKind.REMOVE,
            strategy="destroy",
            disruption=Disruption.N2,
        )
        for node in sorted(nodes, key=lambda item: str(item.node_id))
    ] + [
        Intent(
            id=f"rm-link-{link.link_id}",
            target=IntentTarget(table="links", id=link.link_id),
            kind=IntentKind.REMOVE,
            strategy="destroy",
            disruption=Disruption.N2,
        )
        for link in sorted(links, key=lambda item: str(item.link_id))
    ]

    nodes_by_server: dict[int, list[DestroyNode]] = {}
    for node in nodes:
        if node.server_id is not None:
            nodes_by_server.setdefault(node.server_id, []).append(node)

    actions: list[Action] = []
    for index, server_id in enumerate(sorted(nodes_by_server), start=1):
        server_nodes = sorted(nodes_by_server[server_id], key=lambda item: str(item.node_id))
        # Se borran TODOS los TAP: los privados quedan huerfanos al borrar
        # br-sl-<slice>, y los publicos viven en br-provider.
        tap_names = sorted(
            {tap_name_for_port(port_id) for node in server_nodes for port_id in node.port_ids}
        )
        actions.append(
            Action(
                id=f"d{index}",
                op=ActionOp.DESTROY_SLICE_HOST,
                executor=Executor.ADAPTER,
                intent_id=DESTROY_INTENT_ID,
                target=ActionTarget(
                    kind=TargetKind.SLICE,
                    id=host_target_id(slice_id, server_id),
                ),
                params={
                    "server_id": server_id,
                    "vms": [{"vm_id": str(node.node_id)} for node in server_nodes],
                    "tap_names": tap_names,
                },
            )
        )

    zero = ResourceVector(vcpu=0, ram_mb=0, disk_mb=0)
    return DeploymentPlan(
        type=PlanType.DESTROY,
        summary=PlanSummary(
            disruption_max=Disruption.N2,
            destructive=True,
            actions=len(actions),
            resource_delta=zero,
            resource_total_after=zero,
        ),
        intents=intents,
        reservation=ReservationPlan(
            release=[
                ReservationRelease(node_id=node.node_id)
                for node in sorted(nodes, key=lambda item: str(item.node_id))
            ]
        ),
        point_of_no_return_round=1 if actions else None,
        rounds=[actions] if actions else [],
    )
