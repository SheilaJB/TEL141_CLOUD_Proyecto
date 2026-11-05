from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from contracts.plan import (
    Action,
    DeploymentPlan,
    Disruption,
    Intent,
    IntentKind,
    IntentTarget,
    PlanType,
    ResourceVector,
)
from slice_manager.app.planning.common import (
    OP_DISRUPTION,
    AdapterCapabilities,
    CatalogFlavor,
    CatalogImage,
    attach_port_action,
    calculate_reservation_plan,
    create_link_action,
    create_vm_action,
    delete_link_action,
    delete_vm_action,
    detach_port_action,
    order_rounds,
    resize_vm_action,
    resolve_flavor,
    resolve_vm_catalog,
    summarize,
    validate_definition,
    validate_resize,
)
from slice_manager.app.planning.definition import SliceDefinition
from slice_manager.app.planning.errors import InvalidSpecError
from slice_manager.app.planning.identities import EntityType, PlanIdentityFactory

__all__ = [
    "CurrentLink",
    "CurrentPort",
    "CurrentState",
    "CurrentVM",
    "LinkCapacity",
    "PUBLIC_LINK",
    "UpdateInputs",
    "build_update_plan",
    "existing_ids_from_state",
    "validate_update_links",
    "validate_update_resizes",
]

# Nombre natural del enlace público derivado (P33, P42).
PUBLIC_LINK = "public"

ExistingIds = Mapping[tuple[EntityType, tuple[str, ...]], UUID]

_DISRUPTION_RANK = {Disruption.N0: 0, Disruption.N1: 1, Disruption.N2: 2}


@dataclass(frozen=True, slots=True)
class CurrentPort:
    port_id: UUID
    link_name: str  # "public" para el puerto público
    endpoint: str  # nombre de la VM


@dataclass(frozen=True, slots=True)
class CurrentVM:
    node_id: UUID
    name: str
    flavor: str
    image: str
    public: bool
    ports: tuple[CurrentPort, ...] = ()


@dataclass(frozen=True, slots=True)
class CurrentLink:
    link_id: UUID
    name: str
    endpoints: tuple[str, ...]
    public: bool = False
    cidr: str | None = None


@dataclass(frozen=True, slots=True)
class CurrentState:
    vms: tuple[CurrentVM, ...]
    links: tuple[CurrentLink, ...]


@dataclass(frozen=True, slots=True)
class LinkCapacity:
    """D5: hosts del CIDR guardado y puertos vivos de un enlace privado existente."""

    hosts: int
    live_ports: int


@dataclass(frozen=True, slots=True)
class UpdateInputs:
    slice_id: int
    identity_scope: str
    current: CurrentState
    desired: SliceDefinition
    existing_ids: ExistingIds
    link_capacities: Mapping[str, LinkCapacity]
    flavors: Mapping[str, CatalogFlavor]
    images: Mapping[str, CatalogImage]
    cluster_name: str
    capabilities: AdapterCapabilities
    supports_public_access: bool = False


def existing_ids_from_state(current: CurrentState) -> dict[tuple[EntityType, tuple[str, ...]], UUID]:
    """Mapa de IDs existentes con la forma de P42, derivado de la IR actual."""
    result: dict[tuple[EntityType, tuple[str, ...]], UUID] = {}
    for vm in current.vms:
        result[("vm", (vm.name,))] = vm.node_id
        for port in vm.ports:
            result[("port", (port.link_name, port.endpoint))] = port.port_id
    for link in current.links:
        result[("link", (PUBLIC_LINK if link.public else link.name,))] = link.link_id
    return result


# Validaciones (P14, P40, D5)
def _catalog_flavor(
    flavors: Mapping[str, CatalogFlavor], name: str, path: str, *, require_active: bool = False
) -> CatalogFlavor:
    return resolve_flavor(name, flavors, path=path, require_active=require_active)


