from typing import Any

from pydantic import Field

from contracts.base import ContractModel
from contracts.plan import Action, ActionOp


class AdapterActionInput(ContractModel):
    idempotency_key: str
    deployment_id: int
    slice_id: int
    action: Action
    server_id: int | None = None


class AdapterActionOutput(ContractModel):
    action_id: str
    operation: ActionOp
    already_existed: bool = False
    already_absent: bool = False
    facts: dict[str, Any] = Field(default_factory=dict)
