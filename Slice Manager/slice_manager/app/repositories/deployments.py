import json
from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

import asyncpg

from contracts.plan import ActionOp, DeploymentPlan
from contracts.registry import (
    ActionFact,
    DeploymentOutcome,
    FinishDeployment,
    RoundFacts,
    RoundFactsResult,
)
from slice_manager.app.planning.planner import CatalogFlavor, CatalogImage

# Columnas de facts que el DDL actual puede persistir. El resto se reporta como warning.
_SUPPORTED_ACTION_FACTS: dict[ActionOp, frozenset[str]] = {
    ActionOp.CREATE_VM: frozenset({"pid"}),
    ActionOp.ATTACH_PORT: frozenset({"mac", "ip"}),
    ActionOp.CREATE_LINK: frozenset(),
    ActionOp.DELETE_VM: frozenset(),
    ActionOp.DELETE_LINK: frozenset(),
    ActionOp.DETACH_PORT: frozenset(),
}


class DeploymentRepository:
    async def lock_initial_deploy_context(
        self,
        connection: asyncpg.Connection,
        *,
        slice_id: int,
        version_number: int,
    ) -> asyncpg.Record | None:
        return await connection.fetchrow(
            """
            SELECT s.id AS slice_id, s.usuario_id, s.zona_id, s.estado AS slice_state,
                   s.version_activa_id, sv.id AS version_id,
                   sv.numero_version, sv.spec, sv.estado_version,
                   za.estado AS zone_state, za.cluster_id, c.nombre AS cluster_name,
                   u.nivel_id, n.nombre AS service_level
            FROM slices.slice AS s
            JOIN slices.slice_version AS sv ON sv.slice_id = s.id
            JOIN slices.zona_disponibilidad AS za ON za.id = s.zona_id
            JOIN slices.cluster AS c ON c.id = za.cluster_id
            JOIN auth.usuario AS u ON u.id = s.usuario_id
            LEFT JOIN auth.nivel AS n ON n.id = u.nivel_id
            WHERE s.id = $1 AND sv.numero_version = $2
              AND u.estado = 'ACTIVE'
            FOR UPDATE OF s, sv
            """,
            slice_id,
            version_number,
        )

    async def get_active_deployment(
        self, connection: asyncpg.Connection, slice_id: int
    ) -> asyncpg.Record | None:
        return await connection.fetchrow(
            """
            SELECT id, target_version_id, solicitado_por, estado, workflow_id, plan
            FROM slices.deployment
            WHERE slice_id = $1 AND estado IN ('PENDING', 'IN_PROGRESS')
            FOR UPDATE
            """,
            slice_id,
        )

    async def reserve_deployment_id(
        self, connection: asyncpg.Connection
    ) -> int:
        return await connection.fetchval(
            """
            SELECT nextval(pg_get_serial_sequence('slices.deployment', 'id'))
            """
        )

    async def is_zone_allowed(
        self,
        connection: asyncpg.Connection,
        *,
        zone_id: int,
        service_level_id: int,
    ) -> bool:
        return await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM auth.nivel_accede_zona
                WHERE nivel_id = $1 AND zona_id = $2
            )
            """,
            service_level_id,
            zone_id,
        )

    async def list_pending_workflows(
        self, connection: asyncpg.Connection
    ) -> list[asyncpg.Record]:
        return await connection.fetch(
            """
            SELECT id, workflow_id
            FROM slices.deployment
            WHERE estado = 'PENDING' AND workflow_id IS NULL
            ORDER BY id
            """
        )

    async def ensure_workflow_id(
        self, connection: asyncpg.Connection, deployment_id: int
    ) -> str:
        workflow_id = f"deployment-{deployment_id}"
        await connection.execute(
            """
            UPDATE slices.deployment SET workflow_id = $2
            WHERE id = $1 AND workflow_id IS NULL AND estado = 'PENDING'
            """,
            deployment_id,
            workflow_id,
        )
        current = await connection.fetchval(
            "SELECT workflow_id FROM slices.deployment WHERE id = $1",
            deployment_id,
        )
        if current is None:
            raise LookupError(f"pending deployment {deployment_id} was not found")
        return current

    async def has_live_version_inventory(
        self, connection: asyncpg.Connection, version_id: int
    ) -> bool:
        return await connection.fetchval(
            """
            SELECT
                EXISTS (
                    SELECT 1 FROM slices.slice_nodo
                    WHERE defin_version_id = $1 AND estado_nodo <> 'DELETED'
                )
                OR EXISTS (
                    SELECT 1 FROM slices.slice_enlace
                    WHERE defin_version_id = $1
                )
            """,
            version_id,
        )

    async def load_catalog(
        self, connection: asyncpg.Connection, cluster_id: int
    ) -> tuple[dict[str, CatalogFlavor], dict[str, CatalogImage]]:
        flavor_rows = await connection.fetch(
            """
            SELECT id, name, vcpu, vram_mb, vdisk_mb
            FROM slices.flavor
            WHERE activo = TRUE
            ORDER BY name
            """
        )
        image_rows = await connection.fetch(
            """
            SELECT i.id, i.nombre, i.ruta_referencia, c.nombre AS cluster_name
            FROM slices.image AS i
            LEFT JOIN slices.cluster AS c ON c.id = i.cluster_compatible
            WHERE i.estado = 'ACTIVE'
              AND (i.cluster_compatible IS NULL OR i.cluster_compatible = $1)
            ORDER BY i.nombre
            """,
            cluster_id,
        )
        flavors = {
            row["name"]: CatalogFlavor(
                name=row["name"],
                vcpu=row["vcpu"],
                ram_mb=row["vram_mb"],
                disk_mb=row["vdisk_mb"],
                catalog_id=row["id"],
            )
            for row in flavor_rows
        }
        images = {
            row["nombre"]: CatalogImage(
                name=row["nombre"],
                reference=row["ruta_referencia"],
                catalog_id=row["id"],
                cluster_compatible=row["cluster_name"],
            )
            for row in image_rows
        }
        return flavors, images

    async def admission_failure(
        self,
        connection: asyncpg.Connection,
        *,
        user_id: int,
        service_level_id: int,
        zone_id: int,
        resource_delta: Mapping[str, int],
    ) -> str | None:
        zone_allowed = await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM auth.nivel_accede_zona
                WHERE nivel_id = $1 AND zona_id = $2
            )
            """,
            service_level_id,
            zone_id,
        )
        if not zone_allowed:
            return "The user's service level is not allowed in this availability zone"

        quotas = await connection.fetch(
            """
            SELECT q.recurso,
                   COALESCE(o.prometido, q.prometido) AS promised
            FROM auth.cuota AS q
            LEFT JOIN auth.cuota_override AS o
              ON o.usuario_id = $1 AND o.recurso = q.recurso
            WHERE q.nivel_id = $2
            """,
            user_id,
            service_level_id,
        )
        quota_by_resource = {
            row["recurso"]: Decimal(row["promised"]) for row in quotas
        }
        usage_rows = await connection.fetch(
            """
            SELECT r.recurso, COALESCE(SUM(r.cantidad_reservada), 0)::bigint AS amount
            FROM slices.reserva_nodo AS r
            JOIN slices.slice_nodo AS node ON node.id = r.nodo_id
            JOIN slices.slice AS s ON s.id = node.slice_id
            WHERE s.usuario_id = $1 AND r.estado = 'RESERVED'
            GROUP BY r.recurso
            """,
            user_id,
        )
        usage_keys = {"vcpu": "vcpu", "vram_mb": "ram_mb", "vdisk_mb": "disk_mb"}
        current_usage = {
            usage_keys[row["recurso"]]: Decimal(row["amount"])
            for row in usage_rows
            if row["recurso"] in usage_keys
        }
        quota_keys = {"vcpu": "vcpu", "ram_mb": "ram_mb", "disk_mb": "disk_mb"}
        for resource, increase in resource_delta.items():
            current = current_usage.get(resource, 0)
            quota = quota_by_resource.get(quota_keys[resource])
            if quota is None or current + increase > quota:
                return (
                    f"Quota exceeded for {resource}: "
                    f"reserved={current}, requested={increase}, quota={quota}"
                )
        return None

    async def create_rejected_deployment(
        self,
        connection: asyncpg.Connection,
        *,
        deployment_id: int,
        slice_id: int,
        version_id: int,
        requester_id: int,
        plan: DeploymentPlan,
        reason: str,
    ) -> int:
        await connection.execute(
            """
            INSERT INTO slices.deployment
                (id, slice_id, target_version_id, tipo, estado, plan, error, solicitado_por)
            VALUES ($1, $2, $3, 'DEPLOY', 'REJECTED', $4::jsonb, $5::jsonb, $6)
            """,
            deployment_id,
            slice_id,
            version_id,
            plan.model_dump_json(),
            json.dumps({"code": "AdmissionRejected", "message": reason}),
            requester_id,
        )
        await connection.execute(
            """
            INSERT INTO slices.solicitud_aprobacion
                (deployment_id, usuario_id, estado, motivo)
            VALUES ($1, $2, 'REJECTED_BY_SYSTEM', $3)
            """,
            deployment_id,
            requester_id,
            reason,
        )
        return deployment_id

    async def create_pending_deployment(
        self,
        connection: asyncpg.Connection,
        *,
        deployment_id: int,
        slice_id: int,
        version_id: int,
        requester_id: int,
        plan: DeploymentPlan,
        requires_approval: bool,
        flavors: Mapping[str, CatalogFlavor],
        images: Mapping[str, CatalogImage],
    ) -> str:
        await connection.execute(
            """
            INSERT INTO slices.deployment
                (id, slice_id, target_version_id, tipo, estado, plan, solicitado_por)
            VALUES ($1, $2, $3, 'DEPLOY', 'PENDING', $4::jsonb, $5)
            """,
            deployment_id,
            slice_id,
            version_id,
            plan.model_dump_json(),
            requester_id,
        )
        workflow_id = f"deployment-{deployment_id}"
        await connection.execute(
            "UPDATE slices.deployment SET workflow_id = $2 WHERE id = $1",
            deployment_id,
            workflow_id,
        )
        approval_state = "PENDING" if requires_approval else "APPROVED"
        await connection.execute(
            """
            INSERT INTO slices.solicitud_aprobacion
                (deployment_id, usuario_id, estado)
            VALUES ($1, $2, $3)
            """,
            deployment_id,
            requester_id,
            approval_state,
        )
        await connection.execute(
            """
            UPDATE slices.slice
            SET estado = 'UPDATING', fecha_modificacion = now()
            WHERE id = $1
            """,
            slice_id,
        )
        await connection.execute(
            """
            UPDATE slices.slice_version
            SET estado_version = $2
            WHERE id = $1
            """,
            version_id,
            "APPROVAL_PENDING" if requires_approval else "DEPLOYING",
        )
        return workflow_id

    async def _insert_planned_inventory(
        self,
        connection: asyncpg.Connection,
        *,
        slice_id: int,
        version_id: int,
        deployment_id: int,
        plan: DeploymentPlan,
        flavors: Mapping[str, CatalogFlavor],
        images: Mapping[str, CatalogImage],
    ) -> None:
        port_targets: list[tuple[object, object, object, bool]] = []
        public_link_id: UUID | None = None
        for action in (item for round_actions in plan.rounds for item in round_actions):
            if action.op == ActionOp.CREATE_VM:
                vm = action.after
                if not isinstance(vm, dict):
                    raise ValueError(f"CREATE_VM action {action.id} has no VM specification")
                flavor = flavors.get(vm["flavor_ref"])
                image = images.get(vm["image_ref"])
                if flavor is None or image is None:
                    raise ValueError("planned VM references a catalog item that is no longer available")
                await connection.execute(
                    """
                    INSERT INTO slices.slice_nodo
                        (id, slice_id, name, defin_version_id, flavor_id, imagen_id,
                         public_access, estado_nodo, deployment_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'PENDING', $8)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    action.target.id,
                    slice_id,
                    vm["name"],
                    version_id,
                    flavor.catalog_id,
                    image.catalog_id,
                    bool(vm.get("public", False)),
                    deployment_id,
                )
            elif action.op == ActionOp.CREATE_LINK:
                link = action.after
                if not isinstance(link, dict):
                    raise ValueError(f"CREATE_LINK action {action.id} has no link specification")
                is_public = bool(link.get("public", False))
                if is_public:
                    if public_link_id is not None:
                        raise ValueError("plan contains more than one derived public link")
                    public_link_id = action.target.id
                await connection.execute(
                    """
                    INSERT INTO slices.slice_enlace
                        (id, slice_id, name, public, defin_version_id, deployment_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    action.target.id,
                    slice_id,
                    link["name"],
                    is_public,
                    version_id,
                    deployment_id,
                )
            elif action.op == ActionOp.ATTACH_PORT:
                if action.target.link_id is None or action.target.node_id is None:
                    raise ValueError(f"ATTACH_PORT action {action.id} has an incomplete target")
                port_targets.append(
                    (
                        action.target.id,
                        action.target.link_id,
                        action.target.node_id,
                        bool(action.after.get("public", False))
                        if isinstance(action.after, dict)
                        else False,
                    )
                )
        for port_id, link_id, node_id, is_public in port_targets:
            if is_public and public_link_id != link_id:
                raise ValueError("public ATTACH_PORT does not target the derived public link")
            await connection.execute(
                """
                INSERT INTO slices.slice_enlace_puerto
                    (id, enlace_id, nodo_id, public, defin_version_id, deployment_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (id) DO NOTHING
                """,
                port_id,
                link_id,
                node_id,
                is_public,
                version_id,
                deployment_id,
            )

    async def get_deployment_context(
        self, connection: asyncpg.Connection, deployment_id: int
    ) -> asyncpg.Record | None:
        return await connection.fetchrow(
            """
            SELECT d.id AS deployment_id, d.slice_id, d.target_version_id,
                   d.workflow_id, d.estado AS deployment_state, d.plan,
                   c.nombre AS cluster_name
            FROM slices.deployment AS d
            JOIN slices.slice AS s ON s.id = d.slice_id
            JOIN slices.zona_disponibilidad AS z ON z.id = s.zona_id
            JOIN slices.cluster AS c ON c.id = z.cluster_id
            WHERE d.id = $1
            """,
            deployment_id,
        )

    async def get_server_directory(
        self, connection: asyncpg.Connection, slice_id: int
    ) -> list[asyncpg.Record]:
        return await connection.fetch(
            """
            SELECT srv.id AS server_id, host(srv.ip_serv) AS management_ip
                FROM slices.slice AS s
              JOIN slices.servidor AS srv
                ON srv.zona_id = s.zona_id
               AND srv.estado = 'ACTIVE'
             WHERE s.id = $1
             ORDER BY srv.id
            """,
            slice_id,
        )

    async def create_inventory(
        self, connection: asyncpg.Connection, deployment_id: int
    ) -> None:
        row = await connection.fetchrow(
            """
            SELECT d.plan, d.slice_id, d.target_version_id, s.cluster_id
              FROM slices.deployment AS d
              JOIN slices.slice AS s ON s.id = d.slice_id
             WHERE d.id = $1
             FOR UPDATE OF d
            """,
            deployment_id,
        )
        if row is None:
            raise LookupError(f"deployment {deployment_id} was not found")
        plan = DeploymentPlan.model_validate_json(row["plan"])
        flavors, images = await self.load_catalog(connection, row["cluster_id"])
        await self._insert_planned_inventory(
            connection,
            slice_id=row["slice_id"],
            version_id=row["target_version_id"],
            deployment_id=deployment_id,
            plan=plan,
            flavors=flavors,
            images=images,
        )

    async def commit_inventory(
        self, connection: asyncpg.Connection, deployment_id: int
    ) -> None:
        row = await connection.fetchrow(
            """
            SELECT d.estado, d.plan, d.slice_id
            FROM slices.deployment d
            JOIN slices.slice s ON s.id = d.slice_id
            WHERE d.id = $1
            FOR UPDATE OF d, s
            """,
            deployment_id,
        )
        if row is None:
            raise LookupError(f"deployment {deployment_id} was not found")
        if row["estado"] not in {"IN_PROGRESS", "COMPLETED"}:
            raise ValueError("inventory commit requires an in-progress deployment")
        plan = DeploymentPlan.model_validate_json(row["plan"])
        flavor_updates: dict[UUID, str] = {}
        for intent in plan.intents:
            if intent.target.table != "vms":
                continue
            for change in intent.changes:
                if change.field == "flavor_ref" and isinstance(change.after, str):
                    flavor_updates[intent.target.id] = change.after

        flavor_names = set(flavor_updates.values())
        flavor_rows = await connection.fetch(
            """
            SELECT id, name
            FROM slices.flavor
            WHERE name = ANY($1::text[]) AND activo
            """,
            list(flavor_names),
        )
        flavor_id_by_name = {item["name"]: item["id"] for item in flavor_rows}
        if set(flavor_id_by_name) != flavor_names:
            raise ValueError("inventory commit references a missing or inactive flavor")
        for node_id, flavor_name in flavor_updates.items():
            status = await connection.execute(
                """
                UPDATE slices.slice_nodo
                SET flavor_id = $3
                WHERE id = $1 AND slice_id = $2
                """,
                node_id,
                row["slice_id"],
                flavor_id_by_name[flavor_name],
            )
            if not status.endswith(" 1"):
                raise ValueError(f"inventory node {node_id} was not found in the deployment slice")

        released_node_ids = [item.node_id for item in plan.reservation.release]
        if released_node_ids:
            updated_nodes = await connection.fetch(
                """
                UPDATE slices.slice_nodo
                SET estado_nodo = 'DELETED'
                WHERE slice_id = $1 AND id = ANY($2::uuid[])
                  AND estado_nodo <> 'DELETED'
                RETURNING id
                """,
                row["slice_id"],
                released_node_ids,
            )
            updated_ids = {item["id"] for item in updated_nodes}
            already_deleted = await connection.fetch(
                """
                SELECT id
                FROM slices.slice_nodo
                WHERE slice_id = $1 AND id = ANY($2::uuid[])
                  AND estado_nodo = 'DELETED'
                """,
                row["slice_id"],
                released_node_ids,
            )
            if updated_ids | {item["id"] for item in already_deleted} != set(released_node_ids):
                raise ValueError("inventory release nodes do not match deployment slice")

    async def get_approval_state(
        self, connection: asyncpg.Connection, deployment_id: int
    ) -> asyncpg.Record | None:
        return await connection.fetchrow(
            """
            SELECT d.id AS deployment_id, d.workflow_id, a.estado, a.motivo
            FROM slices.deployment AS d
            JOIN slices.solicitud_aprobacion AS a ON a.deployment_id = d.id
            WHERE d.id = $1
            """,
            deployment_id,
        )

    async def start_deployment(
        self, connection: asyncpg.Connection, deployment_id: int, _event_id: str
    ) -> None:
        row = await connection.fetchrow(
            """
            SELECT d.estado, a.estado AS approval_state, d.target_version_id,
                   d.slice_id, s.zona_id, z.cluster_id, d.plan
            FROM slices.deployment AS d
            JOIN slices.solicitud_aprobacion AS a ON a.deployment_id = d.id
            JOIN slices.slice AS s ON s.id = d.slice_id
            JOIN slices.zona_disponibilidad AS z ON z.id = s.zona_id
            WHERE d.id = $1
            FOR UPDATE OF d
            """,
            deployment_id,
        )
        if row is None:
            raise LookupError(f"deployment {deployment_id} was not found")
        if row["estado"] == "IN_PROGRESS":
            return
        if row["estado"] != "PENDING" or row["approval_state"] != "APPROVED":
            raise ValueError("deployment is not approved and pending")
        await connection.execute(
            """
            UPDATE slices.deployment SET estado = 'IN_PROGRESS'
            WHERE id = $1 AND estado = 'PENDING'
            """,
            deployment_id,
        )
        await connection.execute(
            """
            UPDATE slices.slice_version SET estado_version = 'DEPLOYING'
            WHERE id = $1 AND estado_version = 'APPROVAL_PENDING'
            """,
            row["target_version_id"],
        )

    async def record_round_facts(
        self, connection: asyncpg.Connection, deployment_id: int, facts: RoundFacts
    ) -> RoundFactsResult:
        deployment = await connection.fetchrow(
            "SELECT slice_id FROM slices.deployment WHERE id = $1 FOR UPDATE",
            deployment_id,
        )
        if deployment is None:
            raise LookupError(f"deployment {deployment_id} was not found")
        warnings: set[str] = set()
        persisted_count = 0
        for result in facts.actions:
            persisted = await self._apply_action_fact(
                connection,
                slice_id=deployment["slice_id"],
                result=result,
            )
            persisted_count += int(persisted)
            unsupported = sorted(set(result.facts) - _SUPPORTED_ACTION_FACTS.get(result.op, frozenset()))
            if unsupported:
                warnings.add(
                    f"Unsupported facts for {result.op.value} action {result.action_id} were not persisted: "
                    f"{', '.join(unsupported)}."
                )
        return RoundFactsResult(
            event_id=facts.event_id,
            persisted_action_count=persisted_count,
            warnings=sorted(warnings),
        )

    async def _apply_action_fact(
        self,
        connection: asyncpg.Connection,
        *,
        slice_id: int,
        result: ActionFact,
    ) -> bool:
        if result.op == ActionOp.CREATE_VM:
            pid = result.facts.get("pid")
            status = await connection.execute(
                """
                UPDATE slices.slice_nodo
                SET estado_nodo = $3,
                    pid = COALESCE($4::int, pid)
                WHERE id = $1 AND slice_id = $2
                """,
                result.target_id,
                slice_id,
                "RUNNING" if result.succeeded else "FAILED",
                pid,
            )
            if not status.endswith(" 1"):
                raise LookupError(f"node {result.target_id} was not found for action facts")
            return True
        elif result.op == ActionOp.DELETE_VM:
            status = await connection.execute(
                """
                UPDATE slices.slice_nodo SET estado_nodo = 'DELETED'
                WHERE id = $1 AND slice_id = $2
                """,
                result.target_id,
                slice_id,
            )
            if not status.endswith(" 1"):
                raise LookupError(f"node {result.target_id} was not found for action facts")
            return True
        elif result.op == ActionOp.CREATE_LINK:
            status = await connection.execute(
                """
                UPDATE slices.slice_enlace SET estado = $3
                WHERE id = $1 AND slice_id = $2
                """,
                result.target_id,
                slice_id,
                "CREATED" if result.succeeded else "FAILED",
            )
            if not status.endswith(" 1"):
                raise LookupError(f"link {result.target_id} was not found for action facts")
            return True
        elif result.op == ActionOp.DELETE_LINK:
            status = await connection.execute(
                """
                UPDATE slices.slice_enlace SET estado = 'DELETED'
                WHERE id = $1 AND slice_id = $2
                """,
                result.target_id,
                slice_id,
            )
            if not status.endswith(" 1"):
                raise LookupError(f"link {result.target_id} was not found for action facts")
            return True
        elif result.op == ActionOp.ATTACH_PORT:
            status = await connection.execute(
                """
                UPDATE slices.slice_enlace_puerto
                SET estado = $2,
                    mac = COALESCE($3::text::macaddr, mac),
                    ip  = COALESCE($4::text::inet, ip)
                WHERE id = $1
                """,
                result.target_id,
                "ATTACHED" if result.succeeded else "FAILED",
                result.facts.get("mac"),
                result.facts.get("ip"),
            )
            if not status.endswith(" 1"):
                raise LookupError(f"port {result.target_id} was not found for action facts")
            return True
        elif result.op == ActionOp.DETACH_PORT:
            status = await connection.execute(
                """
                UPDATE slices.slice_enlace_puerto SET estado = 'DELETED'
                WHERE id = $1
                """,
                result.target_id,
            )
            if not status.endswith(" 1"):
                raise LookupError(f"port {result.target_id} was not found for action facts")
            return True
        return False

    async def finish_deployment(
        self,
        connection: asyncpg.Connection,
        deployment_id: int,
        finish: FinishDeployment,
    ) -> None:
        row = await connection.fetchrow(
            """
            SELECT slice_id, target_version_id, tipo, estado, plan
            FROM slices.deployment
            WHERE id = $1
            FOR UPDATE
            """,
            deployment_id,
        )
        if row is None:
            raise LookupError(f"deployment {deployment_id} was not found")
        expected_state = finish.outcome.value
        if row["estado"] in {"COMPLETED", "COMPENSATED", "FAILED", "REJECTED"}:
            if row["estado"] == expected_state:
                return
            raise ValueError("deployment already finished with a different outcome")
        if row["estado"] not in {"PENDING", "IN_PROGRESS"}:
            raise ValueError("deployment cannot be finished from its current state")
        if finish.outcome == DeploymentOutcome.REJECTED:
            rejection_reason = finish.error.message if finish.error else "Approval expired or rejected"
            await connection.execute(
                """
                UPDATE slices.solicitud_aprobacion
                SET estado = 'REJECTED_BY_OPERATOR',
                    operador_id = $2,
                    motivo = $3
                WHERE deployment_id = $1 AND estado = 'PENDING'
                """,
                deployment_id,
                finish.operator_id,
                rejection_reason,
            )
        await connection.execute(
            """
            UPDATE slices.deployment
            SET estado = $2, error = $3::jsonb, finished_at = now()
            WHERE id = $1
            """,
            deployment_id,
            expected_state,
            json.dumps(finish.error.model_dump(mode="json")) if finish.error else None,
        )
        target_version_id = row["target_version_id"]
        slice_id = row["slice_id"]
        plan = DeploymentPlan.model_validate_json(row["plan"])
        node_ids = [allocation.node_id for allocation in plan.reservation.allocate]

        if finish.outcome == DeploymentOutcome.COMPLETED:
            if target_version_id is None:
                raise ValueError("DEPLOY deployment has no target version")
            await connection.execute(
                """
                UPDATE slices.slice_version
                SET estado_version = 'ARCHIVED'
                WHERE slice_id = $1 AND estado_version = 'ACTIVE' AND id <> $2
                """,
                slice_id,
                target_version_id,
            )
            await connection.execute(
                "UPDATE slices.slice_version SET estado_version = 'ACTIVE' WHERE id = $1",
                target_version_id,
            )
            await connection.execute(
                """
                UPDATE slices.slice
                SET version_activa_id = $2, fecha_modificacion = now()
                WHERE id = $1
                """,
                slice_id,
                target_version_id,
            )
            await self._recalculate_slice_state(connection, slice_id)
        elif finish.outcome == DeploymentOutcome.COMPENSATED:
            if target_version_id is not None:
                await connection.execute(
                    "UPDATE slices.slice_version SET estado_version = 'DRAFT' WHERE id = $1",
                    target_version_id,
                )
            await connection.execute(
                """
                UPDATE slices.slice_nodo SET estado_nodo = 'DELETED'
                WHERE slice_id = $1 AND id = ANY($2::uuid[])
                """,
                slice_id,
                node_ids,
            )
            await self._delete_plan_links(connection, slice_id, plan)
            await self._recalculate_slice_state(connection, slice_id)
        elif finish.outcome == DeploymentOutcome.FAILED:
            if target_version_id is not None:
                await connection.execute(
                    """
                    UPDATE slices.slice_version SET estado_version = 'INCONSISTENT'
                    WHERE id = $1
                    """,
                    target_version_id,
                )
            await connection.execute(
                """
                UPDATE slices.slice_nodo SET estado_nodo = 'FAILED'
                WHERE slice_id = $1 AND id = ANY($2::uuid[])
                  AND estado_nodo <> 'DELETED'
                """,
                slice_id,
                node_ids,
            )
            await connection.execute(
                """
                UPDATE slices.slice
                SET estado = 'FAILED', version_activa_id = NULL, fecha_modificacion = now()
                WHERE id = $1
                """,
                slice_id,
            )
        else:
            if target_version_id is not None:
                await connection.execute(
                    "UPDATE slices.slice_version SET estado_version = 'DRAFT' WHERE id = $1",
                    target_version_id,
                )
            await connection.execute(
                """
                UPDATE slices.slice_nodo SET estado_nodo = 'DELETED'
                WHERE slice_id = $1 AND id = ANY($2::uuid[])
                """,
                slice_id,
                node_ids,
            )
            await self._delete_plan_links(connection, slice_id, plan)
            await self._recalculate_slice_state(connection, slice_id)

    async def _delete_plan_links(
        self,
        connection: asyncpg.Connection,
        slice_id: int,
        plan: DeploymentPlan,
    ) -> None:
        link_ids = [
            action.target.id
            for round_actions in plan.rounds
            for action in round_actions
            if action.op == ActionOp.CREATE_LINK
        ]
        if link_ids:
            await connection.execute(
                "DELETE FROM slices.slice_enlace WHERE slice_id = $1 AND id = ANY($2::uuid[])",
                slice_id,
                link_ids,
            )

    async def _recalculate_slice_state(
        self, connection: asyncpg.Connection, slice_id: int
    ) -> None:
        await connection.execute(
            """
            UPDATE slices.slice AS s
            SET estado = CASE
                WHEN s.version_activa_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM slices.slice_nodo n
                    WHERE n.slice_id = s.id AND n.estado_nodo = 'RUNNING'
                ) THEN 'RUNNING'
                WHEN s.version_activa_id IS NOT NULL THEN 'STOPPED'
                WHEN EXISTS (
                    SELECT 1 FROM slices.slice_nodo n
                    WHERE n.slice_id = s.id AND n.estado_nodo IN ('RUNNING', 'STOPPED', 'FAILED')
                ) THEN 'FAILED'
                ELSE 'DRAFT'
            END,
            fecha_modificacion = now()
            WHERE s.id = $1
            """,
            slice_id,
        )

    async def decide_approval(
        self,
        connection: asyncpg.Connection,
        *,
        deployment_id: int,
        operator_id: int,
        approved: bool,
        reason: str | None,
    ) -> tuple[str, str]:
        row = await connection.fetchrow(
            """
            SELECT d.workflow_id, a.estado
            FROM slices.deployment AS d
            JOIN slices.solicitud_aprobacion AS a ON a.deployment_id = d.id
            WHERE d.id = $1
            FOR UPDATE OF d, a
            """,
            deployment_id,
        )
        if row is None:
            raise LookupError(f"deployment {deployment_id} was not found")
        desired = "APPROVED" if approved else "REJECTED_BY_OPERATOR"
        if row["estado"] == desired:
            return desired, row["workflow_id"]
        if row["estado"] != "PENDING":
            raise ValueError("approval request is no longer pending")
        await connection.execute(
            """
            UPDATE slices.solicitud_aprobacion
            SET estado = $2, operador_id = $3, motivo = $4
            WHERE deployment_id = $1
            """,
            deployment_id,
            desired,
            operator_id,
            reason,
        )
        return desired, row["workflow_id"]

    async def verify_operator(self, connection: asyncpg.Connection, operator_id: int) -> bool:
        return await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM auth.usuario AS u
                JOIN auth.rol AS r ON r.id = u.rol_id
                WHERE u.id = $1 AND u.estado = 'ACTIVE'
                  AND r.nombre IN ('operador', 'admin')
            )
            """,
            operator_id,
        )
