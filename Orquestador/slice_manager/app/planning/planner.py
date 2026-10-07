from collections.abc import Mapping
from dataclasses import dataclass
from graphlib import TopologicalSorter
from uuid import UUID

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
    ReservationAllocation,
    ReservationPlan,
    ResourceVector,
    TargetKind,
)
from slice_manager.app.planning.definition import SliceDefinition, parse_definition
from slice_manager.app.planning.errors import InvalidSpecError
from slice_manager.app.planning.identities import PlanIdentityFactory


@dataclass(frozen=True, slots=True)
class CatalogFlavor:
    name: str
    vcpu: int
    ram_mb: int
    disk_mb: int
    catalog_id: int

    def resources(self) -> ResourceVector:
        return ResourceVector(vcpu=self.vcpu, ram_mb=self.ram_mb, disk_mb=self.disk_mb)


@dataclass(frozen=True, slots=True)
class CatalogImage:
    name: str
    reference: str
    catalog_id: int
    cluster_compatible: str | None


def build_initial_deploy_plan(
    definition: object,
    *,
    slice_id: int,
    identity_scope: str,
    flavors: Mapping[str, CatalogFlavor],
    images: Mapping[str, CatalogImage],
    cluster_name: str,
    supports_public_access: bool = False,
) -> DeploymentPlan:
    parsed = parse_definition(definition)
    return _build_plan(
        parsed,
        flavors=flavors,
        images=images,
        cluster_name=cluster_name,
        identities=PlanIdentityFactory(slice_id=slice_id, scope=identity_scope),
        supports_public_access=supports_public_access,
    )


