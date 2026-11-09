from contracts.placement import (
    PlacementResult,

)
from contracts.adapter import AdapterActionInput, AdapterHost
from contracts.servers import ServerHost
from contracts.network import NetworkAllocation, NetworkPortAllocation
from contracts.plan import ActionOp, DeploymentPlan
from contracts.queues import (
    ACTIVITY_BUILD_ADAPTER_ACTIONS,
    ACTIVITY_COMMIT_DESTROY,
    ACTIVITY_COMMIT_INVENTORY,
    ACTIVITY_COMMIT_UPDATE,
    ACTIVITY_CREATE_INVENTORY,
    ACTIVITY_GET_SERVER_DIRECTORY,
)
from contracts.registry import (
    ApprovalState,
    CommitDestroy,
    CommitUpdate,
    DeploymentContext,
    FinishDeployment,
    RoundFacts,
    RoundFactsResult,
    SliceRuntime,
    SliceRuntimeLink,
    SliceRuntimePort,
    StartDeployment,
)
from temporalio import activity
from uuid import UUID
from contracts.queues import (
    ACTIVITY_FINISH_DEPLOYMENT,
    ACTIVITY_GET_APPROVAL_STATE,
    ACTIVITY_GET_PLAN,
    ACTIVITY_RECORD_ROUND_FACTS,

    ACTIVITY_START_DEPLOYMENT,
)
from temporal_worker.worker.activities.slice_manager_client import SliceManagerClient


