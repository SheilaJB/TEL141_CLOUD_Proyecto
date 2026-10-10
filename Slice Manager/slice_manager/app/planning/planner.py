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
    ReservationRelease,
    ReservationResize,
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


def calculate_reservation_plan(
    intents: list[Intent],
    flavors: Mapping[str, CatalogFlavor],
) -> tuple[ReservationPlan, ResourceVector]:
    """Calculate reservations and capacity demand without side effects."""
    allocate: list[ReservationAllocation] = []
    resize: list[ReservationResize] = []
    release: list[ReservationRelease] = []
    demand = {"vcpu": 0, "ram_mb": 0, "disk_mb": 0}

    def flavor_resources(value: object, path: str) -> ResourceVector:
        if not isinstance(value, str) or value not in flavors:
            raise InvalidSpecError(f"unknown or inactive flavor {value!r}", path=path)
        return flavors[value].resources()

    for intent in intents:
        if intent.target.table != "vms":
            continue
        changes = {change.field: change for change in intent.changes}
        flavor_change = changes.get("flavor_ref")
        before = (
            flavor_resources(
                flavor_change.before,
                f"intents[{intent.id}].changes[flavor_ref].before",
            )
            if flavor_change is not None and flavor_change.before is not None
            else None
        )
        after = (
            flavor_resources(
                flavor_change.after,
                f"intents[{intent.id}].changes[flavor_ref].after",
            )
            if flavor_change is not None and flavor_change.after is not None
            else None
        )

        if intent.kind == IntentKind.CREATE:
            if after is None:
                raise InvalidSpecError(
                    f"create intent {intent.id!r} has no target flavor",
                    path=f"intents[{intent.id}]",
                )
            allocate.append(ReservationAllocation(node_id=intent.target.id, resources=after))
            for key, value in after.model_dump().items():
                demand[key] += value
        elif intent.kind == IntentKind.REMOVE:
            if before is None:
                raise InvalidSpecError(
                    f"remove intent {intent.id!r} has no previous flavor",
                    path=f"intents[{intent.id}]",
                )
            release.append(ReservationRelease(node_id=intent.target.id))
        elif intent.kind in {IntentKind.MODIFY, IntentKind.RECREATE}:
            if before is None or after is None:
                raise InvalidSpecError(
                    f"{intent.kind.value.lower()} intent {intent.id!r} must define before and after flavors",
                    path=f"intents[{intent.id}]",
                )
            resize.append(
                ReservationResize(
                    node_id=intent.target.id,
                    before=before,
                    after=after,
                )
            )
            before_values = before.model_dump()
            after_values = after.model_dump()
            for key in before_values:
                difference = after_values[key] - before_values[key]
                if difference > 0:
                    demand[key] += difference

    return ReservationPlan(allocate=allocate, resize=resize, release=release), ResourceVector(
        **demand
    )


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
                    "public": vm.public,
                },
                after={
                    "name": vm.name,
                    "flavor_ref": vm.flavor,
                    "image_ref": vm.image,
                    "public": vm.public,
                },
            )
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
                params={},
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
                    params={},
                    after={"link_id": str(link_id), "node_id": str(node_id)},
                ),
                after={link_create_actions[link.name], vm_create_actions[endpoint]},
            )

    public_vms = [
        vm
        for vm in sorted(definition.vms, key=lambda item: item.name)
        if vm.public
    ]
    if public_vms:
        public_link_id = identities.entity_id("link", "public")
        public_intent_id = "i_pub"
        public_node_ids = [resolved[vm.name][2] for vm in public_vms]
        intents.append(
            Intent(
                id=public_intent_id,
                target=IntentTarget(table="links", id=public_link_id),
                kind=IntentKind.CREATE,
                strategy="create derived public link and attach public ports",
                disruption=Disruption.N0,
                changes=[
                    {"field": "name", "before": None, "after": "public"},
                    {"field": "public", "before": None, "after": True},
                    {
                        "field": "endpoints",
                        "before": None,
                        "after": [str(node_id) for node_id in public_node_ids],
                    },
                ],
            )
        )
        public_create_action_id = f"a{next_action}"
        next_action += 1
        append_action(
            Action(
                id=public_create_action_id,
                op=ActionOp.CREATE_LINK,
                executor=Executor.ADAPTER,
                intent_id=public_intent_id,
                target=ActionTarget(kind=TargetKind.LINK, id=public_link_id),
                params={},
                after={
                    "name": "public",
                    "public": True,
                    "endpoints": [str(node_id) for node_id in public_node_ids],
                },
            )
        )
        for vm in public_vms:
            node_id = resolved[vm.name][2]
            public_port_id = identities.entity_id("port", "public", vm.name)
            public_attach_action_id = f"a{next_action}"
            next_action += 1
            append_action(
                Action(
                    id=public_attach_action_id,
                    op=ActionOp.ATTACH_PORT,
                    executor=Executor.ADAPTER,
                    intent_id=public_intent_id,
                    target=ActionTarget(
                        kind=TargetKind.PORT,
                        id=public_port_id,
                        link_id=public_link_id,
                        node_id=node_id,
                    ),
                    params={},
                    after={
                        "link_id": str(public_link_id),
                        "node_id": str(node_id),
                        "public": True,
                    },
                ),
                after={public_create_action_id, vm_create_actions[vm.name]},
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

    reservation, resource_delta = calculate_reservation_plan(
        intents,
        flavors,
    )
    return DeploymentPlan(
        type=PlanType.DEPLOY,
        summary=PlanSummary(
            disruption_max=Disruption.N0,
            destructive=False,
            actions=len(actions),
            resource_delta=resource_delta,
            resource_total_after=resource_delta,
        ),
        intents=intents,
        reservation=reservation,
        point_of_no_return_round=None,
        rounds=ordered_rounds,
    )