def _build_plan(
    definition: SliceDefinition,
    *,
    flavors: Mapping[str, CatalogFlavor],
    images: Mapping[str, CatalogImage],
    cluster_name: str,
    identities: PlanIdentityFactory,
    supports_public_access: bool,
) -> DeploymentPlan:
    vm_names = [vm.name for vm in definition.vms]
    if len(vm_names) != len(set(vm_names)):
        raise InvalidSpecError("VM names must be unique")
    link_names = [link.name for link in definition.links]
    if len(link_names) != len(set(link_names)):
        raise InvalidSpecError("link names must be unique")

    vm_name_set = set(vm_names)
    for link in definition.links:
        if len(link.endpoints) < 2:
            raise InvalidSpecError(
                f"link {link.name!r} must have at least two endpoints",
                path=f"links[{link.name}].endpoints",
            )
        if len(link.endpoints) != len(set(link.endpoints)):
            raise InvalidSpecError(
                f"link {link.name!r} contains a duplicate endpoint",
                path=f"links[{link.name}].endpoints",
            )
        unknown = sorted(set(link.endpoints) - vm_name_set)
        if unknown:
            raise InvalidSpecError(
                f"link {link.name!r} refers to unknown VM(s): {', '.join(unknown)}",
                path=f"links[{link.name}].endpoints",
            )

    resolved: dict[str, tuple[CatalogFlavor, CatalogImage, UUID]] = {}
    allocated: list[ReservationAllocation] = []
    resource_delta = {"vcpu": 0, "ram_mb": 0, "disk_mb": 0}
    for vm in sorted(definition.vms, key=lambda item: item.name):
        flavor = flavors.get(vm.flavor)
        if flavor is None or flavor.name != vm.flavor:
            raise InvalidSpecError(f"unknown or inactive flavor {vm.flavor!r}", path=f"vms[{vm.name}].flavor")
        image = images.get(vm.image)
        if image is None or image.name != vm.image:
            raise InvalidSpecError(f"unknown or inactive image {vm.image!r}", path=f"vms[{vm.name}].image")
        if not image.reference.strip():
            raise InvalidSpecError(
                f"image {image.name!r} has no infrastructure reference",
                path=f"vms[{vm.name}].image",
            )
        if image.cluster_compatible not in (None, cluster_name):
            raise InvalidSpecError(
                f"image {image.name!r} is not compatible with cluster {cluster_name!r}",
                path=f"vms[{vm.name}].image",
            )
        if min(flavor.vcpu, flavor.ram_mb, flavor.disk_mb) <= 0:
            raise InvalidSpecError(
                f"flavor {flavor.name!r} contains non-positive resource values",
                path=f"vms[{vm.name}].flavor",
            )
        if vm.public and not supports_public_access:
            raise InvalidSpecError(
                "public access is not supported by this cluster",
                path=f"vms[{vm.name}].public",
            )

        node_id = identities.entity_id("vm", vm.name)
        resolved[vm.name] = (flavor, image, node_id)
        resources = flavor.resources()
        allocated.append(ReservationAllocation(node_id=node_id, resources=resources))
        resource_delta["vcpu"] += resources.vcpu
        resource_delta["ram_mb"] += resources.ram_mb
        resource_delta["disk_mb"] += resources.disk_mb

    connected_vms = {
        endpoint for link in definition.links for endpoint in link.endpoints
    }
    orphaned = sorted(vm_name_set - connected_vms)
    if orphaned:
        raise InvalidSpecError(f"VM(s) have no links: {', '.join(orphaned)}")

    intents: list[Intent] = []
    actions: list[Action] = []
    dependencies: dict[str, set[str]] = {}

    def append_action(action: Action, *, after: set[str] | None = None) -> None:
        actions.append(action)
        dependencies[action.id] = set(after or ())

    next_action = 1
    next_intent = 1
    vm_create_actions: dict[str, str] = {}
    link_create_actions: dict[str, str] = {}

    for vm in sorted(definition.vms, key=lambda item: item.name):
        flavor, image, node_id = resolved[vm.name]
        intent_id = f"i{next_intent}"
        next_intent += 1
        intents.append(
            Intent(
                id=intent_id,
                target=IntentTarget(table="vms", id=node_id),
                kind=IntentKind.CREATE,
                strategy="create VM",
                disruption=Disruption.N0,
                changes=[
                    {"field": "name", "before": None, "after": vm.name},
                    {"field": "flavor_ref", "before": None, "after": vm.flavor},
                    {"field": "image_ref", "before": None, "after": vm.image},
                    {"field": "public", "before": None, "after": vm.public},
                ],
            )
        )
        create_action_id = f"a{next_action}"
        next_action += 1
        vm_create_actions[vm.name] = create_action_id
        append_action(
            Action(
                id=create_action_id,
                op=ActionOp.CREATE_VM,
                executor=Executor.ADAPTER,
                intent_id=intent_id,
                target=ActionTarget(kind=TargetKind.VM, id=node_id),
                params={
                    "name": vm.name,
                    "image": {"reference": image.reference},
                    "flavor": {
                        "name": flavor.name,
                        "vcpu": flavor.vcpu,
                        "ram_mb": flavor.ram_mb,
                        "disk_mb": flavor.disk_mb,
                    },
                    "public": False,
                },
                after={
                    "name": vm.name,
                    "flavor_ref": vm.flavor,
                    "image_ref": vm.image,
                    "public": vm.public,
                },
            )
        )
        if vm.public:
            public_action_id = f"a{next_action}"
            next_action += 1
            append_action(
                Action(
                    id=public_action_id,
                    op=ActionOp.SET_PUBLIC,
                    executor=Executor.ADAPTER,
                    intent_id=intent_id,
                    target=ActionTarget(kind=TargetKind.VM, id=node_id),
                    params={"public": True},
                    before={"public": False},
                    after={"public": True},
                ),
                after={create_action_id},
            )

    link_ids: dict[str, UUID] = {}
    for link in sorted(definition.links, key=lambda item: item.name):
        link_id = identities.entity_id("link", link.name)
        link_ids[link.name] = link_id
        intent_id = f"i{next_intent}"
        next_intent += 1
        intents.append(
            Intent(
                id=intent_id,
                target=IntentTarget(table="links", id=link_id),
                kind=IntentKind.CREATE,
                strategy="create link and attach endpoints",
                disruption=Disruption.N0,
                changes=[
                    {"field": "name", "before": None, "after": link.name},
                    {
                        "field": "endpoints",
                        "before": None,
                        "after": sorted(
                            str(resolved[endpoint][2]) for endpoint in link.endpoints
                        ),
                    },
                ],
            )
        )
        create_action_id = f"a{next_action}"
        next_action += 1
        link_create_actions[link.name] = create_action_id
        append_action(
            Action(
                id=create_action_id,
                op=ActionOp.CREATE_LINK,
                executor=Executor.ADAPTER,
                intent_id=intent_id,
                target=ActionTarget(kind=TargetKind.LINK, id=link_id),
                params={"name": link.name},
                after={
                    "name": link.name,
                    "endpoints": sorted(
                        str(resolved[endpoint][2]) for endpoint in link.endpoints
                    ),
                },
            )
        )
        for endpoint in sorted(link.endpoints):
            port_id = identities.entity_id("port", link.name, endpoint)
            attach_id = f"a{next_action}"
            next_action += 1
            node_id = resolved[endpoint][2]
            append_action(
                Action(
                    id=attach_id,
                    op=ActionOp.ATTACH_PORT,
                    executor=Executor.ADAPTER,
                    intent_id=intent_id,
                    target=ActionTarget(
                        kind=TargetKind.PORT,
                        id=port_id,
                        link_id=link_id,
                        node_id=node_id,
                    ),
                    params={"link_name": link.name, "vm_name": endpoint},
                    after={"link_id": str(link_id), "node_id": str(node_id)},
                ),
                after={link_create_actions[link.name], vm_create_actions[endpoint]},
            )

    ordered_rounds: list[list[Action]] = []
    sorter = TopologicalSorter(dependencies)
    sorter.prepare()
    action_by_id = {action.id: action for action in actions}
    while sorter.is_active():
        ready = sorted(sorter.get_ready(), key=lambda action_id: int(action_id[1:]))
        if not ready:
            raise InvalidSpecError("action dependency graph contains a cycle")
        ordered_rounds.append([action_by_id[action_id] for action_id in ready])
        sorter.done(*ready)

    return DeploymentPlan(
        type=PlanType.DEPLOY,
        summary=PlanSummary(
            disruption_max=Disruption.N0,
            destructive=False,
            actions=len(actions),
            resource_delta=ResourceVector(**resource_delta),
        ),
        intents=intents,
        reservation=ReservationPlan(allocate=allocated),
        point_of_no_return_round=None,
        rounds=ordered_rounds,
    )