class DeploymentActivities:
    def __init__(
        self,
        slice_manager: SliceManagerClient,
        public_gateway: str = "10.60.5.1",
    ) -> None:
        self._slice_manager = slice_manager
        self._public_gateway = public_gateway

    @staticmethod
    def _build_adapter_actions(
        deployment_id: int,
        plan: DeploymentPlan,
        placement: PlacementResult,
        network: NetworkAllocation,
        server_directory: list[ServerHost],
        vnc_displays: dict[str, int],
        runtime: SliceRuntime | None = None,
        public_gateway: str = "10.60.5.1",
    ) -> list[AdapterActionInput]:
        if placement.deployment_id != deployment_id or network.deployment_id != deployment_id:
            raise ValueError("decision results belong to a different deployment")
        link_allocations = {item.link_id: item for item in network.links}
        port_allocations = {item.port_id: item for item in network.ports}
        ports_by_node: dict[UUID, list[NetworkPortAllocation]] = {}
        for port in network.ports:
            ports_by_node.setdefault(port.node_id, []).append(port)
        detach_port_ids = {
            action.target.id
            for round_actions in plan.rounds
            for action in round_actions
            if action.op == ActionOp.DETACH_PORT
        }
        server_by_node = placement.server_by_node
        vnc_by_node = vnc_displays
        runtime_ports_by_node: dict[UUID, list[SliceRuntimePort]] = {}
        runtime_ports_by_id: dict[UUID, SliceRuntimePort] = {}
        runtime_links: dict[UUID, SliceRuntimeLink] = {}
        if runtime is not None:
            # Solo se usa BD para nodos que no vienen de Placement (Update).
            server_by_node = {
                **placement.server_by_node,
                **{
                    node.node_id: node.server_id
                    for node in runtime.nodes
                    if node.server_id is not None
                    and node.node_id not in placement.server_by_node
                },
            }
            vnc_by_node = {
                **vnc_displays,
                **{
                    str(node.node_id): node.vnc_display
                    for node in runtime.nodes
                    if node.vnc_display is not None
                    and str(node.node_id) not in vnc_displays
                },
            }
            for port in runtime.ports:
                runtime_ports_by_node.setdefault(port.node_id, []).append(port)
                runtime_ports_by_id[port.port_id] = port
            runtime_links = {link.link_id: link for link in runtime.links}
        actions: list[AdapterActionInput] = []

        def host_for(server_id: int) -> AdapterHost:
            server = next(
                (item for item in server_directory if item.server_id == server_id),
                None,
            )
            if server is None:
                raise ValueError(
                    f"missing management_ip for Placement server {server_id}"
                )
            return AdapterHost(
                server_id=server.server_id,
                management_ip=server.management_ip,
                ssh_port=server.ssh_port,
            )

        def display_for(node_id: UUID) -> int:
            value = vnc_by_node.get(str(node_id))
            if value is None:
                raise ValueError(f"missing vnc_display for node {node_id}")
            return int(value)

        def nic_for(port: NetworkPortAllocation) -> dict[str, object]:
            if (
                not port.mac
                or not port.ip
                or port.nic_index is None
                or port.prefijo is None
            ):
                raise ValueError(
                    f"incomplete network allocation for port {port.port_id}"
                )
            if port.publico and not port.gateway:
                raise ValueError(f"missing public gateway for port {port.port_id}")
            if not port.publico and port.gateway is not None:
                raise ValueError(
                    f"private port {port.port_id} must not have a gateway"
                )
            return {
                "port_id": str(port.port_id),
                "ip": port.ip,
                "mac": port.mac,
                "nic_index": port.nic_index,
                "publico": port.publico,
                "prefijo": port.prefijo,
                "gateway": port.gateway,
                "public_vid": network.public_vid,
                "vlan_ctag": next(
                    (
                        link.vlan_c
                        for link in network.links
                        if link.link_id == port.link_id
                    ),
                    None,
                ),
            }

        def runtime_nic(port: SliceRuntimePort) -> dict[str, object]:
            if not port.mac or not port.ip or port.nic_index is None:
                raise ValueError(
                    f"incomplete live network port {port.port_id}"
                )
            return {
                "port_id": str(port.port_id),
                "ip": port.ip,
                "mac": port.mac,
                "nic_index": port.nic_index,
                "publico": port.public,
                "prefijo": port.prefijo,
                "gateway": public_gateway if port.public else None,
                "public_vid": (
                    runtime.public_vid
                    if runtime is not None and runtime.public_vid is not None
                    else network.public_vid
                ),
                "vlan_ctag": port.vlan_ctag,
            }

        def reject_commands(value: object) -> None:
            if isinstance(value, str) and any(
                token in value.lower()
                for token in ("ovs-vsctl", "ip link", "ip netns", "iptables", "br-inet")
            ):
                raise ValueError("adapter action contains an infrastructure command")
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key) in {"dnat_port", "ssh_publico", "br-inet", "vlan_tag"}:
                        raise ValueError(
                            f"adapter action contains forbidden field {key!r}"
                        )
                    reject_commands(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    reject_commands(child)

        for round_actions in plan.rounds:
            for action in round_actions:
                params = dict(action.params)
                server_id: int | None = None
                host = None
                hosts: list[AdapterHost] = []
                if action.op == ActionOp.CREATE_LINK:
                    allocation = link_allocations.get(action.target.id)
                    if allocation is None:
                        raise ValueError(
                            f"missing network allocation for link {action.target.id}"
                        )
                    params["network"] = allocation.model_dump(mode="json")
                    if allocation.publico:
                        params["public_vid"] = network.public_vid
                        params["vlan_stag"] = network.public_vid
                    else:
                        if allocation.vlan_s is None:
                            raise ValueError(
                                f"missing vlan_s for private link {action.target.id}"
                            )
                        params["vlan_stag"] = allocation.vlan_s
                    link_server_ids: set[int] = set()
                    for port in network.ports:
                        if port.link_id != action.target.id:
                            continue
                        server = server_by_node.get(port.node_id)
                        if server is None:
                            raise ValueError(
                                f"missing server for node {port.node_id}"
                            )
                        link_server_ids.add(server)
                    if not link_server_ids:
                        raise ValueError(
                            f"missing endpoints for link {action.target.id}"
                        )
                    hosts = [
                        host_for(server)
                        for server in sorted(link_server_ids)
                    ]
                elif action.op == ActionOp.ATTACH_PORT:
                    allocation = port_allocations.get(action.target.id)
                    if allocation is None:
                        raise ValueError(
                            f"missing network allocation for port {action.target.id}"
                        )
                    params["network"] = allocation.model_dump(mode="json")
                    params["vlan_ctag"] = (
                        next(
                            (
                                link.vlan_c
                                for link in network.links
                                if link.link_id == allocation.link_id
                            ),
                            None,
                        )
                    )
                    params["publico"] = allocation.publico
                    params["public_vid"] = network.public_vid
                    # Nodo existente: server_by_node se completa desde slice_nodo.server_id.
                    server_id = server_by_node.get(action.target.node_id)
                    if server_id is None:
                        raise ValueError(
                            f"missing server for node {action.target.node_id}"
                        )
                    host = host_for(server_id)
                elif action.op == ActionOp.CREATE_VM:
                    server_id = server_by_node.get(action.target.id)
                    if server_id is None:
                        raise ValueError(
                            f"missing server for node {action.target.id}"
                        )
                    host = host_for(server_id)
                    params["vnc_display"] = display_for(action.target.id)
                    new_nics = [
                        nic_for(port)
                        for port in sorted(
                            ports_by_node.get(action.target.id, []),
                            key=lambda item: (
                                item.nic_index if item.nic_index is not None else -1
                            ),
                        )
                    ]
                    if runtime is None:
                        params["nics"] = new_nics
                    else:
                        live_nics = [
                            runtime_nic(port)
                            for port in runtime_ports_by_node.get(action.target.id, [])
                            if port.port_id not in detach_port_ids
                        ]
                        params["nics"] = sorted(
                            [*live_nics, *new_nics],
                            key=lambda item: (
                                item["nic_index"]
                                if item["nic_index"] is not None
                                else -1,
                                str(item["port_id"]),
                            ),
                        )
                    params["hostname"] = params["hostname"] if "hostname" in params else "ubuntu"
                elif action.op in {ActionOp.DELETE_VM, ActionOp.RESIZE_VM}:
                    server_id = server_by_node.get(action.target.id)
                    if server_id is None:
                        raise ValueError(
                            f"missing server for node {action.target.id}"
                        )
                    host = host_for(server_id)
                elif action.op == ActionOp.DETACH_PORT:
                    node_id = action.target.node_id
                    if node_id is None:
                        raise ValueError(
                            f"DETACH_PORT action {action.id} has no node target"
                        )
                    server_id = server_by_node.get(node_id)
                    if server_id is None:
                        raise ValueError(f"missing server for node {node_id}")
                    host = host_for(server_id)
                    port = runtime_ports_by_id.get(action.target.id)
                    if port is None or port.node_id != node_id or port.link_id != action.target.link_id:
                        raise ValueError(f"missing runtime port {action.target.id}")
                    params["publico"] = port.public
                elif action.op == ActionOp.DELETE_LINK:
                    link = runtime_links.get(action.target.id)
                    if link is None:
                        raise ValueError(f"missing runtime link {action.target.id}")
                    params["publico"] = link.public
                    link_server_ids = set()
                    for port in runtime_ports_by_id.values():
                        if port.link_id != action.target.id:
                            continue
                        server = server_by_node.get(port.node_id)
                        if server is None:
                            raise ValueError(f"missing server for node {port.node_id}")
                        link_server_ids.add(server)
                    hosts = [host_for(server) for server in sorted(link_server_ids)]
                enriched = action.model_copy(
                    update={"params": params, "frozen": False}
                )
                reject_commands(enriched.params)
                actions.append(
                    AdapterActionInput(
                        idempotency_key=f"{deployment_id}:{action.id}",
                        deployment_id=deployment_id,
                        slice_id=network.slice_id,
                        action=enriched,
                        server_id=server_id,
                        host=host,
                        hosts=hosts,
                    )
                )
        return actions

    @staticmethod
    def _missing_runtime_nodes(
        plan: DeploymentPlan,
        placement: PlacementResult,
        vnc_displays: dict[str, int],
    ) -> set[UUID]:
        required_servers: set[UUID] = set()
        required_vnc: set[UUID] = set()
        intents = {intent.id: intent for intent in plan.intents}
        for round_actions in plan.rounds:
            for action in round_actions:
                if action.op in {
                    ActionOp.CREATE_VM,
                    ActionOp.DELETE_VM,
                    ActionOp.RESIZE_VM,
                }:
                    required_servers.add(action.target.id)
                    if action.op == ActionOp.CREATE_VM:
                        required_vnc.add(action.target.id)
                elif action.op in {ActionOp.ATTACH_PORT, ActionOp.DETACH_PORT}:
                    if action.target.node_id is not None:
                        required_servers.add(action.target.node_id)
                elif action.op == ActionOp.CREATE_LINK:
                    after = action.after
                    if isinstance(after, dict):
                        endpoints = after.get("endpoints")
                        if isinstance(endpoints, list):
                            for value in endpoints:
                                required_servers.add(UUID(str(value)))
                elif action.op == ActionOp.DELETE_LINK:
                    intent = intents.get(action.intent_id)
                    if intent is not None:
                        for change in intent.changes:
                            if change.field == "endpoints" and isinstance(change.before, list):
                                required_servers.update(UUID(str(value)) for value in change.before)
        missing = {
            node_id
            for node_id in required_servers
            if node_id not in placement.server_by_node
        }
        missing.update(
            node_id
            for node_id in required_vnc
            if str(node_id) not in vnc_displays
        )
        return missing

    @activity.defn(name=ACTIVITY_BUILD_ADAPTER_ACTIONS)
    async def build_adapter_actions(
        self,
        deployment_id: int,
        plan: DeploymentPlan,
        placement: PlacementResult,
        network: NetworkAllocation,
        server_directory: list[ServerHost],
        vnc_displays: dict[str, int],
    ) -> list[AdapterActionInput]:
        runtime = None
        missing = self._missing_runtime_nodes(plan, placement, vnc_displays)
        needs_network_runtime = any(
            action.op in {ActionOp.DELETE_LINK, ActionOp.DETACH_PORT}
            for round_actions in plan.rounds
            for action in round_actions
        )
        if needs_network_runtime:
            runtime = await self._slice_manager.get_slice_runtime(network.slice_id)
        elif missing:
            runtime = await self._slice_manager.get_slice_runtime(
                network.slice_id,
                sorted(missing, key=str),
            )
        return self._build_adapter_actions(
            deployment_id,
            plan,
            placement,
            network,
            server_directory,
            vnc_displays,
            runtime=runtime,
            public_gateway=self._public_gateway,
        )

    @activity.defn(name=ACTIVITY_CREATE_INVENTORY)
    async def create_inventory(self, deployment_id: int) -> None:
        await self._slice_manager.create_inventory(deployment_id)

    @activity.defn(name=ACTIVITY_COMMIT_INVENTORY)
    async def commit_inventory(self, deployment_id: int) -> None:
        await self._slice_manager.commit_inventory(deployment_id)

    @activity.defn(name=ACTIVITY_COMMIT_DESTROY)
    async def commit_destroy(self, deployment_id: int, request: CommitDestroy) -> None:
        await self._slice_manager.commit_destroy(deployment_id, request)

    @activity.defn(name=ACTIVITY_COMMIT_UPDATE)
    async def commit_update(self, deployment_id: int, request: CommitUpdate) -> None:
        await self._slice_manager.commit_update(deployment_id, request)

    @activity.defn(name=ACTIVITY_GET_SERVER_DIRECTORY)
    async def get_server_directory(self, slice_id: int) -> list[ServerHost]:
        return await self._slice_manager.get_server_directory(slice_id)

    @activity.defn(name=ACTIVITY_GET_PLAN)
    async def get_plan(self, deployment_id: int) -> DeploymentContext:
        activity.logger.info(
            "Loading frozen deployment plan",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": activity.info().workflow_id,
                "idempotency_key": deployment_id,
            },
        )
        return await self._slice_manager.get_plan(deployment_id)

    @activity.defn(name=ACTIVITY_GET_APPROVAL_STATE)
    async def get_approval_state(self, deployment_id: int) -> ApprovalState:
        return await self._slice_manager.get_approval_state(deployment_id)

    @activity.defn(name=ACTIVITY_START_DEPLOYMENT)
    async def start_deployment(self, deployment_id: int) -> None:
        activity.logger.info(
            "Starting deployment state transition",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": activity.info().workflow_id,
                "idempotency_key": f"{deployment_id}:start",
            },
        )
        await self._slice_manager.start_deployment(
            deployment_id,
            StartDeployment(event_id=f"{deployment_id}:start"),
        )

    @activity.defn(name=ACTIVITY_RECORD_ROUND_FACTS)
    async def record_round_facts(
        self, deployment_id: int, facts: RoundFacts
    ) -> RoundFactsResult:
        activity.logger.info(
            "Recording deployment round results",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": activity.info().workflow_id,
                "idempotency_key": facts.event_id,
            },
        )
        result = await self._slice_manager.record_round_facts(deployment_id, facts)
        for warning in result.warnings:
            activity.logger.warning(
                "Deployment facts were not fully persisted",
                extra={"deployment_id": deployment_id, "warning": warning},
            )
        return result

    @activity.defn(name=ACTIVITY_FINISH_DEPLOYMENT)
    async def finish_deployment(
        self, deployment_id: int, finish: FinishDeployment
    ) -> None:
        activity.logger.info(
            "Finishing deployment state transition",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": activity.info().workflow_id,
                "idempotency_key": finish.event_id,
            },
        )
        await self._slice_manager.finish_deployment(deployment_id, finish)
