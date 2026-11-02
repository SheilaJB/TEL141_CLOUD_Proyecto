from collections.abc import Mapping
from uuid import UUID

from contracts.plan import (
    Action,
    DeploymentPlan,
    Disruption,
    Intent,
    IntentKind,
    IntentTarget,
    PlanType,
)
from slice_manager.app.planning.common import (
    CatalogFlavor,
    CatalogImage,
    assert_rounds_reversible,
    attach_port_action,
    calculate_reservation_plan,
    create_link_action,
    create_vm_action,
    order_rounds,
    resolve_vm_catalog,
    summarize,
    validate_definition,
)
from slice_manager.app.planning.definition import SliceDefinition, parse_definition
from slice_manager.app.planning.identities import PlanIdentityFactory

__all__ = [
    "CatalogFlavor",
    "CatalogImage",
    "build_initial_deploy_plan",
    "calculate_reservation_plan",
]


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
    validate_definition(definition)

    resolved: dict[str, tuple[CatalogFlavor, CatalogImage, UUID]] = {}
    for vm in sorted(definition.vms, key=lambda item: item.name):
        flavor, image = resolve_vm_catalog(
            vm,
            flavors=flavors,
            images=images,
            cluster_name=cluster_name,
            supports_public_access=supports_public_access,
        )
        resolved[vm.name] = (flavor, image, identities.entity_id("vm", vm.name))

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
    vm_attach_actions: dict[str, set[str]] = {vm.name: set() for vm in definition.vms}

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
            create_vm_action(
                action_id=create_action_id,
                intent_id=intent_id,
                node_id=node_id,
                vm=vm,
                flavor=flavor,
                image=image,
            )
        )
    for link in sorted(definition.links, key=lambda item: item.name):
        link_id = identities.entity_id("link", link.name)
        intent_id = f"i{next_intent}"
        next_intent += 1
        endpoint_node_ids = sorted(str(resolved[endpoint][2]) for endpoint in link.endpoints)
        intents.append(
            Intent(
                id=intent_id,
                target=IntentTarget(table="links", id=link_id),
                kind=IntentKind.CREATE,
                strategy="create link and attach endpoints",
                disruption=Disruption.N0,
                changes=[
                    {"field": "name", "before": None, "after": link.name},
                    {"field": "endpoints", "before": None, "after": endpoint_node_ids},
                ],
            )
        )
        create_action_id = f"a{next_action}"
        next_action += 1
        link_create_actions[link.name] = create_action_id
        append_action(
            create_link_action(
                action_id=create_action_id,
                intent_id=intent_id,
                link_id=link_id,
                name=link.name,
                endpoint_node_ids=endpoint_node_ids,
            )
        )
        for endpoint in sorted(link.endpoints):
            port_id = identities.entity_id("port", link.name, endpoint)
            attach_id = f"a{next_action}"
            next_action += 1
            # Deploy: taps antes de QEMU (hot_attach_port=false). UPDATE+hot_attach queda fuera de alcance.
            append_action(
                attach_port_action(
                    action_id=attach_id,
                    intent_id=intent_id,
                    port_id=port_id,
                    link_id=link_id,
                    node_id=resolved[endpoint][2],
                ),
                after={link_create_actions[link.name]},
            )
            vm_attach_actions[endpoint].add(attach_id)

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
            create_link_action(
                action_id=public_create_action_id,
                intent_id=public_intent_id,
                link_id=public_link_id,
                name="public",
                endpoint_node_ids=[str(node_id) for node_id in public_node_ids],
                public=True,
            )
        )
        for vm in public_vms:
            node_id = resolved[vm.name][2]
            public_port_id = identities.entity_id("port", "public", vm.name)
            public_attach_action_id = f"a{next_action}"
            next_action += 1
            append_action(
                attach_port_action(
                    action_id=public_attach_action_id,
                    intent_id=public_intent_id,
                    port_id=public_port_id,
                    link_id=public_link_id,
                    node_id=node_id,
                    public=True,
                ),
                after={public_create_action_id},
            )
            vm_attach_actions[vm.name].add(public_attach_action_id)

    for vm_name, create_action_id in vm_create_actions.items():
        dependencies[create_action_id] = set(vm_attach_actions[vm_name])

    ordered_rounds, point_of_no_return = order_rounds(actions, dependencies)
    assert_rounds_reversible(ordered_rounds)

    reservation, resource_delta = calculate_reservation_plan(
        intents,
        flavors,
    )
    return DeploymentPlan(
        type=PlanType.DEPLOY,
        summary=summarize(
            actions,
            floor_disruption=Disruption.N0,
            floor_destructive=False,
            resource_delta=resource_delta,
            resource_total_after=resource_delta,
        ),
        intents=intents,
        reservation=reservation,
        point_of_no_return_round=point_of_no_return,
        rounds=ordered_rounds,
    )
