from uuid import UUID

from pydantic import AliasChoices, Field

from contracts.base import ContractModel


class NetworkLinkAllocation(ContractModel):
    link_id: UUID
    # LEGACY BROKEN compatibility input: NM currently still emits these names.
    vlan_s: int | None = Field(
        default=None,
        ge=100,
        le=999,
        validation_alias=AliasChoices("vlan_s", "vlan_stag"),
    )
    vlan_c: int | None = Field(
        default=None,
        ge=2,
        le=1999,
        validation_alias=AliasChoices("vlan_c", "vlan_ctag"),
    )
    cidr: str | None = None
    publico: bool = Field(
        default=False,
        validation_alias=AliasChoices("publico", "public"),
    )


class NetworkPortAllocation(ContractModel):
    port_id: UUID
    link_id: UUID
    node_id: UUID
    # LEGACY BROKEN compatibility input: the old producer calls this "public".
    publico: bool = Field(
        default=False,
        validation_alias=AliasChoices("publico", "public"),
    )
    ip: str | None = None
    mac: str | None = None
    nic_index: int | None = Field(default=None, ge=0)


class NetworkAllocation(ContractModel):
    deployment_id: int
    slice_id: int
    links: list[NetworkLinkAllocation] = Field(default_factory=list)
    ports: list[NetworkPortAllocation] = Field(default_factory=list)
    public_vid: int = Field(default=4000, ge=1, le=4094)


class NetworkRollbackResult(ContractModel):
    deployment_id: int
    cleared_links: int = Field(ge=0)
    cleared_ports: int = Field(ge=0)
