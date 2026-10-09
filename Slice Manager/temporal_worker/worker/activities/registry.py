from contracts.placement import (
    PlacementReserveRequest,
    PlacementRollbackRequest,
    PlacementReserveResponse,
    PlacementRollbackResponse,
)
from contracts.registry import (
    ApprovalState,
    DeploymentContext,
    FinishDeployment,
    RoundFacts,
    RoundFactsResult,
    StartDeployment,
)
from typing import Any
import httpx
from temporalio import activity
from contracts.queues import (
    ACTIVITY_FINISH_DEPLOYMENT,
    ACTIVITY_GET_APPROVAL_STATE,
    ACTIVITY_GET_PLAN,
    ACTIVITY_RECORD_ROUND_FACTS,
    ACTIVITY_RESERVE_PLACEMENT,
    ACTIVITY_ROLLBACK_PLACEMENT,
    ACTIVITY_START_DEPLOYMENT,
    ACTIVITY_ALLOCATE_NETWORKING,
)
from temporal_worker.worker.activities.slice_manager_client import SliceManagerClient


class DeploymentActivities:
    def __init__(
        self,
        slice_manager: SliceManagerClient,
        network_manager_url: str = "http://networkmanager:8000",
    ) -> None:
        self._slice_manager = slice_manager
        self._network_manager_url = network_manager_url.rstrip("/")

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

    @activity.defn(name=ACTIVITY_ALLOCATE_NETWORKING)
    async def allocate_networking(
        self,
        deployment_id: int,
        allocation_by_node: dict[str, int],
    ) -> dict[str, Any]:
        activity.logger.info(
            "Executing Network Manager allocation for deployment",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": activity.info().workflow_id,
            },
        )
        # 1. Obtener el plan congelado para extraer slice_id y la topología
        context = await self._slice_manager.get_plan(deployment_id)

        placement_map: dict[str, Any] = {}
        for action_round in context.plan.rounds:
            for act in action_round:
                if act.op.value == "create_vm":
                    node_id_str = str(act.target.id)
                    vm_name = act.params.get("name", node_id_str)
                    worker_id = allocation_by_node.get(node_id_str, 1)
                    placement_map[vm_name] = f"Worker{worker_id}"
                    placement_map[node_id_str] = f"Worker{worker_id}"

        links_payload: list[dict[str, Any]] = []
        for intent in context.plan.intents:
            if intent.target.table == "links":
                link_name = next((c["after"] for c in intent.changes if c["field"] == "name"), "link")
                endpoints = next((c["after"] for c in intent.changes if c["field"] == "endpoints"), [])
                if len(endpoints) >= 2:
                    links_payload.append({
                        "link_name": str(link_name),
                        "vm_a_id": str(endpoints[0]),
                        "iface_a": "eth1",
                        "vm_b_id": str(endpoints[1]),
                        "iface_b": "eth1",
                    })

        allocate_payload = {
            "slice_id": context.slice_id,
            "placement_map": placement_map,
            "links": links_payload,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                f"{self._network_manager_url}/networking/allocate",
                json=allocate_payload,
            )
            res.raise_for_status()
            allocated = res.json()

            ovs_res = await client.get(
                f"{self._network_manager_url}/networking/ovs/commands/{context.slice_id}"
            )
            ovs_data = ovs_res.json() if ovs_res.status_code == 200 else {}

        activity.logger.info(
            f"Network allocation completed successfully. S-TAG={allocated.get('vlan_slice')}"
        )
        return {
            "vlan_slice": allocated.get("vlan_slice"),
            "bridge_name": allocated.get("bridge_name"),
            "networks": allocated.get("networks", []),
            "ovs_commands": ovs_data,
        }
