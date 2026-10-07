import json
import logging
from collections.abc import Mapping
from typing import Protocol

import asyncpg

from contracts.registry import (
    ApprovalDecision,
    ApprovalState,
    ApprovalStatus,
    DeploymentContext,
    DeploymentCreated,
    FinishDeployment,
    RoundFacts,
    RoundFactsResult,
    StartDeployment,
)
from contracts.placement import (
    PlacementReserveRequest,
    PlacementReserveResponse,
    PlacementRollbackRequest,
    PlacementRollbackResponse,
)
from slice_manager.app.config import Settings
from slice_manager.app.domain.capabilities import load_capabilities
from slice_manager.app.domain.errors import (
    AccessDeniedError,
    ConflictError,
    InsufficientCapacityError,
    ResourceNotFoundError,
    WorkflowUnavailableError,
    WorkflowStartError,
)
from contracts.plan import DeploymentPlan
from slice_manager.app.planning import InvalidSpecError, build_initial_deploy_plan
from slice_manager.app.planning.definition import parse_definition
from slice_manager.app.domain.placement_simulator import (
    InsufficientPlacementCapacity,
)
from slice_manager.app.repositories.deployments import DeploymentRepository

logger = logging.getLogger(__name__)


class WorkflowClient(Protocol):
    async def start_deploy(self, deployment_id: int, workflow_id: str) -> None: ...

    async def signal_approval(
        self,
        workflow_id: str,
        approved: bool,
        operator_id: int,
        reason: str | None,
    ) -> None: ...


