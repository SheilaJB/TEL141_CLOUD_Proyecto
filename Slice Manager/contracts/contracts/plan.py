from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from contracts.base import ContractModel


class PlanType(StrEnum):
    DEPLOY = "DEPLOY"
    UPDATE = "UPDATE"
    START = "START"
    STOP = "STOP"
    DESTROY = "DESTROY"


class IntentKind(StrEnum):
    CREATE = "CREATE"
    REMOVE = "REMOVE"
    MODIFY = "MODIFY"
    RECREATE = "RECREATE"
    POWER = "POWER"


class Disruption(StrEnum):
    N0 = "N0"
    N1 = "N1"
    N2 = "N2"


class ActionOp(StrEnum):
    CREATE_VM = "CREATE_VM"
    DELETE_VM = "DELETE_VM"
    START_VM = "START_VM"
    STOP_VM = "STOP_VM"
    RESIZE_VM = "RESIZE_VM"
    CREATE_LINK = "CREATE_LINK"
    DELETE_LINK = "DELETE_LINK"
    ATTACH_PORT = "ATTACH_PORT"
    DETACH_PORT = "DETACH_PORT"


class Executor(StrEnum):
    ADAPTER = "adapter"
    REGISTRY = "registry"


class TargetKind(StrEnum):
    VM = "vm"
    LINK = "link"
    PORT = "port"


class ResourceVector(ContractModel):
    vcpu: int = Field(ge=0)
    ram_mb: int = Field(ge=0)
    disk_mb: int = Field(ge=0)


class Change(ContractModel):
    field: str
    before: Any = None
    after: Any = None


class IntentTarget(ContractModel):
    table: Literal["vms", "links"]
    id: UUID


class Intent(ContractModel):
    id: str
    target: IntentTarget
    kind: IntentKind
    strategy: str
    disruption: Disruption
    changes: list[Change] = Field(default_factory=list)


class ActionTarget(ContractModel):
    kind: TargetKind
    id: UUID
    link_id: UUID | None = None
    node_id: UUID | None = None

    @model_validator(mode="after")
    def validate_port_target(self) -> "ActionTarget":
        if self.kind == TargetKind.PORT and (self.link_id is None or self.node_id is None):
            raise ValueError("port targets require both link_id and node_id")
        if self.kind != TargetKind.PORT and (self.link_id is not None or self.node_id is not None):
            raise ValueError("only port targets may include link_id or node_id")
        return self


class Action(ContractModel):
    id: str
    op: ActionOp
    executor: Executor
    intent_id: str
    target: ActionTarget
    params: dict[str, Any] = Field(default_factory=dict)
    frozen: bool = True
    before: Any = None
    after: Any = None

    @model_validator(mode="after")
    def validate_network_action_params(self) -> "Action":
        if (
            self.frozen
            and self.op in {ActionOp.CREATE_LINK, ActionOp.ATTACH_PORT}
            and self.params
        ):
            raise ValueError(
                f"{self.op.value} parameters must be empty in the frozen plan"
            )
        return self


class PlanSummary(ContractModel):
    disruption_max: Disruption
    destructive: bool
    actions: int = Field(ge=0)
    resource_delta: ResourceVector
    resource_total_after: ResourceVector


class ReservationAllocation(ContractModel):
    node_id: UUID
    resources: ResourceVector


class ReservationResize(ContractModel):
    node_id: UUID
    before: ResourceVector
    after: ResourceVector


class ReservationRelease(ContractModel):
    node_id: UUID


class ReservationPlan(ContractModel):
    allocate: list[ReservationAllocation] = Field(default_factory=list)
    resize: list[ReservationResize] = Field(default_factory=list)
    release: list[ReservationRelease] = Field(default_factory=list)


class DeploymentPlan(ContractModel):
    plan_version: Literal[1] = 1
    type: PlanType
    summary: PlanSummary
    intents: list[Intent]
    reservation: ReservationPlan = Field(default_factory=ReservationPlan)
    point_of_no_return_round: int | None = Field(default=None, ge=1)
    rounds: list[list[Action]]

    @model_validator(mode="after")
    def validate_action_count(self) -> "DeploymentPlan":
        actions = [action for round_actions in self.rounds for action in round_actions]
        if len(actions) != self.summary.actions:
            raise ValueError("summary.actions must equal the number of planned actions")
        action_ids = [action.id for action in actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("action ids must be unique within a plan")
        return self
