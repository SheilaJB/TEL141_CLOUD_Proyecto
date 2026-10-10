import json
from collections.abc import Mapping
from typing import Any

import asyncpg


EMPTY_SLICE_SPEC: dict[str, Any] = {
    "schema_version": 1,
    "vms": [],
    "links": [],
}


class SliceRepository:
    async def get_active_actor(
        self, connection: asyncpg.Connection, user_id: int
    ) -> asyncpg.Record | None:
        return await connection.fetchrow(
            """
            SELECT u.id, u.nivel_id, r.nombre AS role, n.nombre AS service_level
            FROM auth.usuario AS u
            JOIN auth.rol AS r ON r.id = u.rol_id
            LEFT JOIN auth.nivel AS n ON n.id = u.nivel_id
            WHERE u.id = $1 AND u.estado = 'ACTIVE'
            """,
            user_id,
        )

    async def get_zone(
        self,
        connection: asyncpg.Connection,
        *,
        zone_id: int,
        service_level_id: int,
    ) -> asyncpg.Record | None:
        return await connection.fetchrow(
            """
            SELECT z.id, z.estado, z.cluster_id, c.nombre AS cluster_name,
                   EXISTS (
                       SELECT 1 FROM auth.nivel_accede_zona AS az
                       WHERE az.nivel_id = $2 AND az.zona_id = z.id
                   ) AS level_allowed
            FROM slices.zona_disponibilidad AS z
            LEFT JOIN slices.cluster AS c ON c.id = z.cluster_id
            WHERE z.id = $1
            """,
            zone_id,
            service_level_id,
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

    async def get_visible_template(
        self,
        connection: asyncpg.Connection,
        *,
        template_id: int,
        user_id: int,
    ) -> asyncpg.Record | None:
        return await connection.fetchrow(
            """
            SELECT id, dueño_id, spec
            FROM slices.plantilla
            WHERE id = $1 AND (dueño_id IS NULL OR dueño_id = $2)
            """,
            template_id,
            user_id,
        )

    async def insert_slice(
        self,
        connection: asyncpg.Connection,
        *,
        user_id: int,
        name: str,
        zone_id: int,
    ) -> int:
        return await connection.fetchval(
            """
            INSERT INTO slices.slice (usuario_id, nombre, estado, zona_id, cluster_id)
            SELECT $1, $2, 'DRAFT', z.id, z.cluster_id
            FROM slices.zona_disponibilidad AS z
            WHERE z.id = $3
            RETURNING id
            """,
            user_id,
            name,
            zone_id,
        )

    async def insert_version(
        self,
        connection: asyncpg.Connection,
        *,
        slice_id: int,
        version_number: int,
        spec: Mapping[str, Any],
        custom: bool,
        template_id: int | None,
    ) -> int:
        return await connection.fetchval(
            """
            INSERT INTO slices.slice_version
                (slice_id, numero_version, spec, custom, plantilla_origen_id, estado_version)
            VALUES ($1, $2, $3::jsonb, $4, $5, 'DRAFT')
            RETURNING id
            """,
            slice_id,
            version_number,
            json.dumps(spec),
            custom,
            template_id,
        )

    async def lock_slice(
        self, connection: asyncpg.Connection, slice_id: int
    ) -> asyncpg.Record | None:
        return await connection.fetchrow(
            """
            SELECT id, usuario_id, estado, version_activa_id
            FROM slices.slice
            WHERE id = $1
            FOR UPDATE
            """,
            slice_id,
        )

    async def get_version(
        self,
        connection: asyncpg.Connection,
        *,
        slice_id: int,
        version_number: int,
        for_update: bool = False,
    ) -> asyncpg.Record | None:
        lock_clause = "FOR UPDATE OF sv" if for_update else ""
        return await connection.fetchrow(
            f"""
            SELECT sv.id, sv.numero_version, sv.spec, sv.custom,
                   sv.plantilla_origen_id, sv.estado_version
            FROM slices.slice_version AS sv
            WHERE sv.slice_id = $1 AND sv.numero_version = $2
            {lock_clause}
            """,
            slice_id,
            version_number,
        )

    async def get_version_by_id(
        self, connection: asyncpg.Connection, version_id: int
    ) -> asyncpg.Record | None:
        return await connection.fetchrow(
            """
            SELECT id, slice_id, numero_version, spec, custom, plantilla_origen_id,
                   estado_version
            FROM slices.slice_version
            WHERE id = $1
            """,
            version_id,
        )

    async def get_slice_plan_context(
        self,
        connection: asyncpg.Connection,
        *,
        slice_id: int,
        version_number: int,
    ) -> asyncpg.Record | None:
        return await connection.fetchrow(
            """
            SELECT s.id AS slice_id, s.usuario_id, s.estado AS slice_state,
                   s.version_activa_id, s.zona_id, z.estado AS zone_state,
                   z.cluster_id, c.nombre AS cluster_name,
                   sv.id AS version_id, sv.numero_version, sv.spec,
                   sv.estado_version, u.nivel_id, n.nombre AS service_level
            FROM slices.slice AS s
            JOIN slices.slice_version AS sv ON sv.slice_id = s.id
            JOIN slices.zona_disponibilidad AS z ON z.id = s.zona_id
            JOIN slices.cluster AS c ON c.id = z.cluster_id
            JOIN auth.usuario AS u ON u.id = s.usuario_id
            LEFT JOIN auth.nivel AS n ON n.id = u.nivel_id
            WHERE s.id = $1 AND sv.numero_version = $2 AND u.estado = 'ACTIVE'
            """,
            slice_id,
            version_number,
        )

    async def next_version_number(
        self, connection: asyncpg.Connection, slice_id: int
    ) -> int:
        current = await connection.fetchval(
            """
            SELECT COALESCE(MAX(numero_version), 0)
            FROM slices.slice_version
            WHERE slice_id = $1
            """,
            slice_id,
        )
        return int(current) + 1

    async def version_is_referenced(
        self, connection: asyncpg.Connection, version_id: int
    ) -> bool:
        return await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM slices.deployment
                WHERE origin_version_id = $1 OR target_version_id = $1
            )
            OR EXISTS (
                SELECT 1 FROM slices.slice
                WHERE version_activa_id = $1
            )
            OR EXISTS (
                SELECT 1 FROM slices.slice_nodo
                WHERE defin_version_id = $1
            )
            OR EXISTS (
                SELECT 1 FROM slices.slice_enlace
                WHERE defin_version_id = $1
            )
            """,
            version_id,
        )

    async def update_version_spec(
        self,
        connection: asyncpg.Connection,
        *,
        version_id: int,
        spec: Mapping[str, Any],
    ) -> bool:
        result = await connection.execute(
            """
            UPDATE slices.slice_version
            SET spec = $2::jsonb, custom = TRUE, plantilla_origen_id = NULL
            WHERE id = $1 AND estado_version = 'DRAFT'
            """,
            version_id,
            json.dumps(spec),
        )
        return result.endswith(" 1")

    async def delete_version(
        self, connection: asyncpg.Connection, version_id: int
    ) -> bool:
        result = await connection.execute(
            """
            DELETE FROM slices.slice_version
            WHERE id = $1 AND estado_version = 'DRAFT'
            """,
            version_id,
        )
        return result.endswith(" 1")

    async def touch_slice(
        self, connection: asyncpg.Connection, slice_id: int
    ) -> None:
        await connection.execute(
            "UPDATE slices.slice SET fecha_modificacion = now() WHERE id = $1",
            slice_id,
        )
