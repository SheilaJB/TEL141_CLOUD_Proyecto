from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from contracts.plan import (
    Action,
    ActionOp,
    ActionTarget,
    Disruption,
    Executor,
    Intent,
    IntentKind,
    PlanSummary,
    ReservationAllocation,
    ReservationPlan,
    ReservationRelease,
    ReservationResize,
    ResourceVector,
    TargetKind,
)
from slice_manager.app.planning.definition import SliceDefinition, VMDefinition
from slice_manager.app.planning.errors import InvalidSpecError
from uuid import UUID

# Catálogo
@dataclass(frozen=True, slots=True)
class CatalogFlavor:
    name: str
    vcpu: int
    ram_mb: int
    disk_mb: int
    catalog_id: int
    active: bool = True

    def resources(self) -> ResourceVector:
        return ResourceVector(vcpu=self.vcpu, ram_mb=self.ram_mb, disk_mb=self.disk_mb)


@dataclass(frozen=True, slots=True)
class CatalogImage:
    name: str
    reference: str
    disk_size: int
    catalog_id: int
    cluster_compatible: str | None
    active: bool = True


def resolve_flavor(
    name: str,
    flavors: Mapping[str, CatalogFlavor],
    *,
    path: str,
    require_active: bool,
) -> CatalogFlavor:
    flavor = flavors.get(name)
    if flavor is None or flavor.name != name or (require_active and not flavor.active):
        raise InvalidSpecError(f"unknown or inactive flavor {name!r}", path=path)
    return flavor


def resolve_image(
    name: str,
    images: Mapping[str, CatalogImage],
    *,
    path: str,
    require_active: bool,
) -> CatalogImage:
    image = images.get(name)
    if image is None or image.name != name or (require_active and not image.active):
        raise InvalidSpecError(f"unknown or inactive image {name!r}", path=path)
    return image


def resolve_vm_catalog(
    vm: VMDefinition,
    *,
    flavors: Mapping[str, CatalogFlavor],
    images: Mapping[str, CatalogImage],
    cluster_name: str,
    supports_public_access: bool,
    require_active_flavor: bool = True,
    require_active_image: bool = True,
) -> tuple[CatalogFlavor, CatalogImage]:
    flavor = resolve_flavor(
        vm.flavor, flavors, path=f"vms[{vm.name}].flavor", require_active=require_active_flavor
    )
    image = resolve_image(
        vm.image, images, path=f"vms[{vm.name}].image", require_active=require_active_image
    )
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
    if image.disk_size > flavor.disk_mb:
        raise InvalidSpecError(
            f"DISK_TOO_SMALL: la imagen {image.name} requiere {image.disk_size} MB "
            f"y el flavor {flavor.name} tiene {flavor.disk_mb} MB",
            path=f"vms[{vm.name}].flavor",
        )
    if vm.public and not supports_public_access:
        raise InvalidSpecError(
            "public access is not supported by this cluster",
            path=f"vms[{vm.name}].public",
        )
    return flavor, image


# Validación de definición
def validate_definition(definition: SliceDefinition) -> None:
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

    connected_vms = {endpoint for link in definition.links for endpoint in link.endpoints}
    orphaned = sorted(vm_name_set - connected_vms)
    if orphaned:
        raise InvalidSpecError(f"VM(s) have no links: {', '.join(orphaned)}")


