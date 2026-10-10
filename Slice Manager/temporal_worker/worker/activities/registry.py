from contracts.placement import (
    PlacementReserveRequest,
    PlacementResult,
    PlacementRollbackRequest,
    PlacementReserveResponse,
    PlacementRollbackResponse,
)
from contracts.adapter import AdapterActionInput
from contracts.network import NetworkAllocation
from contracts.plan import ActionOp, DeploymentPlan
from contracts.queues import ACTIVITY_BUILD_ADAPTER_ACTIONS
from contracts.registry import (
    ApprovalState,
    DeploymentContext,
    FinishDeployment,
    RoundFacts,
    RoundFactsResult,
    StartDeployment,
)
from temporalio import activity
from contracts.queues import (
    ACTIVITY_FINISH_DEPLOYMENT,
    ACTIVITY_GET_APPROVAL_STATE,
    ACTIVITY_GET_PLAN,
    ACTIVITY_RECORD_ROUND_FACTS,
    ACTIVITY_RESERVE_PLACEMENT,
    ACTIVITY_ROLLBACK_PLACEMENT,
    ACTIVITY_START_DEPLOYMENT,
)
from temporal_worker.worker.activities.slice_manager_client import SliceManagerClient


class DeploymentActivities:
    def __init__(
        self,
        slice_manager: SliceManagerClient,
    ) -> None:
        self._slice_manager = slice_manager

    @staticmethod
    def _build_adapter_actions(
        deployment_id: int,
        plan: DeploymentPlan,
        placement: PlacementResult,
        network: NetworkAllocation,
    ) -> list[AdapterActionInput]:
        if placement.deployment_id != deployment_id or network.deployment_id != deployment_id:
            raise ValueError("decision results belong to a different deployment")
        link_allocations = {item.link_id: item for item in network.links}
        port_allocations = {item.port_id: item for item in network.ports}
        actions: list[AdapterActionInput] = []

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
                if action.op == ActionOp.CREATE_LINK:
                    allocation = link_allocations.get(action.target.id)
                    if allocation is None:
                        raise ValueError(
                            f"missing network allocation for link {action.target.id}"
                        )
                    params["network"] = allocation.model_dump(mode="json")
                elif action.op == ActionOp.ATTACH_PORT:
                    allocation = port_allocations.get(action.target.id)
                    if allocation is None:
                        raise ValueError(
                            f"missing network allocation for port {action.target.id}"
                        )
                    params["network"] = allocation.model_dump(mode="json")
                    server_id = placement.server_by_node.get(action.target.node_id)
                    if server_id is None:
                        raise ValueError(
                            f"missing Placement result for node {action.target.node_id}"
                        )
                elif action.op == ActionOp.CREATE_VM:
                    server_id = placement.server_by_node.get(action.target.id)
                    if server_id is None:
                        raise ValueError(
                            f"missing Placement result for node {action.target.id}"
                        )
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
                    )
                )
        return actions

    @activity.defn(name=ACTIVITY_BUILD_ADAPTER_ACTIONS)
    async def build_adapter_actions(
        self,
        deployment_id: int,
        plan: DeploymentPlan,
        placement: PlacementResult,
        network: NetworkAllocation,
    ) -> list[AdapterActionInput]:
        return self._build_adapter_actions(deployment_id, plan, placement, network)

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

    @activity.defn(name=ACTIVITY_RESERVE_PLACEMENT)
    async def reserve(self, deployment_id: int) -> PlacementReserveResponse:
        activity.logger.info(
            "Requesting local Placement reservation",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": activity.info().workflow_id,
                "idempotency_key": deployment_id,
            },
        )
        return await self._slice_manager.reserve_placement(
            PlacementReserveRequest(deployment_id=deployment_id)
        )

    @activity.defn(name=ACTIVITY_ROLLBACK_PLACEMENT)
    async def rollback_reservation(
        self, deployment_id: int
    ) -> PlacementRollbackResponse:
        activity.logger.info(
            "Requesting local Placement rollback",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": activity.info().workflow_id,
                "idempotency_key": deployment_id,
            },
        )
        return await self._slice_manager.rollback_placement(
            PlacementRollbackRequest(deployment_id=deployment_id)
        )
