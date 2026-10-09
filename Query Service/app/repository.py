import json
from typing import Any, Dict, List, Optional
import asyncpg
from app.schemas import (
    ClusterMetrics,
    FlavorResponse,
    ImageResponse,
    QuotaUsage,
    RequestSummary,
    ServerDetail,
    SliceLinkDetail,
    SliceNodeDetail,
    SlicePortDetail,
    SliceSummary,
    SliceTopology,
    UserAdminView,
    UserQuotaSummary,
    ZoneResponse,
)


class QueryRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def list_flavors(self, active_only: bool = True) -> List[FlavorResponse]:
        query = """
            SELECT id, name, descripcion, vcpu, vram_mb, vdisk_mb, activo
            FROM slices.flavor
        """
        if active_only:
            query += " WHERE activo = TRUE"
        query += " ORDER BY id ASC"

        rows = await self.pool.fetch(query)
        return [FlavorResponse(**dict(r)) for r in rows]

    async def list_images(self, active_only: bool = True) -> List[ImageResponse]:
        query = """
            SELECT id, nombre, cluster_compatible, ruta_referencia, contador_refs, estado
            FROM slices.image
        """
        if active_only:
            query += " WHERE estado = 'ACTIVE'"
        query += " ORDER BY id ASC"

        rows = await self.pool.fetch(query)
        return [ImageResponse(**dict(r)) for r in rows]

    async def list_zones(
        self,
        service_level: Optional[str] = None,
        is_admin: bool = False,
    ) -> List[ZoneResponse]:
        if is_admin or not service_level:
            query = """
                SELECT id, nombre, cluster_id, estado,
                       cpu_allocation_ratio_default, cpu_allocation_ratio_limit,
                       ram_allocation_ratio_default, ram_allocation_ratio_limit,
                       disk_allocation_ratio_default, disk_allocation_ratio_limit
                FROM slices.zona_disponibilidad
                ORDER BY id ASC
            """
            rows = await self.pool.fetch(query)
        else:
            query = """
                SELECT z.id, z.nombre, z.cluster_id, z.estado,
                       z.cpu_allocation_ratio_default, z.cpu_allocation_ratio_limit,
                       z.ram_allocation_ratio_default, z.ram_allocation_ratio_limit,
                       z.disk_allocation_ratio_default, z.disk_allocation_ratio_limit
                FROM slices.zona_disponibilidad z
                JOIN auth.nivel_accede_zona naz ON z.id = naz.zona_id
                JOIN auth.nivel n ON naz.nivel_id = n.id
                WHERE n.nombre = $1
                ORDER BY z.id ASC
            """
            rows = await self.pool.fetch(query, service_level)

        return [
            ZoneResponse(
                id=r["id"],
                nombre=r["nombre"],
                cluster_id=r["cluster_id"],
                estado=r["estado"],
                cpu_allocation_ratio_default=float(r["cpu_allocation_ratio_default"]),
                cpu_allocation_ratio_limit=float(r["cpu_allocation_ratio_limit"]),
                ram_allocation_ratio_default=float(r["ram_allocation_ratio_default"]),
                ram_allocation_ratio_limit=float(r["ram_allocation_ratio_limit"]),
                disk_allocation_ratio_default=float(r["disk_allocation_ratio_default"]),
                disk_allocation_ratio_limit=float(r["disk_allocation_ratio_limit"]),
            )
            for r in rows
        ]

    async def get_user_quota(self, user_id: int) -> UserQuotaSummary:
        user_row = await self.pool.fetchrow(
            """
            SELECT u.id, n.nombre AS nivel_nombre, u.nivel_id
            FROM auth.usuario u
            LEFT JOIN auth.nivel n ON u.nivel_id = n.id
            WHERE u.id = $1
            """,
            user_id,
        )
        if not user_row:
            return UserQuotaSummary(usuario_id=user_id, nivel=None, cuotas=[])

        nivel_id = user_row["nivel_id"]
        nivel_nombre = user_row["nivel_nombre"]

        if not nivel_id:
            return UserQuotaSummary(usuario_id=user_id, nivel=None, cuotas=[])

        limits_rows = await self.pool.fetch(
            """
            SELECT c.recurso,
                   COALESCE(co.prometido, c.prometido) AS limite
            FROM auth.cuota c
            LEFT JOIN auth.cuota_override co
                   ON co.usuario_id = $1 AND co.recurso = c.recurso
            WHERE c.nivel_id = $2
            """,
            user_id,
            nivel_id,
        )

        consumed_rows = await self.pool.fetch(
            """
            SELECT
                COALESCE(SUM(f.vcpu), 0) AS vcpu_used,
                COALESCE(SUM(f.vram_mb), 0) AS ram_mb_used,
                COALESCE(SUM(f.vdisk_mb), 0) AS disk_mb_used
            FROM slices.slice s
            JOIN slices.slice_nodo sn ON sn.slice_id = s.id
            JOIN slices.flavor f ON sn.flavor_id = f.id
            WHERE s.usuario_id = $1
              AND s.estado IN ('RUNNING', 'UPDATING')
              AND sn.estado_nodo IN ('RUNNING', 'PENDING')
            """,
            user_id,
        )
        consumed = (
            consumed_rows[0]
            if consumed_rows
            else {"vcpu_used": 0, "ram_mb_used": 0, "disk_mb_used": 0}
        )

        pending_rows = await self.pool.fetch(
            """
            SELECT
                COALESCE(SUM(f.vcpu), 0) AS vcpu_pending,
                COALESCE(SUM(f.vram_mb), 0) AS ram_mb_pending,
                COALESCE(SUM(f.vdisk_mb), 0) AS disk_mb_pending
            FROM slices.deployment d
            JOIN slices.slice s ON d.slice_id = s.id
            JOIN slices.slice_nodo sn ON sn.defin_version_id = d.target_version_id
            JOIN slices.flavor f ON sn.flavor_id = f.id
            WHERE d.solicitado_por = $1
              AND d.estado IN ('PENDING', 'IN_PROGRESS')
              AND s.estado NOT IN ('RUNNING', 'UPDATING')
            """,
            user_id,
        )
        pending = (
            pending_rows[0]
            if pending_rows
            else {"vcpu_pending": 0, "ram_mb_pending": 0, "disk_mb_pending": 0}
        )

        consumed_map = {
            "vcpu": float(consumed["vcpu_used"]),
            "ram_mb": float(consumed["ram_mb_used"]),
            "disk_mb": float(consumed["disk_mb_used"]),
        }
        pending_map = {
            "vcpu": float(pending["vcpu_pending"]),
            "ram_mb": float(pending["ram_mb_pending"]),
            "disk_mb": float(pending["disk_mb_pending"]),
        }

        cuotas: List[QuotaUsage] = []
        for r in limits_rows:
            rec = r["recurso"]
            lim = float(r["limite"])
            used = consumed_map.get(rec, 0.0)
            res = pending_map.get(rec, 0.0)
            disp = max(0.0, lim - (used + res))
            cuotas.append(
                QuotaUsage(
                    recurso=rec,
                    prometido=lim,
                    consumido_activos=used,
                    reservado_pendientes=res,
                    disponible=disp,
                )
            )

        return UserQuotaSummary(
            usuario_id=user_id,
            nivel=nivel_nombre,
            cuotas=cuotas,
        )

    async def list_slices(
        self,
        user_id: Optional[int] = None,
        estado: Optional[str] = None,
    ) -> List[SliceSummary]:
        conditions = ["s.estado != 'ELIMINATED'"]
        params = []
        idx = 1

        if user_id is not None:
            conditions.append(f"s.usuario_id = ${idx}")
            params.append(user_id)
            idx += 1

        if estado is not None:
            conditions.append(f"s.estado = ${idx}")
            params.append(estado.upper())
            idx += 1

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT s.id, s.nombre, s.estado, s.zona_id, z.nombre AS zona_nombre,
                   s.vlan_s, s.version_activa_id, sv.numero_version AS version_activa_numero,
                   s.usuario_id, s.fecha_creacion, s.fecha_modificacion
            FROM slices.slice s
            LEFT JOIN slices.zona_disponibilidad z ON s.zona_id = z.id
            LEFT JOIN slices.slice_version sv ON s.version_activa_id = sv.id
            WHERE {where_clause}
            ORDER BY s.id DESC
        """

        rows = await self.pool.fetch(query, *params)
        return [SliceSummary(**dict(r)) for r in rows]

    async def get_slice_detail(
        self,
        slice_id: int,
        user_id: Optional[int] = None,
    ) -> Optional[SliceSummary]:
        conditions = ["s.id = $1", "s.estado != 'ELIMINATED'"]
        params = [slice_id]

        if user_id is not None:
            conditions.append("s.usuario_id = $2")
            params.append(user_id)

        query = f"""
            SELECT s.id, s.nombre, s.estado, s.zona_id, z.nombre AS zona_nombre,
                   s.vlan_s, s.version_activa_id, sv.numero_version AS version_activa_numero,
                   s.usuario_id, s.fecha_creacion, s.fecha_modificacion
            FROM slices.slice s
            LEFT JOIN slices.zona_disponibilidad z ON s.zona_id = z.id
            LEFT JOIN slices.slice_version sv ON s.version_activa_id = sv.id
            WHERE {" AND ".join(conditions)}
        """

        row = await self.pool.fetchrow(query, *params)
        return SliceSummary(**dict(row)) if row else None

    async def get_slice_topology(
        self,
        slice_id: int,
        user_id: Optional[int] = None,
    ) -> Optional[SliceTopology]:
        slice_info = await self.get_slice_detail(slice_id=slice_id, user_id=user_id)
        if not slice_info:
            return None

        version_row = await self.pool.fetchrow(
            """
            SELECT sv.id, sv.numero_version, sv.spec
            FROM slices.slice s
            JOIN slices.slice_version sv
              ON (s.version_activa_id = sv.id OR (s.version_activa_id IS NULL AND sv.slice_id = s.id))
            WHERE s.id = $1
            ORDER BY sv.numero_version DESC
            LIMIT 1
            """,
            slice_id,
        )

        version_id = version_row["id"] if version_row else None
        version_num = version_row["numero_version"] if version_row else None
        raw_spec = version_row["spec"] if version_row else None
        spec_dict = json.loads(raw_spec) if isinstance(raw_spec, str) else raw_spec

        nodes: List[SliceNodeDetail] = []
        if version_id:
            node_rows = await self.pool.fetch(
                """
                SELECT sn.id::text, sn.name, sn.flavor_id, f.name AS flavor_name,
                       f.vcpu, f.vram_mb, f.vdisk_mb,
                       sn.imagen_id, img.nombre AS imagen_nombre,
                       sn.estado_nodo, sn.pid, sn.puerto_vnc,
                       rn.servidor_id, srv.ip_serv::text AS servidor_ip
                FROM slices.slice_nodo sn
                JOIN slices.flavor f ON sn.flavor_id = f.id
                JOIN slices.image img ON sn.imagen_id = img.id
                LEFT JOIN slices.reserva_nodo rn
                       ON rn.nodo_id = sn.id AND rn.estado = 'RESERVED' AND rn.recurso = 'vcpu'
                LEFT JOIN slices.servidor srv ON rn.servidor_id = srv.id
                WHERE sn.defin_version_id = $1
                ORDER BY sn.name ASC
                """,
                version_id,
            )
            for nr in node_rows:
                nodes.append(SliceNodeDetail(**dict(nr)))

        links: List[SliceLinkDetail] = []
        if version_id:
            link_rows = await self.pool.fetch(
                """
                SELECT se.id::text, se.name
                FROM slices.slice_enlace se
                WHERE se.defin_version_id = $1
                ORDER BY se.name ASC
                """,
                version_id,
            )

            port_rows = await self.pool.fetch(
                """
                SELECT sep.id::text, sep.enlace_id::text, sep.name,
                       sep.nodo_id::text, sn.name AS nodo_name,
                       sep.public, sep.mac::text, sep.ip::text,
                       sep.vlan_tag, sep.ns
                FROM slices.slice_enlace_puerto sep
                JOIN slices.slice_enlace se ON sep.enlace_id = se.id
                JOIN slices.slice_nodo sn ON sep.nodo_id = sn.id
                WHERE se.defin_version_id = $1
                ORDER BY sep.name ASC
                """,
                version_id,
            )

            ports_by_link: Dict[str, List[SlicePortDetail]] = {}
            for pr in port_rows:
                lid = pr["enlace_id"]
                p_detail = SlicePortDetail(
                    id=pr["id"],
                    name=pr["name"],
                    nodo_id=pr["nodo_id"],
                    nodo_name=pr["nodo_name"],
                    public=pr["public"],
                    mac=pr["mac"],
                    ip=pr["ip"],
                    vlan_tag=pr["vlan_tag"],
                    ns=pr["ns"],
                )
                ports_by_link.setdefault(lid, []).append(p_detail)

            for lr in link_rows:
                lid = lr["id"]
                links.append(
                    SliceLinkDetail(
                        id=lid,
                        name=lr["name"],
                        puertos=ports_by_link.get(lid, []),
                    )
                )

        return SliceTopology(
            slice_id=slice_info.id,
            nombre=slice_info.nombre,
            estado=slice_info.estado,
            zona_id=slice_info.zona_id,
            vlan_s=slice_info.vlan_s,
            version_id=version_id,
            numero_version=version_num,
            spec=spec_dict,
            nodos=nodes,
            enlaces=links,
        )

    async def list_requests(
        self,
        user_id: Optional[int] = None,
        estado: Optional[str] = None,
    ) -> List[RequestSummary]:
        conditions = []
        params = []
        idx = 1

        if user_id is not None:
            conditions.append(f"sa.usuario_id = ${idx}")
            params.append(user_id)
            idx += 1

        if estado is not None:
            conditions.append(f"sa.estado = ${idx}")
            params.append(estado.upper())
            idx += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT sa.id, sa.deployment_id, d.slice_id, s.nombre AS slice_nombre,
                   sa.usuario_id, u.codigo AS usuario_codigo,
                   sa.estado, sa.operador_id, sa.fecha_sol, sa.motivo,
                   d.tipo AS tipo_deployment
            FROM slices.solicitud_aprobacion sa
            JOIN slices.deployment d ON sa.deployment_id = d.id
            JOIN slices.slice s ON d.slice_id = s.id
            JOIN auth.usuario u ON sa.usuario_id = u.id
            {where_clause}
            ORDER BY sa.id DESC
        """

        rows = await self.pool.fetch(query, *params)
        return [RequestSummary(**dict(r)) for r in rows]

    async def list_servers(self) -> List[ServerDetail]:
        query = """
            SELECT srv.id, srv.ip_serv::text, srv.mac_serv::text,
                   srv.total_cpu_cores, srv.total_ram_mb, srv.total_disk_mb,
                   srv.vcpu_reserved, srv.vram_mb_reserved, srv.vdisk_mb_reserved,
                   srv.zona_id, z.nombre AS zona_nombre, srv.estado
            FROM slices.servidor srv
            LEFT JOIN slices.zona_disponibilidad z ON srv.zona_id = z.id
            ORDER BY srv.id ASC
        """
        rows = await self.pool.fetch(query)
        result = []
        for r in rows:
            cpu_total = r["total_cpu_cores"] or 1
            ram_total = r["total_ram_mb"] or 1
            disk_total = r["total_disk_mb"] or 1
            cpu_pct = round((r["vcpu_reserved"] / cpu_total) * 100, 2)
            ram_pct = round((r["vram_mb_reserved"] / ram_total) * 100, 2)
            disk_pct = round((r["vdisk_mb_reserved"] / disk_total) * 100, 2)

            result.append(
                ServerDetail(
                    id=r["id"],
                    ip_serv=r["ip_serv"],
                    mac_serv=r["mac_serv"],
                    total_cpu_cores=r["total_cpu_cores"],
                    total_ram_mb=r["total_ram_mb"],
                    total_disk_mb=r["total_disk_mb"],
                    vcpu_reserved=r["vcpu_reserved"],
                    vram_mb_reserved=r["vram_mb_reserved"],
                    vdisk_mb_reserved=r["vdisk_mb_reserved"],
                    zona_id=r["zona_id"],
                    zona_nombre=r["zona_nombre"],
                    estado=r["estado"],
                    cpu_usage_pct=cpu_pct,
                    ram_usage_pct=ram_pct,
                    disk_usage_pct=disk_pct,
                )
            )
        return result

    async def list_users(self) -> List[UserAdminView]:
        query = """
            SELECT u.id, u.codigo, r.nombre AS rol, n.nombre AS nivel,
                   u.estado, u.fecha_creacion
            FROM auth.usuario u
            JOIN auth.rol r ON u.rol_id = r.id
            LEFT JOIN auth.nivel n ON u.nivel_id = n.id
            ORDER BY u.id ASC
        """
        rows = await self.pool.fetch(query)
        return [UserAdminView(**dict(r)) for r in rows]

    async def get_cluster_metrics(self) -> ClusterMetrics:
        slice_stats = await self.pool.fetchrow(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE estado = 'RUNNING') AS running,
                COUNT(*) FILTER (WHERE estado = 'DRAFT') AS drafts
            FROM slices.slice
            WHERE estado != 'ELIMINATED'
            """
        )

        node_stats = await self.pool.fetchrow(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE estado_nodo = 'RUNNING') AS running
            FROM slices.slice_nodo
            WHERE estado_nodo != 'DELETED'
            """
        )

        approval_stats = await self.pool.fetchrow(
            """
            SELECT COUNT(*) AS pending
            FROM slices.solicitud_aprobacion
            WHERE estado IN ('VALIDATING', 'PENDING')
            """
        )

        server_stats = await self.pool.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE estado = 'ACTIVE') AS active_servers,
                COALESCE(SUM(vcpu_reserved), 0) AS total_vcpu,
                COALESCE(SUM(vram_mb_reserved), 0) AS total_vram,
                COALESCE(SUM(vdisk_mb_reserved), 0) AS total_vdisk
            FROM slices.servidor
            """
        )

        return ClusterMetrics(
            total_slices=slice_stats["total"] if slice_stats else 0,
            running_slices=slice_stats["running"] if slice_stats else 0,
            draft_slices=slice_stats["drafts"] if slice_stats else 0,
            total_nodes=node_stats["total"] if node_stats else 0,
            running_nodes=node_stats["running"] if node_stats else 0,
            pending_approvals=approval_stats["pending"] if approval_stats else 0,
            active_servers=server_stats["active_servers"] if server_stats else 0,
            total_vcpu_allocated=int(server_stats["total_vcpu"]) if server_stats else 0,
            total_vram_mb_allocated=int(server_stats["total_vram"]) if server_stats else 0,
            total_vdisk_mb_allocated=int(server_stats["total_vdisk"]) if server_stats else 0,
        )
