from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from contracts.base import ContractModel
from contracts.errors import ErrorResponse
from contracts.plan import ActionOp, DeploymentPlan


class DeploymentOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    COMPENSATED = "COMPENSATED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED_BY_OPERATOR = "REJECTED_BY_OPERATOR"
    REJECTED_BY_SYSTEM = "REJECTED_BY_SYSTEM"


class ActionFact(ContractModel):
    action_id: str
    op: ActionOp
    succeeded: bool
    target_id: UUID
    facts: dict[str, Any] = Field(default_factory=dict)
    error: ErrorResponse | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "ActionFact":
        if self.succeeded and self.error is not None:
            raise ValueError("successful action facts cannot include an error")
        if not self.succeeded and self.error is None:
            raise ValueError("failed action facts require an error")
        return self


class DeploymentContext(ContractModel):
    deployment_id: int
    slice_id: int
    target_version_id: int
    cluster_name: str
    plan: DeploymentPlan


class ApprovalState(ContractModel):
    deployment_id: int
    status: ApprovalStatus
    timeout_seconds: int = Field(gt=0)
    reason: str | None = None


class RoundFacts(ContractModel):
    event_id: str
    round_num: int = Field(ge=1)
    actions: list[ActionFact]


class StartDeployment(ContractModel):
    event_id: str


class CommitDestroy(ContractModel):
    node_ids: list[UUID] = Field(default_factory=list)
    complete: bool


class CommitUpdate(ContractModel):
    removed_node_ids: list[UUID] = Field(default_factory=list)  # DELETE_VM exitoso sobre intents REMOVE
    updated_node_ids: list[UUID] = Field(default_factory=list)  # CREATE_VM o RESIZE_VM exitoso
    detached_port_ids: list[UUID] = Field(default_factory=list)  # DETACH_PORT exitoso
    complete: bool


class SliceRuntimeNode(ContractModel):
    node_id: UUID
    name: str
    server_id: int | None = None
    vnc_display: int | None = None
    cloud_init: bool


class SliceRuntimePort(ContractModel):
    port_id: UUID
    link_id: UUID
    node_id: UUID
    public: bool
    mac: str | None = None
    ip: str | None = None
    prefijo: int | None = None
    nic_index: int | None = None
    vlan_ctag: int | None = None


class SliceRuntimeLink(ContractModel):
    link_id: UUID
    public: bool


class SliceRuntime(ContractModel):
    slice_id: int
    public_vid: int | None = None
    nodes: list[SliceRuntimeNode] = Field(default_factory=list)
    ports: list[SliceRuntimePort] = Field(default_factory=list)
    links: list[SliceRuntimeLink] = Field(default_factory=list)


class FinishDeployment(ContractModel):
    event_id: str
    outcome: DeploymentOutcome
    operator_id: int | None = Field(default=None, gt=0)
    error: ErrorResponse | None = None


class DeploymentCreated(ContractModel):
    deployment_id: int
    workflow_id: str | None = None
    state: str
    approval_status: ApprovalStatus
    rejection_reason: str | None = None


class RoundFactsResult(ContractModel):
    event_id: str
    persisted_action_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


class ApprovalDecision(ContractModel):
    approved: bool
    operator_id: int = Field(gt=0)
    reason: str | None = None


class ApprovalDecisionResult(ContractModel):
    deployment_id: int
    status: ApprovalStatus