def validate_update_resizes(inputs: UpdateInputs) -> None:
    """DISK_SHRINK para cada VM emparejada cuyo flavor cambia. Va antes del planner."""
    current = {vm.name: vm for vm in inputs.current.vms}
    for vm in sorted(inputs.desired.vms, key=lambda item: item.name):
        existing = current.get(vm.name)
        if existing is None or existing.flavor == vm.flavor:
            continue
        path = f"vms[{vm.name}].flavor"
        before = _catalog_flavor(inputs.flavors, existing.flavor, path)
        after = _catalog_flavor(inputs.flavors, vm.flavor, path, require_active=True)
        validate_resize(
            {"disk_mb": before.disk_mb}, {"disk_mb": after.disk_mb}, path=path
        )


def validate_update_links(inputs: UpdateInputs) -> None:
    """NO_LINKS y LINK_CIDR_FULL. Va antes de reservar y de crear el deployment."""
    desired = inputs.desired
    if not desired.links:
        raise InvalidSpecError("NO_LINKS: the slice must define at least one link", path="links")
    current_endpoints: dict[str, set[str]] = {}
    for vm in inputs.current.vms:
        for port in vm.ports:
            current_endpoints.setdefault(port.link_name, set()).add(port.endpoint)
    existing_private = {link.name for link in inputs.current.links if not link.public}
    for link in sorted(desired.links, key=lambda item: item.name):
        if link.name not in existing_private:
            continue
        new_ports = set(link.endpoints) - current_endpoints.get(link.name, set())
        if not new_ports:
            continue
        path = f"links[{link.name}].endpoints"
        capacity = inputs.link_capacities.get(link.name)
        if capacity is None:
            raise InvalidSpecError(
                f"LINK_CAPACITY_UNKNOWN: no capacity data for link {link.name!r}", path=path
            )
        # Los puertos que se quitan no se restan: la IP vieja se libera solo al commit.
        if capacity.live_ports + len(new_ports) > capacity.hosts:
            raise InvalidSpecError(
                f"LINK_CIDR_FULL: link {link.name!r} has {capacity.live_ports} live port(s) and "
                f"{len(new_ports)} new one(s), but its CIDR only has {capacity.hosts} host(s)",
                path=path,
            )


# Constructor de plan
class _PlanBuilder:
    def __init__(self) -> None:
        self.actions: list[Action] = []
        self.dependencies: dict[str, set[str]] = {}
        self._intents: list[dict[str, object]] = []
        self._next_action = 1
        self._next_intent = 1

    def intent(
        self,
        *,
        table: str,
        target_id: UUID,
        kind: IntentKind,
        strategy: str,
        changes: list[dict[str, object]],
    ) -> str:
        intent_id = f"i{self._next_intent}"
        self._next_intent += 1
        self._intents.append(
            {
                "id": intent_id,
                "table": table,
                "target_id": target_id,
                "kind": kind,
                "strategy": strategy,
                "changes": changes,
            }
        )
        return intent_id

    def action_id(self) -> str:
        action_id = f"a{self._next_action}"
        self._next_action += 1
        return action_id

    def add(self, action: Action, after: set[str] | None = None) -> None:
        self.actions.append(action)
        self.dependencies[action.id] = set(after or ())

    def depend(self, action_id: str, *after: str) -> None:
        self.dependencies[action_id].update(after)

    def build_intents(self) -> list[Intent]:
        result: list[Intent] = []
        for spec in self._intents:
            disruption = Disruption.N0
            for action in self.actions:
                if action.intent_id != spec["id"]:
                    continue
                candidate = OP_DISRUPTION[action.op]
                if _DISRUPTION_RANK[candidate] > _DISRUPTION_RANK[disruption]:
                    disruption = candidate
            result.append(
                Intent(
                    id=spec["id"],
                    target=IntentTarget(table=spec["table"], id=spec["target_id"]),
                    kind=spec["kind"],
                    strategy=spec["strategy"],
                    disruption=disruption,
                    changes=spec["changes"],
                )
            )
        return result


