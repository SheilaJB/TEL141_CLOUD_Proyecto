from typing import Any
import ipaddress

from pydantic import Field, field_validator

from contracts.base import ContractModel
from contracts.plan import Action, ActionOp


class AdapterHost(ContractModel):
    server_id: int
    management_ip: str

    @field_validator("management_ip")
    @classmethod
    def validate_management_ip(cls, value: str) -> str:
        ipaddress.ip_address(value)
        return value


class AdapterActionInput(ContractModel):
    idempotency_key: str
    deployment_id: int
    slice_id: int
    action: Action
    server_id: int | None = None
    host: AdapterHost | None = None
    hosts: list[AdapterHost] = Field(default_factory=list)


class AdapterActionOutput(ContractModel):
    action_id: str
    operation: ActionOp
    idempotency_key: str
    already_existed: bool = False
    already_absent: bool = False
    facts: dict[str, Any] = Field(default_factory=dict)
