import ipaddress
import json
from pathlib import Path
from uuid import UUID

import asyncpg

from slice_manager.app.domain.capabilities import load_adapter_capabilities, load_capabilities
from slice_manager.app.domain.errors import ConflictError
from slice_manager.app.planning.common import validate_definition
from slice_manager.app.planning.definition import SliceDefinition, parse_definition
from slice_manager.app.planning.errors import InvalidSpecError
from slice_manager.app.planning.update_planner import (
    PUBLIC_LINK,
    CurrentLink,
    CurrentPort,
    CurrentState,
    CurrentVM,
    LinkCapacity,
    UpdateInputs,
    existing_ids_from_state,
)
from slice_manager.app.repositories.deployments import DeploymentRepository


def validate_update_headroom(
    inputs: UpdateInputs, *, vcpu_max: int, memory_max_mb: int
) -> None:
    current_by_name = {vm.name: vm for vm in inputs.current.vms}
    for desired in inputs.desired.vms:
        target = inputs.flavors.get(desired.flavor)
        if target is None:
            raise ConflictError(f"flavor {desired.flavor!r} is missing from Update catalog")
        current = current_by_name.get(desired.name)
        if current is not None and current.flavor not in inputs.flavors:
            raise ConflictError(
                f"current flavor {current.flavor!r} is missing from Update catalog"
            )
        if target.vcpu > vcpu_max or target.ram_mb > memory_max_mb:
            raise ConflictError(
                f"VM {desired.name!r} exceeds the hot-resize headroom ceiling"
            )


async def load_update_inputs(
    connection: asyncpg.Connection,
    repository: DeploymentRepository,
    context: asyncpg.Record,
    deployment_id: int,
    desired: SliceDefinition,
    capabilities_dir: Path | None,
) -> UpdateInputs:
    validate_definition(desired)
    if any(link.name == PUBLIC_LINK for link in desired.links):
        raise InvalidSpecError("private link name 'public' is reserved for Update")

    origin, nodes, links, ports = await repository.load_update_inventory(
        connection, context["slice_id"]
    )
    if origin is None:
        raise ConflictError("Update requires an ACTIVE origin version")
    raw_spec = origin["spec"]
    if isinstance(raw_spec, str):
        try:
            raw_spec = json.loads(raw_spec)
        except json.JSONDecodeError as exc:
            raise ConflictError("active version spec is not valid JSON") from exc
    current_spec = parse_definition(raw_spec)
    validate_definition(current_spec)
    if any(link.name == PUBLIC_LINK for link in current_spec.links):
        raise InvalidSpecError("private link name 'public' is reserved for Update")

    spec_vms = {vm.name: vm for vm in current_spec.vms}
    spec_links = {link.name: link for link in current_spec.links}
    nodes_by_name = {row["name"]: row for row in nodes}
    nodes_by_id = {row["id"]: row for row in nodes}
    if len(nodes_by_name) != len(nodes) or set(nodes_by_name) != set(spec_vms):
        raise ConflictError("active spec and live node inventory do not match")
    links_by_id = {row["id"]: row for row in links}
    private_links = [row for row in links if not row["public"]]
    private_by_name = {row["name"]: row for row in private_links}
    public_links = [row for row in links if row["public"]]
    if (
        len(private_by_name) != len(private_links)
        or set(private_by_name) != set(spec_links)
        or len(public_links) > 1
    ):
        raise ConflictError("active spec and live link inventory do not match")

    ports_by_node: dict[UUID, list[CurrentPort]] = {}
    endpoints: dict[UUID, list[str]] = {}
    port_keys: set[tuple[str, str]] = set()
    for row in ports:
        node = nodes_by_id.get(row["nodo_id"])
        link = links_by_id.get(row["enlace_id"])
        if node is None or link is None or row["public"] != link["public"]:
            raise ConflictError("live port references an invalid node or link")
        name = PUBLIC_LINK if link["public"] else link["name"]
        key = (name, node["name"])
        if key in port_keys:
            raise ConflictError("duplicate live port in update inventory")
        port_keys.add(key)
        ports_by_node.setdefault(node["id"], []).append(
            CurrentPort(port_id=row["id"], link_name=name, endpoint=node["name"])
        )
        endpoints.setdefault(link["id"], []).append(node["name"])

    current = CurrentState(
        vms=tuple(
            CurrentVM(
                node_id=nodes_by_name[name]["id"],
                name=name,
                flavor=vm.flavor,
                image=vm.image,
                public=vm.public,
                ports=tuple(ports_by_node.get(nodes_by_name[name]["id"], [])),
            )
            for name, vm in sorted(spec_vms.items())
        ),
        links=tuple(
            CurrentLink(
                link_id=row["id"],
                name=PUBLIC_LINK if row["public"] else row["name"],
                endpoints=tuple(sorted(endpoints.get(row["id"], []))),
                public=row["public"],
                cidr=row["cidr"],
            )
            for row in links
        ),
    )
    capacities: dict[str, LinkCapacity] = {}
    for row in private_links:
        if row["cidr"] is not None:
            network = ipaddress.ip_network(row["cidr"])
            capacities[row["name"]] = LinkCapacity(
                hosts=sum(1 for _ in network.hosts()),
                live_ports=len(endpoints.get(row["id"], [])),
            )

    flavors, images = await repository.load_catalog(
        connection, context["cluster_id"], include_inactive=True
    )
    cluster = context["cluster_name"]
    supports = load_capabilities(capabilities_dir, cluster)
    return UpdateInputs(
        slice_id=context["slice_id"],
        identity_scope=f"deployment:{deployment_id}",
        current=current,
        desired=desired,
        existing_ids=existing_ids_from_state(current),
        link_capacities=capacities,
        flavors=flavors,
        images=images,
        cluster_name=cluster,
        capabilities=load_adapter_capabilities(capabilities_dir, cluster),
        supports_public_access=supports.get("set_public_access", False),
    )
