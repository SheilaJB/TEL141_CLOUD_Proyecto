from typing import Literal

from pydantic import Field, ValidationError, field_validator

from contracts.base import ContractModel
from slice_manager.app.planning.errors import InvalidSpecError


class VMDefinitionShape(ContractModel):
    name: str = Field(min_length=1)
    flavor: str = Field(min_length=1)
    image: str = Field(min_length=1)
    public: bool = False

    @field_validator("name", "flavor", "image")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError("must not be blank")
        return result


class LinkDefinitionShape(ContractModel):
    name: str = Field(min_length=1)
    endpoints: list[str]

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError("must not be blank")
        return result

    @field_validator("endpoints")
    @classmethod
    def strip_endpoints(cls, value: list[str]) -> list[str]:
        endpoints = [endpoint.strip() for endpoint in value]
        if any(not endpoint for endpoint in endpoints):
            raise ValueError("endpoint names must not be blank")
        return endpoints


class SliceDefinitionShape(ContractModel):
    schema_version: Literal[1]
    vms: list[VMDefinitionShape]
    links: list[LinkDefinitionShape]


class VMDefinition(VMDefinitionShape):
    pass


class LinkDefinition(LinkDefinitionShape):
    pass


class SliceDefinition(ContractModel):
    schema_version: Literal[1]
    vms: list[VMDefinition]
    links: list[LinkDefinition]


def parse_definition(value: object) -> SliceDefinition:
    try:
        return SliceDefinition.model_validate(value)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or '$'}: {issue['msg']}"
            for issue in exc.errors()
        )
        raise InvalidSpecError(details) from exc


def parse_definition_shape(value: object) -> SliceDefinitionShape:
    try:
        return SliceDefinitionShape.model_validate(value)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or '$'}: {issue['msg']}"
            for issue in exc.errors()
        )
        raise InvalidSpecError(details) from exc