class SliceService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        repository: DeploymentRepository,
        workflow_client: WorkflowClient,
        settings: Settings,
    ) -> None:
        self._pool = pool
        self._repository = repository
        self._workflow_client = workflow_client
        self._settings = settings

    async def request_initial_deploy(
        self,
        *,
        slice_id: int,
        version_number: int,
        requester_id: int,
    ) -> DeploymentCreated:
        if not self._settings.internal_service_token:
            raise WorkflowUnavailableError(
                "ORCHESTRATOR_INTERNAL_SERVICE_TOKEN must be configured"
            )
        workflow_id: str | None = None

        async with self._pool.acquire() as connection:
            async with connection.transaction():
                context = await self._repository.lock_initial_deploy_context(
                    connection,
                    slice_id=slice_id,
                    version_number=version_number,
                )
                if context is None:
                    raise ResourceNotFoundError("slice or draft version was not found")
                if context["usuario_id"] != requester_id:
                    raise AccessDeniedError("only the slice owner can deploy this slice")

                active = await self._repository.get_active_deployment(connection, slice_id)
                version_id = context["version_id"]
                if active is None:
                    if (
                        context["slice_state"] != "DRAFT"
                        or context["version_activa_id"] is not None
                    ):
                        raise ConflictError(
                            "only a slice without an active version can be deployed"
                        )
                    if context["estado_version"] != "DRAFT":
                        raise ConflictError("the target slice version is not editable")
                    if await self._repository.has_live_version_inventory(
                        connection, version_id
                    ):
                        raise ConflictError(
                            "initial Deploy requires a draft version without existing inventory"
                        )
                elif active["target_version_id"] != version_id:
                    raise ConflictError(
                        "another deployment is already active for this slice"
                    )

                if context["zone_state"] != "ACTIVE":
                    raise InvalidSpecError("availability zone is not active")
                if context["nivel_id"] is None or context["service_level"] is None:
                    raise AccessDeniedError("the slice owner has no service level")
                if context["service_level"] not in {"basico", "avanzado"}:
                    raise ConflictError(
                        f"unsupported service level {context['service_level']!r}"
                    )
                if not await self._repository.is_zone_allowed(
                    connection,
                    zone_id=context["zona_id"],
                    service_level_id=context["nivel_id"],
                ):
                    raise InvalidSpecError(
                        "the user's service level is not allowed in this availability zone"
                    )

                definition = parse_definition(self._decode_spec(context["spec"]))
                flavors, images = await self._repository.load_catalog(
                    connection, context["cluster_id"]
                )
                capabilities = load_capabilities(
                    self._settings.capabilities_dir,
                    context["cluster_name"],
                )
                if active is None:
                    deployment_id = await self._repository.reserve_deployment_id(
                        connection
                    )
                    identity_scope = f"deployment:{deployment_id}"
                else:
                    deployment_id = active["id"]
                    identity_scope = f"deployment:{deployment_id}"
                plan = build_initial_deploy_plan(
                    definition,
                    slice_id=slice_id,
                    identity_scope=identity_scope,
                    flavors=flavors,
                    images=images,
                    cluster_name=context["cluster_name"],
                    supports_public_access=capabilities.get(
                        "public_access", False
                    ),
                )

                if active is not None:
                    if not self._same_json(active["plan"], plan.model_dump(mode="json")):
                        raise ConflictError(
                            "active deployment does not match the current stored spec and plan"
                        )
                    approval = await self._repository.get_approval_state(
                        connection, deployment_id
                    )
                    if approval is None:
                        raise ConflictError("active deployment has no approval record")
                    workflow_id = active["workflow_id"]
                    if workflow_id is None and active["estado"] == "PENDING":
                        workflow_id = await self._repository.ensure_workflow_id(
                            connection, deployment_id
                        )
                    created = DeploymentCreated(
                        deployment_id=deployment_id,
                        workflow_id=workflow_id,
                        state=active["estado"],
                        approval_status=ApprovalStatus(approval["estado"]),
                    )
                else:
                    rejection_reason = await self._repository.admission_failure(
                        connection,
                        user_id=requester_id,
                        service_level_id=context["nivel_id"],
                        zone_id=context["zona_id"],
                        resource_delta=plan.summary.resource_delta.model_dump(),
                    )
                    if rejection_reason is not None:
                        await self._repository.create_rejected_deployment(
                            connection,
                            deployment_id=deployment_id,
                            slice_id=slice_id,
                            version_id=version_id,
                            requester_id=requester_id,
                            plan=plan,
                            reason=rejection_reason,
                        )
                        created = DeploymentCreated(
                            deployment_id=deployment_id,
                            state="REJECTED",
                            approval_status=ApprovalStatus.REJECTED_BY_SYSTEM,
                            rejection_reason=rejection_reason,
                        )
                    else:
                        requires_approval = context["service_level"] == "basico"
                        workflow_id = await self._repository.create_pending_deployment(
                            connection,
                            deployment_id=deployment_id,
                            slice_id=slice_id,
                            version_id=version_id,
                            requester_id=requester_id,
                            plan=plan,
                            requires_approval=requires_approval,
                            flavors=flavors,
                            images=images,
                        )
                        created = DeploymentCreated(
                            deployment_id=deployment_id,
                            workflow_id=workflow_id,
                            state="PENDING",
                            approval_status=(
                                ApprovalStatus.PENDING
                                if requires_approval
                                else ApprovalStatus.APPROVED
                            ),
                        )

        if workflow_id is not None:
            logger.info(
                "Starting deployment workflow",
                extra={
                    "deployment_id": created.deployment_id,
                    "workflow_id": workflow_id,
                    "idempotency_key": created.deployment_id,
                },
            )
            try:
                await self._workflow_client.start_deploy(
                    created.deployment_id,
                    workflow_id,
                )
            except WorkflowStartError as exc:
                raise WorkflowUnavailableError(
                    f"could not start Temporal workflow {workflow_id}"
                ) from exc
        return created

    async def approve_deployment(
        self,
        *,
        deployment_id: int,
        decision: ApprovalDecision,
    ) -> ApprovalStatus:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                if not await self._repository.verify_operator(
                    connection, decision.operator_id
                ):
                    raise AccessDeniedError("an active operator or admin is required")
                try:
                    status, workflow_id = await self._repository.decide_approval(
                        connection,
                        deployment_id=deployment_id,
                        operator_id=decision.operator_id,
                        approved=decision.approved,
                        reason=decision.reason,
                    )
                except LookupError as exc:
                    raise ResourceNotFoundError(str(exc)) from exc
                except ValueError as exc:
                    raise ConflictError(str(exc)) from exc
        if workflow_id is None:
            raise ConflictError("deployment has no Temporal workflow")
        try:
            logger.info(
                "Delivering deployment approval",
                extra={
                    "deployment_id": deployment_id,
                    "workflow_id": workflow_id,
                    "idempotency_key": deployment_id,
                },
            )
            await self._workflow_client.signal_approval(
                workflow_id,
                decision.approved,
                decision.operator_id,
                decision.reason,
            )
        except WorkflowStartError as exc:
            raise WorkflowUnavailableError(
                f"could not deliver approval to workflow {workflow_id}"
            ) from exc
        return ApprovalStatus(status)

    async def recover_pending_workflows(self) -> int:
        if not self._settings.internal_service_token:
            logger.error(
                "Cannot recover pending workflows without the internal service token"
            )
            return 0
        pending: list[tuple[int, str]] = []
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                rows = await self._repository.list_pending_workflows(connection)
                for row in rows:
                    workflow_id = row["workflow_id"]
                    if workflow_id is None:
                        workflow_id = await self._repository.ensure_workflow_id(
                            connection, row["id"]
                        )
                    pending.append((row["id"], workflow_id))
        started = 0
        for deployment_id, workflow_id in pending:
            try:
                await self._workflow_client.start_deploy(deployment_id, workflow_id)
                started += 1
            except WorkflowStartError as exc:
                logger.error(
                    "Could not recover pending deployment workflow",
                    extra={
                        "deployment_id": deployment_id,
                        "workflow_id": workflow_id,
                        "idempotency_key": deployment_id,
                        "error": str(exc),
                    },
                )
        return started

    async def get_plan(self, deployment_id: int) -> DeploymentContext:
        async with self._pool.acquire() as connection:
            row = await self._repository.get_deployment_context(
                connection, deployment_id
            )
        if row is None:
            raise ResourceNotFoundError(f"deployment {deployment_id} was not found")
        if row["target_version_id"] is None:
            raise ConflictError("deployment has no target version")
        plan_value = row["plan"]
        plan_json = (
            json.dumps(plan_value)
            if isinstance(plan_value, Mapping)
            else str(plan_value)
        )
        return DeploymentContext(
            deployment_id=row["deployment_id"],
            slice_id=row["slice_id"],
            target_version_id=row["target_version_id"],
            cluster_name=row["cluster_name"],
            plan=DeploymentPlan.model_validate_json(plan_json),
        )

    async def reserve_placement(
        self, request: PlacementReserveRequest
    ) -> PlacementReserveResponse:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                try:
                    return await self._repository.reserve_deployment_placement(
                        connection, request
                    )
                except LookupError as exc:
                    raise ResourceNotFoundError(str(exc)) from exc
                except InsufficientPlacementCapacity as exc:
                    raise InsufficientCapacityError(str(exc)) from exc
                except ValueError as exc:
                    raise ConflictError(str(exc)) from exc

    async def rollback_placement(
        self, request: PlacementRollbackRequest
    ) -> PlacementRollbackResponse:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                try:
                    return await self._repository.rollback_deployment_placement(
                        connection, request
                    )
                except LookupError as exc:
                    raise ResourceNotFoundError(str(exc)) from exc
                except ValueError as exc:
                    raise ConflictError(str(exc)) from exc

    async def get_approval_state(self, deployment_id: int) -> ApprovalState:
        async with self._pool.acquire() as connection:
            row = await self._repository.get_approval_state(connection, deployment_id)
        if row is None:
            raise ResourceNotFoundError(f"approval for deployment {deployment_id} was not found")
        return ApprovalState(
            deployment_id=deployment_id,
            status=ApprovalStatus(row["estado"]),
            timeout_seconds=self._settings.approval_timeout_seconds,
            reason=row["motivo"],
        )

    async def start_deployment(
        self, deployment_id: int, request: StartDeployment
    ) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                try:
                    await self._repository.start_deployment(
                        connection, deployment_id, request.event_id
                    )
                except LookupError as exc:
                    raise ResourceNotFoundError(str(exc)) from exc
                except ValueError as exc:
                    raise ConflictError(str(exc)) from exc

    async def record_round_facts(
        self, deployment_id: int, facts: RoundFacts
    ) -> RoundFactsResult:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                try:
                    return await self._repository.record_round_facts(
                        connection, deployment_id, facts
                    )
                except LookupError as exc:
                    raise ResourceNotFoundError(str(exc)) from exc

    async def finish_deployment(
        self, deployment_id: int, finish: FinishDeployment
    ) -> None:
        logger.info(
            "Finishing deployment",
            extra={
                "deployment_id": deployment_id,
                "workflow_id": f"deployment-{deployment_id}",
                "idempotency_key": finish.event_id,
                "outcome": finish.outcome.value,
            },
        )
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                try:
                    await self._repository.finish_deployment(
                        connection, deployment_id, finish
                    )
                except LookupError as exc:
                    raise ResourceNotFoundError(str(exc)) from exc
                except ValueError as exc:
                    raise ConflictError(str(exc)) from exc

    @staticmethod
    def _same_json(stored: object, requested: object) -> bool:
        if isinstance(stored, str):
            stored = json.loads(stored)
        return json.dumps(stored, sort_keys=True) == json.dumps(
            requested, sort_keys=True
        )

    @staticmethod
    def _decode_spec(value: object) -> object:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise InvalidSpecError("stored spec is not valid JSON") from exc
        return value