# Constructores de acciones (los IDs los asigna la fuente)
def create_vm_action(
    *,
    action_id: str,
    intent_id: str,
    node_id: UUID,
    vm: VMDefinition,
    flavor: CatalogFlavor,
    image: CatalogImage,
) -> Action:
    return Action(
        id=action_id,
        op=ActionOp.CREATE_VM,
        executor=Executor.ADAPTER,
        intent_id=intent_id,
        target=ActionTarget(kind=TargetKind.VM, id=node_id),
        params={
            "name": vm.name,
            "hostname": vm.name,
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


def create_link_action(
    *,
    action_id: str,
    intent_id: str,
    link_id: UUID,
    name: str,
    endpoint_node_ids: list[str],
    public: bool = False,
) -> Action:
    after: dict[str, object] = {"name": name, "endpoints": endpoint_node_ids}
    if public:
        after["public"] = True
    return Action(
        id=action_id,
        op=ActionOp.CREATE_LINK,
        executor=Executor.ADAPTER,
        intent_id=intent_id,
        target=ActionTarget(kind=TargetKind.LINK, id=link_id),
        params={},
        after=after,
    )


def attach_port_action(
    *,
    action_id: str,
    intent_id: str,
    port_id: UUID,
    link_id: UUID,
    node_id: UUID,
    public: bool = False,
) -> Action:
    after: dict[str, object] = {"link_id": str(link_id), "node_id": str(node_id)}
    if public:
        after["public"] = True
    return Action(
        id=action_id,
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
        after=after,
    )


def _flavor_params(flavor: CatalogFlavor) -> dict[str, object]:
    return {
        "name": flavor.name,
        "vcpu": flavor.vcpu,
        "ram_mb": flavor.ram_mb,
        "disk_mb": flavor.disk_mb,
    }


def _vm_op_action(
    op: ActionOp,
    *,
    action_id: str,
    intent_id: str,
    node_id: UUID,
    params: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
) -> Action:
    return Action(
        id=action_id,
        op=op,
        executor=Executor.ADAPTER,
        intent_id=intent_id,
        target=ActionTarget(kind=TargetKind.VM, id=node_id),
        params=params or {},
        after=after,
    )


def stop_vm_action(*, action_id: str, intent_id: str, node_id: UUID) -> Action:
    return _vm_op_action(
        ActionOp.STOP_VM, action_id=action_id, intent_id=intent_id, node_id=node_id
    )


def start_vm_action(*, action_id: str, intent_id: str, node_id: UUID) -> Action:
    return _vm_op_action(
        ActionOp.START_VM, action_id=action_id, intent_id=intent_id, node_id=node_id
    )


def delete_vm_action(*, action_id: str, intent_id: str, node_id: UUID) -> Action:
    return _vm_op_action(
        ActionOp.DELETE_VM, action_id=action_id, intent_id=intent_id, node_id=node_id
    )


def resize_vm_action(
    *,
    action_id: str,
    intent_id: str,
    node_id: UUID,
    before_flavor: CatalogFlavor,
    flavor: CatalogFlavor,
) -> Action:
    return _vm_op_action(
        ActionOp.RESIZE_VM,
        action_id=action_id,
        intent_id=intent_id,
        node_id=node_id,
        params={
            RESIZE_BEFORE_KEY: _flavor_params(before_flavor),
            RESIZE_AFTER_KEY: _flavor_params(flavor),
        },
        after={"flavor_ref": flavor.name},
    )


def detach_port_action(
    *,
    action_id: str,
    intent_id: str,
    port_id: UUID,
    link_id: UUID,
    node_id: UUID,
) -> Action:
    return Action(
        id=action_id,
        op=ActionOp.DETACH_PORT,
        executor=Executor.ADAPTER,
        intent_id=intent_id,
        target=ActionTarget(
            kind=TargetKind.PORT,
            id=port_id,
            link_id=link_id,
            node_id=node_id,
        ),
        params={},
    )


def delete_link_action(*, action_id: str, intent_id: str, link_id: UUID) -> Action:
    return Action(
        id=action_id,
        op=ActionOp.DELETE_LINK,
        executor=Executor.ADAPTER,
        intent_id=intent_id,
        target=ActionTarget(kind=TargetKind.LINK, id=link_id),
        params={},
    )


# Reserva
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
            # P47: sin cambio de flavor_ref no hay resize ni se exigen before/after.
            if flavor_change is None or flavor_change.before == flavor_change.after:
                continue
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


# Clase de reversibilidad, rondas con barrera y resumen
class Reversibility(StrEnum):
    I = "I"  # irreversible
    R = "R"  # reversible


# RESIZE_VM no está aquí: su clase depende de los parámetros (P35), ver action_class.
OP_CLASS: Mapping[ActionOp, Reversibility] = {
    ActionOp.CREATE_VM: Reversibility.R,
    ActionOp.CREATE_LINK: Reversibility.R,
    ActionOp.ATTACH_PORT: Reversibility.R,
    ActionOp.START_VM: Reversibility.R,
    ActionOp.STOP_VM: Reversibility.R,
    ActionOp.DELETE_VM: Reversibility.I,
    ActionOp.DELETE_LINK: Reversibility.I,
    ActionOp.DETACH_PORT: Reversibility.I,
    ActionOp.DESTROY_SLICE_HOST: Reversibility.I,
}

OP_DISRUPTION: Mapping[ActionOp, Disruption] = {
    ActionOp.CREATE_VM: Disruption.N0,
    ActionOp.CREATE_LINK: Disruption.N0,
    ActionOp.ATTACH_PORT: Disruption.N0,
    ActionOp.START_VM: Disruption.N1,
    ActionOp.STOP_VM: Disruption.N1,
    ActionOp.RESIZE_VM: Disruption.N1,
    ActionOp.DELETE_VM: Disruption.N2,
    ActionOp.DETACH_PORT: Disruption.N2,
    ActionOp.DELETE_LINK: Disruption.N2,
    ActionOp.DESTROY_SLICE_HOST: Disruption.N2,
}

_DESTRUCTIVE_OPS = frozenset(
    {
        ActionOp.DELETE_VM,
        ActionOp.DELETE_LINK,
        ActionOp.DETACH_PORT,
        ActionOp.DESTROY_SLICE_HOST,
    }
)

_DISRUPTION_ORDER = {Disruption.N0: 0, Disruption.N1: 1, Disruption.N2: 2}

# Claves de params de RESIZE_VM (ver resize_vm_action): ambas con la forma
# {"name", "vcpu", "ram_mb", "disk_mb"}. "before_flavor" es el flavor actual
# y "flavor" el deseado.
RESIZE_BEFORE_KEY = "before_flavor"
RESIZE_AFTER_KEY = "flavor"


def validate_resize(
    before_flavor: Mapping[str, object],
    after_flavor: Mapping[str, object],
    *,
    path: str = "$",
) -> None:
    """P14/P40: el disco solo puede crecer."""
    before_disk = before_flavor.get("disk_mb")
    after_disk = after_flavor.get("disk_mb")
    if before_disk is None or after_disk is None:
        raise InvalidSpecError("RESIZE_VM flavors must define disk_mb", path=path)
    if after_disk < before_disk:
        raise InvalidSpecError(
            f"DISK_SHRINK: el disco no se puede reducir ({before_disk} MB → {after_disk} MB)",
            path=path,
        )


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """P41/P43/P45: mismas claves que `supports` en capabilities/<cluster>.yaml."""

    hot_resize: bool = False
    hot_attach_port: bool = False
    hot_detach_port: bool = False

    @classmethod
    def from_supports(cls, supports: Mapping[str, bool]) -> "AdapterCapabilities":
        return cls(
            hot_resize=bool(supports.get("hot_resize", False)),
            hot_attach_port=bool(supports.get("hot_attach_port", False)),
            hot_detach_port=bool(supports.get("hot_detach_port", False)),
        )


def _resize_class(action: Action) -> Reversibility:
    """P35: R si el disco no cambia; I si crece. Reducir o faltar datos es error (defensa)."""
    before = action.params.get(RESIZE_BEFORE_KEY)
    after = action.params.get(RESIZE_AFTER_KEY)
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise InvalidSpecError("RESIZE_VM requires before and after flavors")
    validate_resize(before, after)
    return Reversibility.I if after["disk_mb"] > before["disk_mb"] else Reversibility.R


def action_class(action: Action) -> Reversibility:
    if action.op == ActionOp.RESIZE_VM:
        return _resize_class(action)
    return OP_CLASS[action.op]


def _action_sort_key(action: Action) -> tuple[str, int, str]:
    prefix = action.id.rstrip("0123456789")
    digits = action.id[len(prefix) :]
    return (prefix, int(digits) if digits else 0, action.id)


def _effective_classes(
    by_id: Mapping[str, Action],
    pending: Mapping[str, set[str]],
) -> dict[str, Reversibility]:
    """P34: una acción es I si lo es ella o alguna de las acciones de las que depende."""
    effective: dict[str, Reversibility] = {}
    visiting: set[str] = set()

    def visit(action_id: str) -> Reversibility:
        if action_id in effective:
            return effective[action_id]
        if action_id in visiting:
            raise InvalidSpecError("action dependency graph contains a cycle")
        visiting.add(action_id)
        result = action_class(by_id[action_id])
        for dependency in sorted(pending[action_id]):
            if visit(dependency) == Reversibility.I:
                result = Reversibility.I
        visiting.discard(action_id)
        effective[action_id] = result
        return result

    for action_id in sorted(by_id):
        visit(action_id)
    return effective


def order_rounds(
    actions: Iterable[Action],
    dependencies: Mapping[str, Iterable[str]] | None = None,
) -> tuple[list[list[Action]], int | None]:
    """Ordena en rondas por DAG sin mezclar clases I y R.

    La clase efectiva propaga I a los descendientes (P34). En cada paso las acciones R listas
    van primero; las I esperan a que no queden R ejecutables. Devuelve las rondas y el punto de
    no retorno (primera ronda I, base 1) o None.
    """
    action_list = list(actions)
    by_id = {action.id: action for action in action_list}
    if len(by_id) != len(action_list):
        raise InvalidSpecError("action ids must be unique")
    pending: dict[str, set[str]] = {}
    for action in action_list:
        deps = set((dependencies or {}).get(action.id, ()))
        unknown = deps - by_id.keys()
        if unknown:
            raise InvalidSpecError(
                f"action {action.id!r} depends on unknown action(s): {', '.join(sorted(unknown))}"
            )
        pending[action.id] = deps

    effective = _effective_classes(by_id, pending)

    rounds: list[list[Action]] = []
    point_of_no_return: int | None = None
    while pending:
        ready = [by_id[action_id] for action_id, deps in pending.items() if not deps]
        if not ready:
            raise InvalidSpecError("action dependency graph contains a cycle")
        reversible = [a for a in ready if effective[a.id] == Reversibility.R]
        chosen = sorted(reversible or ready, key=_action_sort_key)
        if point_of_no_return is None and not reversible:
            point_of_no_return = len(rounds) + 1
        rounds.append(chosen)
        done = {action.id for action in chosen}
        for action_id in done:
            del pending[action_id]
        for deps in pending.values():
            deps -= done
    return rounds, point_of_no_return


def assert_rounds_reversible(rounds: list[list[Action]]) -> None:
    """Garantiza que una fuente solo-R (Deploy) no emite acciones de clase I."""
    for round_index, round_actions in enumerate(rounds, start=1):
        mixed = [
            action.op.value
            for action in round_actions
            if action_class(action) != Reversibility.R
        ]
        if mixed:
            raise InvalidSpecError(
                f"round {round_index} mixes irreversible op(s) into a reversible DEPLOY round: "
                f"{', '.join(mixed)}"
            )


def summarize(
    actions: Iterable[Action],
    *,
    floor_disruption: Disruption,
    floor_destructive: bool,
    resource_delta: ResourceVector,
    resource_total_after: ResourceVector,
) -> PlanSummary:
    action_list = list(actions)
    disruption = floor_disruption
    for action in action_list:
        candidate = OP_DISRUPTION[action.op]
        if _DISRUPTION_ORDER[candidate] > _DISRUPTION_ORDER[disruption]:
            disruption = candidate
    destructive = floor_destructive or any(a.op in _DESTRUCTIVE_OPS for a in action_list)
    return PlanSummary(
        disruption_max=disruption,
        destructive=destructive,
        actions=len(action_list),
        resource_delta=resource_delta,
        resource_total_after=resource_total_after,
    )