def build_update_plan(inputs: UpdateInputs) -> DeploymentPlan:
    desired = inputs.desired
    capabilities = inputs.capabilities
    validate_definition(desired)

    resolved: dict[str, tuple[CatalogFlavor, CatalogImage]] = {}
    current_by_name = {vm.name: vm for vm in inputs.current.vms}
    for vm in sorted(desired.vms, key=lambda item: item.name):
        current_vm = current_by_name.get(vm.name)
        resolved[vm.name] = resolve_vm_catalog(
            vm,
            flavors=inputs.flavors,
            images=inputs.images,
            cluster_name=inputs.cluster_name,
            supports_public_access=inputs.supports_public_access,
            require_active_flavor=current_vm is None or vm.flavor != current_vm.flavor,
            require_active_image=current_vm is None or vm.image != current_vm.image,
        )
    validate_update_resizes(inputs)
    validate_update_links(inputs)

    identities = PlanIdentityFactory(
        slice_id=inputs.slice_id,
        scope=inputs.identity_scope,
        existing_ids={
            **existing_ids_from_state(inputs.current),
            **inputs.existing_ids,
        },
    )

    current_vms = {vm.name: vm for vm in inputs.current.vms}
    desired_vms = {vm.name: vm for vm in desired.vms}
    removed_vm_names = sorted(set(current_vms) - set(desired_vms))
    node_ids: dict[str, UUID] = {}
    for name in sorted(set(current_vms) | set(desired_vms)):
        node_ids[name] = (
            current_vms[name].node_id
            if name in current_vms
            else identities.entity_id("vm", name)
        )

    current_ports = {
        (port.link_name, port.endpoint): port
        for vm in inputs.current.vms
        for port in vm.ports
    }
    desired_ports: set[tuple[str, str]] = {
        (link.name, endpoint) for link in desired.links for endpoint in link.endpoints
    }
    public_vm_names = sorted(vm.name for vm in desired.vms if vm.public)
    desired_ports.update((PUBLIC_LINK, name) for name in public_vm_names)

    new_by_vm: dict[str, list[str]] = {}
    for label, vm_name in sorted(desired_ports - set(current_ports)):
        new_by_vm.setdefault(vm_name, []).append(label)
    removed_by_vm: dict[str, list[str]] = {}
    for label, vm_name in sorted(set(current_ports) - desired_ports):
        removed_by_vm.setdefault(vm_name, []).append(label)

    builder = _PlanBuilder()
    create_vm_ids: dict[str, str] = {}
    delete_vm_ids: dict[str, str] = {}
    recreated: set[str] = set()
    vm_attach_ids: dict[str, set[str]] = {}

    # VMs nuevas, redimensionadas y recreadas
    for vm in sorted(desired.vms, key=lambda item: item.name):
        flavor, image = resolved[vm.name]
        node_id = node_ids[vm.name]
        existing = current_vms.get(vm.name)
        if existing is None:
            intent_id = builder.intent(
                table="vms",
                target_id=node_id,
                kind=IntentKind.CREATE,
                strategy="create VM",
                changes=[
                    {"field": "name", "before": None, "after": vm.name},
                    {"field": "flavor_ref", "before": None, "after": vm.flavor},
                    {"field": "image_ref", "before": None, "after": vm.image},
                    {"field": "public", "before": None, "after": vm.public},
                ],
            )
            create_vm_ids[vm.name] = builder.action_id()
            builder.add(
                create_vm_action(
                    action_id=create_vm_ids[vm.name],
                    intent_id=intent_id,
                    node_id=node_id,
                    vm=vm,
                    flavor=flavor,
                    image=image,
                )
            )
            continue

        flavor_changed = existing.flavor != vm.flavor
        image_changed = existing.image != vm.image
        new_ports = new_by_vm.get(vm.name, [])
        removed_ports = removed_by_vm.get(vm.name, [])
        recreate = (
            image_changed
            or (bool(new_ports) and not capabilities.hot_attach_port)
            or (bool(removed_ports) and not capabilities.hot_detach_port)
        )
        changes: list[dict[str, object]] = [
            {"field": "name", "before": vm.name, "after": vm.name}
        ]
        if flavor_changed:
            changes.append(
                {"field": "flavor_ref", "before": existing.flavor, "after": vm.flavor}
            )
        if image_changed:
            changes.append(
                {"field": "image_ref", "before": existing.image, "after": vm.image}
            )
        if existing.public != vm.public:
            changes.append(
                {"field": "public", "before": existing.public, "after": vm.public}
            )
        if recreate:
            if new_ports or removed_ports:
                changes.append(
                    {
                        "field": "links",
                        "before": sorted(port.link_name for port in existing.ports),
                        "after": sorted(label for label, name in desired_ports if name == vm.name),
                    }
                )
            intent_id = builder.intent(
                table="vms",
                target_id=node_id,
                kind=IntentKind.RECREATE,
                strategy="recreate VM keeping its node",
                changes=changes,
            )
            recreated.add(vm.name)
            delete_vm_ids[vm.name] = builder.action_id()
            builder.add(
                delete_vm_action(
                    action_id=delete_vm_ids[vm.name],
                    intent_id=intent_id,
                    node_id=node_id,
                )
            )
            create_vm_ids[vm.name] = builder.action_id()
            builder.add(
                create_vm_action(
                    action_id=create_vm_ids[vm.name],
                    intent_id=intent_id,
                    node_id=node_id,
                    vm=vm,
                    flavor=flavor,
                    image=image,
                ),
                after={delete_vm_ids[vm.name]},
            )
        elif flavor_changed:
            intent_id = builder.intent(
                table="vms",
                target_id=node_id,
                kind=IntentKind.MODIFY,
                strategy="resize VM",
                changes=changes,
            )
            builder.add(
                resize_vm_action(
                    action_id=builder.action_id(),
                    intent_id=intent_id,
                    node_id=node_id,
                    before_flavor=_catalog_flavor(
                        inputs.flavors, existing.flavor, f"vms[{vm.name}].flavor"
                    ),
                    flavor=flavor,
                )
            )

    # VMs eliminadas: el DELETE_VM no depende de nada; sus DETACH_PORT lo esperan
    for name in removed_vm_names:
        existing = current_vms[name]
        intent_id = builder.intent(
            table="vms",
            target_id=existing.node_id,
            kind=IntentKind.REMOVE,
            strategy="remove VM",
            changes=[
                {"field": "name", "before": name, "after": None},
                {"field": "flavor_ref", "before": existing.flavor, "after": None},
                {"field": "image_ref", "before": existing.image, "after": None},
                {"field": "public", "before": existing.public, "after": None},
            ],
        )
        delete_vm_ids[name] = builder.action_id()
        builder.add(
            delete_vm_action(
                action_id=delete_vm_ids[name],
                intent_id=intent_id,
                node_id=existing.node_id,
            )
        )

    # Enlaces y puertos
    current_private = {link.name: link for link in inputs.current.links if not link.public}
    current_public = next((link for link in inputs.current.links if link.public), None)
    desired_private = {link.name for link in desired.links}
    labels = [(name, False) for name in sorted(set(current_private) | desired_private)]
    labels.append((PUBLIC_LINK, True))

    for label, public in labels:
        existing_link = current_public if public else current_private.get(label)
        wanted = bool(public_vm_names) if public else label in desired_private
        current_eps = sorted(vm for name, vm in current_ports if name == label)
        desired_eps = sorted(vm for name, vm in desired_ports if name == label)
        new_eps = sorted(set(desired_eps) - set(current_eps))
        removed_eps = sorted(set(current_eps) - set(desired_eps))

        if existing_link is None and wanted:
            kind = IntentKind.CREATE
            link_id = identities.entity_id("link", label)
        elif existing_link is not None and not wanted:
            kind = IntentKind.REMOVE
            link_id = existing_link.link_id
        elif existing_link is not None and (new_eps or removed_eps):
            kind = IntentKind.MODIFY
            link_id = existing_link.link_id
        else:
            continue

        before_ids = sorted(str(node_ids[vm]) for vm in current_eps)
        after_ids = sorted(str(node_ids[vm]) for vm in desired_eps)
        if kind == IntentKind.CREATE:
            link_changes: list[dict[str, object]] = [
                {"field": "name", "before": None, "after": label},
                {"field": "endpoints", "before": None, "after": after_ids},
            ]
            if public:
                link_changes.insert(1, {"field": "public", "before": None, "after": True})
            strategy = "create link and attach endpoints"
        elif kind == IntentKind.REMOVE:
            link_changes = [
                {"field": "name", "before": label, "after": None},
                {"field": "endpoints", "before": before_ids, "after": None},
            ]
            strategy = "detach endpoints and delete link"
        else:
            link_changes = [{"field": "endpoints", "before": before_ids, "after": after_ids}]
            strategy = "attach or detach link endpoints"
        intent_id = builder.intent(
            table="links",
            target_id=link_id,
            kind=kind,
            strategy=strategy,
            changes=link_changes,
        )

        create_link_id: str | None = None
        if kind == IntentKind.CREATE:
            create_link_id = builder.action_id()
            builder.add(
                create_link_action(
                    action_id=create_link_id,
                    intent_id=intent_id,
                    link_id=link_id,
                    name=label,
                    endpoint_node_ids=after_ids,
                    public=public,
                )
            )
        for vm_name in new_eps:
            attach_id = builder.action_id()
            after: set[str] = set()
            if create_link_id is not None:
                after.add(create_link_id)
            if vm_name in recreated:
                after.add(delete_vm_ids[vm_name])
            builder.add(
                attach_port_action(
                    action_id=attach_id,
                    intent_id=intent_id,
                    port_id=identities.entity_id("port", label, vm_name),
                    link_id=link_id,
                    node_id=node_ids[vm_name],
                    public=public,
                ),
                after=after,
            )
            if vm_name in create_vm_ids:
                vm_attach_ids.setdefault(vm_name, set()).add(attach_id)

        link_removal_deps: set[str] = set()
        for vm_name in removed_eps:
            detach_id = builder.action_id()
            builder.add(
                detach_port_action(
                    action_id=detach_id,
                    intent_id=intent_id,
                    port_id=current_ports[(label, vm_name)].port_id,
                    link_id=link_id,
                    node_id=node_ids[vm_name],
                ),
                after={delete_vm_ids[vm_name]} if vm_name in delete_vm_ids else None,
            )
            link_removal_deps.add(detach_id)
        if kind == IntentKind.REMOVE:
            builder.add(
                delete_link_action(
                    action_id=builder.action_id(),
                    intent_id=intent_id,
                    link_id=link_id,
                ),
                after=link_removal_deps,
            )

    for vm_name, create_id in create_vm_ids.items():
        builder.depend(create_id, *vm_attach_ids.get(vm_name, ()))

    intents = builder.build_intents()
    ordered_rounds, point_of_no_return = order_rounds(builder.actions, builder.dependencies)
    reservation, resource_delta = calculate_reservation_plan(intents, inputs.flavors)
    total_after = {"vcpu": 0, "ram_mb": 0, "disk_mb": 0}
    for name in sorted(desired_vms):
        for key, value in resolved[name][0].resources().model_dump().items():
            total_after[key] += value
    return DeploymentPlan(
        type=PlanType.UPDATE,
        summary=summarize(
            builder.actions,
            floor_disruption=Disruption.N0,
            floor_destructive=False,
            resource_delta=resource_delta,
            resource_total_after=ResourceVector(**total_after),
        ),
        intents=intents,
        reservation=reservation,
        point_of_no_return_round=point_of_no_return,
        rounds=ordered_rounds,
    )
