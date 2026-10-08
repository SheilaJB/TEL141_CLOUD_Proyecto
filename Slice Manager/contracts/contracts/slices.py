from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from contracts.base import ContractModel
from contracts.plan import Disruption, Intent, ResourceVector


class CreateSliceRequest(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    zona_id: int = Field(gt=0)
    spec: Any | None = None
    plantilla_id: int | None = Field(default=None, gt=0)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("name must not be blank")
        return name


class SliceCreated(ContractModel):
    slice_id: int
    version_id: int
    numero_version: int
    estado: str
    cluster: str


class CreateVersionRequest(ContractModel):
    from_version: int | None = Field(default=None, gt=0)
    empty: bool = False

    @model_validator(mode="after")
    def validate_source(self) -> "CreateVersionRequest":
        if self.from_version is not None and self.empty:
            raise ValueError("from_version and empty cannot be used together")
        return self


class VersionCreated(ContractModel):
    slice_id: int
    version_id: int
    numero_version: int
    estado: str


class ValidationPreview(ContractModel):
    slice_id: int
    version_id: int
    numero_version: int
    valid: bool = True
    layers: list[str]
    resource_delta: ResourceVector
    requires_approval: bool


class PlanPreviewSummary(ContractModel):
    disruption_max: Disruption
    destructive: bool
    resource_delta: ResourceVector


class PlanPreview(ContractModel):
    slice_id: int
    version_id: int
    numero_version: int
    identity_scope: Literal["preview"] = "preview"
    summary: PlanPreviewSummary
    requires_approval: bool
    intents: list[Intent]
