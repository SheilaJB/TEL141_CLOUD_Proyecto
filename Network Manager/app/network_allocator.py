"""Pure network allocation rules used by REST and Temporal adapters."""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from typing import Iterable
from uuid import UUID


class NetworkAllocationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LinkInput:
    link_id: UUID
    public: bool
    node_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class LinkAllocation:
    link_id: UUID
    public: bool
    vlan_stag: int | None
    vlan_ctag: int | None
    cidr: ipaddress.IPv4Network | None


@dataclass(frozen=True)
class PortAllocation:
    port_id: UUID
    link_id: UUID
    node_id: UUID
    public: bool
    ip: ipaddress.IPv4Address
    mac: str
    nic_index: int


class NetworkAllocator:
    def __init__(
        self,
        *,
        private_base: str = "192.168.1.0/24",
        public_base: str = "10.60.5.0/24",
        public_vid: int = 4000,
    ) -> None:
        self.private_base = ipaddress.ip_network(private_base)
        self.public_base = ipaddress.ip_network(public_base)
        self.public_vid = public_vid

    def allocate(
        self,
        *,
        slice_id: int,
        vlan_stag: int,
        links: Iterable[LinkInput],
        used_private_cidrs: Iterable[str] = (),
        used_private_ctags: Iterable[int] = (),
        used_public_ips: Iterable[str] = (),
        used_macs: Iterable[str] = (),
    ) -> tuple[list[LinkAllocation], list[PortAllocation]]:
        link_list = list(links)
        private_links = [link for link in link_list if not link.public]
        if len(private_links) > 32:
            raise NetworkAllocationError(
                "NET_TOO_MANY_LINKS",
                "a slice cannot contain more than 32 private links",
            )
        if not 100 <= vlan_stag <= 999:
            raise NetworkAllocationError("NET_VLAN_S_EXHAUSTED", "invalid S-TAG")

        used_cidrs = {ipaddress.ip_network(value) for value in used_private_cidrs}
        used_ctags = set(used_private_ctags)
        used_ips = {ipaddress.ip_address(value) for value in used_public_ips}
        used_mac_values = {value.lower() for value in used_macs}
        links_out: list[LinkAllocation] = []
        ports_out: list[PortAllocation] = []
        private_index = 0

        for link in link_list:
            if link.public:
                links_out.append(LinkAllocation(link.link_id, True, None, None, None))
                public_ips = self._allocate_public_ips(
                    link, used_ips, used_mac_values, slice_id
                )
                ports_out.extend(public_ips)
                continue

            prefix = self._private_prefix(len(link.node_ids))
            cidr = self._first_free_subnet(prefix, used_cidrs, private_index)
            private_index = int(cidr.network_address) - int(self.private_base.network_address)
            used_cidrs.add(cidr)
            ctag = next((value for value in range(2, 2000) if value not in used_ctags), None)
            if ctag is None:
                raise NetworkAllocationError(
                    "NET_VLAN_C_EXHAUSTED", "no private C-TAG is available"
                )
            used_ctags.add(ctag)
            links_out.append(LinkAllocation(link.link_id, False, vlan_stag, ctag, cidr))
            hosts = list(cidr.hosts())
            if len(hosts) < len(link.node_ids):
                raise NetworkAllocationError("NET_IP_EXHAUSTED", "private CIDR is too small")
            for nic_index, (node_id, address) in enumerate(
                zip(link.node_ids, hosts), start=1
            ):
                mac = self._mac(slice_id, link.link_id, node_id)
                if mac in used_mac_values:
                    raise NetworkAllocationError("NET_MAC_COLLISION", "MAC collision detected")
                used_mac_values.add(mac)
                ports_out.append(
                    PortAllocation(
                        port_id=UUID(int=(link.link_id.int ^ node_id.int)),
                        link_id=link.link_id,
                        node_id=node_id,
                        public=False,
                        ip=address,
                        mac=mac,
                        nic_index=nic_index,
                    )
                )
        return links_out, ports_out

    def _allocate_public_ips(
        self,
        link: LinkInput,
        used_ips: set[ipaddress.IPv4Address],
        used_macs: set[str],
        slice_id: int,
    ) -> list[PortAllocation]:
        hosts = iter(list(self.public_base.hosts())[1:])
        result: list[PortAllocation] = []
        for node_id in link.node_ids:
            address = next((item for item in hosts if item not in used_ips), None)
            if address is None:
                raise NetworkAllocationError(
                    "NET_PUBLIC_IP_EXHAUSTED", "no public IP is available"
                )
            used_ips.add(address)
            mac = self._mac(slice_id, link.link_id, node_id)
            if mac in used_macs:
                raise NetworkAllocationError("NET_MAC_COLLISION", "MAC collision detected")
            used_macs.add(mac)
            result.append(
                PortAllocation(
                    port_id=UUID(int=(link.link_id.int ^ node_id.int)),
                    link_id=link.link_id,
                    node_id=node_id,
                    public=True,
                    ip=address,
                    mac=mac,
                    nic_index=0,
                )
            )
        return result

    def _private_prefix(self, endpoint_count: int) -> int:
        required = endpoint_count + 2
        prefix = 32 - (required - 1).bit_length()
        return max(prefix - 1, self.private_base.prefixlen)

    def _first_free_subnet(
        self,
        prefix: int,
        used: set[ipaddress.IPv4Network],
        start_offset: int,
    ) -> ipaddress.IPv4Network:
        for subnet in self.private_base.subnets(new_prefix=prefix):
            if int(subnet.network_address) - int(self.private_base.network_address) < start_offset:
                continue
            if not any(subnet.overlaps(existing) for existing in used):
                return subnet
        raise NetworkAllocationError(
            "NET_PRIVATE_BLOCK_EXHAUSTED", "no private CIDR block is available"
        )

    @staticmethod
    def _mac(slice_id: int, link_id: UUID, node_id: UUID) -> str:
        digest = hashlib.sha256(f"{slice_id}:{link_id}:{node_id}".encode()).digest()
        return "52:54:00:" + ":".join(f"{byte:02x}" for byte in digest[:3])
