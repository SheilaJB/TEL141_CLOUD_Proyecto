import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import asyncpg

from contracts.slices import (
    CreateSliceRequest,
    CreateVersionRequest,
    PlanPreview,
    PlanPreviewSummary,
    SliceCreated,
    ValidationPreview,
    VersionCreated,
)
from contracts.plan import DeploymentPlan
from slice_manager.app.domain.errors import (
    AccessDeniedError,
    ConflictError,
    ResourceNotFoundError,
)
from slice_manager.app.domain.capabilities import load_capabilities
from slice_manager.app.planning import InvalidSpecError, build_initial_deploy_plan
from slice_manager.app.planning.definition import (
    parse_definition,
    parse_definition_shape,
)
from slice_manager.app.repositories.deployments import DeploymentRepository
from slice_manager.app.repositories.slices import EMPTY_SLICE_SPEC, SliceRepository


class SliceLifecycleService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        repository: SliceRepository,
        deployment_repository: DeploymentRepository,
        capabilities_dir: Path | None,
    ) -> None:
        self._pool = pool
        self._repository = repository
        self._deployment_repository = deployment_repository
        self._capabilities_dir = capabilities_dir

    async def create_slice(
        self, *, user_id: int, request: CreateSliceRequest
    ) -> SliceCreated:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                actor = await self._repository.get_active_actor(connection, user_id)
                if actor is None or actor["role"] != "consumidor":
                    raise AccessDeniedError("only an active consumer can create a slice")
                if actor["nivel_id"] is None:
                    raise AccessDeniedError("the consumer has no service level")

                zone = await self._repository.get_zone(
                    connection,
                    zone_id=request.zona_id,
                    service_level_id=actor["nivel_id"],
                )
                if zone is None:
                    raise ResourceNotFoundError("availability zone was not found")
                if zone["estado"] != "ACTIVE" or zone["cluster_id"] is None:
                    raise ConflictError("availability zone is not active or has no cluster")
                if not zone["level_allowed"]:
                    raise AccessDeniedError(
                        "the consumer's service level is not allowed in this zone"
                    )

                template_id = request.plantilla_id
                if template_id is not None:
                    template = await self._repository.get_visible_template(
                        connection,
                        template_id=template_id,
                        user_id=user_id,
                    )
                    if template is None:
                        raise ResourceNotFoundError("template was not found")
                    raw_spec = template["spec"]
                    parsed_spec = parse_definition_shape(
                        self._decode_spec(raw_spec)
                    ).model_dump(
                        mode="json"
                    )
                    custom = False
                else:
                    raw_spec = request.spec
                    custom = True
                    if raw_spec is None:
                        raw_spec = EMPTY_SLICE_SPEC
                    parsed_spec = parse_definition_shape(
                        self._decode_spec(raw_spec)
                    ).model_dump(
                        mode="json"
                    )

                slice_id = await self._repository.insert_slice(
                    connection,
                    user_id=user_id,
                    name=request.name,
                    zone_id=request.zona_id,
                )
                version_id = await self._repository.insert_version(
                    connection,
                    slice_id=slice_id,
                    version_number=1,
                    spec=parsed_spec,
                    custom=custom,
                    template_id=template_id,
                )
        return SliceCreated(
            slice_id=slice_id,
            version_id=version_id,
            numero_version=1,
            estado="DRAFT",
            cluster=zone["cluster_name"],
        )

    async def create_version(
        self,
        *,
        slice_id: int,
        user_id: int,
        request: CreateVersionRequest,
    ) -> VersionCreated:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                actor = await self._repository.get_active_actor(connection, user_id)
                if actor is None:
                    raise AccessDeniedError("an active user is required")
                slice_row = await self._repository.lock_slice(connection, slice_id)
                if slice_row is None:
                    raise ResourceNotFoundError("slice was not found")
                self._require_owner(slice_row, user_id)
                if slice_row["estado"] == "ELIMINATED":
                    raise ConflictError("an eliminated slice cannot receive new versions")

                source: Mapping[str, Any] | None = None
                if request.from_version is not None:
                    source = await self._repository.get_version(
                        connection,
                        slice_id=slice_id,
                        version_number=request.from_version,
                    )
                    if source is None:
                        raise ResourceNotFoundError("source version was not found")
                elif not request.empty and slice_row["version_activa_id"] is not None:
                    source = await self._repository.get_version_by_id(
                        connection, slice_row["version_activa_id"]
                    )
                    if source is None or source["slice_id"] != slice_id:
                        raise ConflictError("active version reference is invalid")

                if source is None:
                    spec = EMPTY_SLICE_SPEC
                    custom = True
                    template_id = None
                else:
                    spec = source["spec"]
                    custom = source["custom"]
                    template_id = source["plantilla_origen_id"]
                copied_spec = self._decode_spec(spec)
                if not isinstance(copied_spec, Mapping):
                    raise InvalidSpecError("stored spec must be a JSON object")
                normalized_spec = dict(copied_spec)
                version_number = await self._repository.next_version_number(
                    connection, slice_id
                )
                version_id = await self._repository.insert_version(
                    connection,
                    slice_id=slice_id,
                    version_number=version_number,
                    spec=normalized_spec,
                    custom=custom,
                    template_id=template_id,
                )
                await self._repository.touch_slice(connection, slice_id)
        return VersionCreated(
            slice_id=slice_id,
            version_id=version_id,
            numero_version=version_number,
            estado="DRAFT",
        )

    async def replace_version_spec(
        self,
        *,
        slice_id: int,
        version_number: int,
        user_id: int,
        spec: object,
    ) -> VersionCreated:
        normalized_spec = parse_definition_shape(spec).model_dump(mode="json")
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                actor = await self._repository.get_active_actor(connection, user_id)
                if actor is None:
                    raise AccessDeniedError("an active user is required")
                slice_row = await self._repository.lock_slice(connection, slice_id)
                if slice_row is None:
                    raise ResourceNotFoundError("slice was not found")
                if (
                    slice_row["usuario_id"] != user_id
                    and actor["role"] != "operador"
                ):
                    raise AccessDeniedError(
                        "only the slice owner or an operator can replace this spec"
                    )
                version = await self._repository.get_version(
                    connection,
                    slice_id=slice_id,
                    version_number=version_number,
                    for_update=True,
                )
                if version is None:
                    raise ResourceNotFoundError("slice version was not found")
                if version["estado_version"] != "DRAFT":
                    raise ConflictError("only a DRAFT version can be replaced")
                if not await self._repository.update_version_spec(
                    connection,
                    version_id=version["id"],
                    spec=normalized_spec,
                ):
                    raise ConflictError("version is no longer editable")
                await self._repository.touch_slice(connection, slice_id)
        return VersionCreated(
            slice_id=slice_id,
            version_id=version["id"],
            numero_version=version_number,
            estado="DRAFT",
        )

    async def delete_version(
        self,
        *,
        slice_id: int,
        version_number: int,
        user_id: int,
    ) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                actor = await self._repository.get_active_actor(connection, user_id)
                if actor is None:
                    raise AccessDeniedError("an active user is required")
                slice_row = await self._repository.lock_slice(connection, slice_id)
                if slice_row is None:
                    raise ResourceNotFoundError("slice was not found")
                self._require_owner(slice_row, user_id)
                version = await self._repository.get_version(
                    connection,
                    slice_id=slice_id,
                    version_number=version_number,
                    for_update=True,
                )
                if version is None:
                    raise ResourceNotFoundError("slice version was not found")
                if version["estado_version"] != "DRAFT":
                    raise ConflictError("only a DRAFT version can be deleted")
                if await self._repository.version_is_referenced(
                    connection, version["id"]
                ):
                    raise ConflictError(
                        "a version referenced by deployments or inventory cannot be deleted"
                    )
                if not await self._repository.delete_version(
                    connection, version["id"]
                ):
                    raise ConflictError("version is no longer deletable")
                await self._repository.touch_slice(connection, slice_id)

    async def validate_version(
        self, *, slice_id: int, version_number: int, user_id: int
    ) -> ValidationPreview:
        context, plan, requires_approval = await self._build_preview(
            slice_id=slice_id,
            version_number=version_number,
            user_id=user_id,
        )
        return ValidationPreview(
            slice_id=slice_id,
            version_id=context["version_id"],
            numero_version=version_number,
            valid=True,
            layers=[
                "structural",
                "local_semantics",
                "topology",
                "admission",
            ],
            resource_delta=plan.summary.resource_delta,
            requires_approval=requires_approval,
        )

    async def plan_version(
        self, *, slice_id: int, version_number: int, user_id: int
    ) -> PlanPreview:
        context, plan, requires_approval = await self._build_preview(
            slice_id=slice_id,
            version_number=version_number,
            user_id=user_id,
        )
        return PlanPreview(
            slice_id=slice_id,
            version_id=context["version_id"],
            numero_version=version_number,
            summary=PlanPreviewSummary(
                disruption_max=plan.summary.disruption_max,
                destructive=plan.summary.destructive,
                resource_delta=plan.summary.resource_delta,
            ),
            requires_approval=requires_approval,
            intents=plan.intents,
        )

    async def _build_preview(
        self, *, slice_id: int, version_number: int, user_id: int
    ) -> tuple[asyncpg.Record, DeploymentPlan, bool]:
        async with self._pool.acquire() as connection:
            async with connection.transaction(
                isolation="repeatable_read", readonly=True
            ):
                context = await self._repository.get_slice_plan_context(
                    connection,
                    slice_id=slice_id,
                    version_number=version_number,
                )
                if context is None:
                    raise ResourceNotFoundError("slice or version was not found")
                self._require_owner(context, user_id)
                if context["estado_version"] != "DRAFT":
                    raise ConflictError("only a DRAFT version can be validated or previewed")
                if (
                    context["slice_state"] != "DRAFT"
                    or context["version_activa_id"] is not None
                ):
                    raise ConflictError(
                        "validation and plan preview currently support only slices without an active version"
                    )
                definition = parse_definition(self._decode_spec(context["spec"]))
                flavors, images = await self._deployment_repository.load_catalog(
                    connection, context["cluster_id"]
                )
                capabilities = load_capabilities(
                    self._capabilities_dir, context["cluster_name"]
                )
                plan = build_initial_deploy_plan(
                    definition,
                    slice_id=slice_id,
                    identity_scope="preview",
                    flavors=flavors,
                    images=images,
                    cluster_name=context["cluster_name"],
                    supports_public_access=capabilities.get("public_access", False),
                )
                if context["zone_state"] != "ACTIVE":
                    raise InvalidSpecError("availability zone is not active")
                if context["nivel_id"] is None or context["service_level"] is None:
                    raise AccessDeniedError("the slice owner has no service level")
                if not await self._repository.is_zone_allowed(
                    connection,
                    zone_id=context["zona_id"],
                    service_level_id=context["nivel_id"],
                ):
                    raise InvalidSpecError(
                        "the user's service level is not allowed in this availability zone"
                    )
                admission_failure = await self._deployment_repository.admission_failure(
                    connection,
                    user_id=user_id,
                    service_level_id=context["nivel_id"],
                    zone_id=context["zona_id"],
                    resource_delta=plan.summary.resource_delta.model_dump(),
                )
                if admission_failure is not None:
                    raise InvalidSpecError(admission_failure)
                requires_approval = context["service_level"] == "basico"
                if context["service_level"] not in {"basico", "avanzado"}:
                    raise ConflictError(
                        f"unsupported service level {context['service_level']!r}"
                    )
        return context, plan, requires_approval

    @staticmethod
    def _require_owner(row: Mapping[str, Any], user_id: int) -> None:
        if row["usuario_id"] != user_id:
            raise AccessDeniedError("only the slice owner can manage this slice")

    @staticmethod
    def _decode_spec(value: object) -> object:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise InvalidSpecError("stored spec is not valid JSON") from exc
        return value
