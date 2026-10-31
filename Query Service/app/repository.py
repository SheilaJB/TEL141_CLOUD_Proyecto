from datetime import datetime
from typing import Any, Dict, List, Optional
import json
import asyncpg
from app.schemas import (
    ClusterMetrics,
    DeploymentSummary,
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
    TemplateResponse,
    AdminQuotaItem,
    UserAdminView,
    UserQuotaSummary,
    ZoneResponse,
    AuditEvent,
    SecurityAnomaly,
)


class QueryRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    # -------------------------------------------------------------------------
    # Catálogos (Flavors, Imágenes, Zonas, Plantillas)
    # -------------------------------------------------------------------------

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

    async def list_templates(
        self, user_id: Optional[int] = None
    ) -> List[TemplateResponse]:
        if user_id is not None:
            query = """
                SELECT id, dueño_id, nombre, spec
                FROM slices.plantilla
                WHERE dueño_id IS NULL OR dueño_id = $1
                ORDER BY id ASC
            """
            rows = await self.pool.fetch(query, user_id)
        else:
            query = """
                SELECT id, dueño_id, nombre, spec
                FROM slices.plantilla
                ORDER BY id ASC
            """
            rows = await self.pool.fetch(query)

        results = []
        for r in rows:
            raw_spec = r["spec"]
            spec_dict = json.loads(raw_spec) if isinstance(raw_spec, str) else raw_spec
            results.append(
                TemplateResponse(
                    id=r["id"],
                    dueño_id=r["dueño_id"],
                    nombre=r["nombre"],
                    spec=spec_dict or {},
                )
            )
        return results

    # -------------------------------------------------------------------------
    # Cuotas de Usuario
    # -------------------------------------------------------------------------

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

    # -------------------------------------------------------------------------
    # Slices y Topología
    # -------------------------------------------------------------------------

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
                   s.cluster_id, c.nombre AS cluster_nombre,
                   s.vlan_stag, s.version_activa_id, sv.numero_version AS version_activa_numero,
                   s.usuario_id, s.fecha_creacion, s.fecha_modificacion
            FROM slices.slice s
            LEFT JOIN slices.zona_disponibilidad z ON s.zona_id = z.id
            LEFT JOIN slices.cluster c ON s.cluster_id = c.id
            LEFT JOIN slices.slice_version sv ON s.version_activa_id = sv.id
            WHERE {where_clause}
            ORDER BY s.id DESC
        """

        rows = await self.pool.fetch(query, *params)
        results = []
        for r in rows:
            d = dict(r)
            d["vlan_s"] = d.get("vlan_stag")
            results.append(SliceSummary(**d))
        return results

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
                   s.cluster_id, c.nombre AS cluster_nombre,
                   s.vlan_stag, s.version_activa_id, sv.numero_version AS version_activa_numero,
                   s.usuario_id, s.fecha_creacion, s.fecha_modificacion
            FROM slices.slice s
            LEFT JOIN slices.zona_disponibilidad z ON s.zona_id = z.id
            LEFT JOIN slices.cluster c ON s.cluster_id = c.id
            LEFT JOIN slices.slice_version sv ON s.version_activa_id = sv.id
            WHERE {" AND ".join(conditions)}
        """

        row = await self.pool.fetchrow(query, *params)
        if not row:
            return None
        d = dict(row)
        d["vlan_s"] = d.get("vlan_stag")
        return SliceSummary(**d)

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
                       sn.estado_nodo, sn.public_access, sn.pid,
                       sn.vnc_display, sn.deployment_id,
                       COALESCE(sn.server_id, rn.servidor_id) AS servidor_id,
                       srv.ip_serv::text AS servidor_ip
                FROM slices.slice_nodo sn
                JOIN slices.flavor f ON sn.flavor_id = f.id
                JOIN slices.image img ON sn.imagen_id = img.id
                LEFT JOIN slices.reserva_nodo rn
                       ON rn.nodo_id = sn.id AND rn.estado = 'RESERVED' AND rn.recurso = 'vcpu'
                LEFT JOIN slices.servidor srv
                       ON srv.id = COALESCE(sn.server_id, rn.servidor_id)
                WHERE sn.defin_version_id = $1
                ORDER BY sn.name ASC
                """,
                version_id,
            )
            for nr in node_rows:
                d = dict(nr)
                d["puerto_vnc"] = d.get("vnc_display")
                nodes.append(SliceNodeDetail(**d))

        links: List[SliceLinkDetail] = []
        if version_id:
            link_rows = await self.pool.fetch(
                """
                SELECT se.id::text, se.name, se.public,
                       se.cidr::text, se.vlan_ctag, se.estado, se.deployment_id
                FROM slices.slice_enlace se
                WHERE se.defin_version_id = $1
                ORDER BY se.name ASC
                """,
                version_id,
            )

            port_rows = await self.pool.fetch(
                """
                SELECT sep.id::text, sep.enlace_id::text,
                       sep.nodo_id::text, sn.name AS nodo_name,
                       sep.public, sep.nic_index,
                       sep.mac::text, sep.ip::text,
                       sep.estado, sep.deployment_id
                FROM slices.slice_enlace_puerto sep
                JOIN slices.slice_enlace se ON sep.enlace_id = se.id
                JOIN slices.slice_nodo sn ON sep.nodo_id = sn.id
                WHERE se.defin_version_id = $1
                ORDER BY sep.nic_index ASC NULLS LAST, sep.id ASC
                """,
                version_id,
            )

            ports_by_link: Dict[str, List[SlicePortDetail]] = {}
            for pr in port_rows:
                lid = pr["enlace_id"]
                p_detail = SlicePortDetail(
                    id=pr["id"],
                    enlace_id=pr["enlace_id"],
                    nodo_id=pr["nodo_id"],
                    nodo_name=pr["nodo_name"],
                    public=pr["public"],
                    nic_index=pr["nic_index"],
                    mac=pr["mac"],
                    ip=pr["ip"],
                    estado=pr["estado"],
                    deployment_id=pr["deployment_id"],
                )
                ports_by_link.setdefault(lid, []).append(p_detail)

            for lr in link_rows:
                lid = lr["id"]
                links.append(
                    SliceLinkDetail(
                        id=lid,
                        name=lr["name"],
                        public=lr["public"],
                        cidr=lr["cidr"],
                        vlan_ctag=lr["vlan_ctag"],
                        estado=lr["estado"],
                        deployment_id=lr["deployment_id"],
                        puertos=ports_by_link.get(lid, []),
                    )
                )

        return SliceTopology(
            slice_id=slice_info.id,
            nombre=slice_info.nombre,
            estado=slice_info.estado,
            zona_id=slice_info.zona_id,
            zona_nombre=slice_info.zona_nombre,
            cluster_id=slice_info.cluster_id,
            cluster_nombre=slice_info.cluster_nombre,
            vlan_stag=slice_info.vlan_stag,
            vlan_s=slice_info.vlan_stag,
            version_id=version_id,
            numero_version=version_num,
            spec=spec_dict,
            nodos=nodes,
            enlaces=links,
        )

    # -------------------------------------------------------------------------
    # Solicitudes de Aprobación y Despliegues
    # -------------------------------------------------------------------------

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

    async def list_deployments(
        self,
        user_id: Optional[int] = None,
        slice_id: Optional[int] = None,
        estado: Optional[str] = None,
    ) -> List[DeploymentSummary]:
        conditions = []
        params = []
        idx = 1

        if user_id is not None:
            conditions.append(f"d.solicitado_por = ${idx}")
            params.append(user_id)
            idx += 1

        if slice_id is not None:
            conditions.append(f"d.slice_id = ${idx}")
            params.append(slice_id)
            idx += 1

        if estado is not None:
            conditions.append(f"d.estado = ${idx}")
            params.append(estado.upper())
            idx += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT d.id, d.slice_id, s.nombre AS slice_nombre,
                   d.origin_version_id, d.target_version_id,
                   d.tipo, d.estado, d.workflow_id,
                   d.plan, d.error, d.solicitado_por,
                   u.codigo AS solicitado_por_codigo,
                   d.created_at, d.finished_at
            FROM slices.deployment d
            JOIN slices.slice s ON d.slice_id = s.id
            JOIN auth.usuario u ON d.solicitado_por = u.id
            {where_clause}
            ORDER BY d.id DESC
        """

        rows = await self.pool.fetch(query, *params)
        results = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("plan"), str):
                d["plan"] = json.loads(d["plan"])
            if isinstance(d.get("error"), str):
                d["error"] = json.loads(d["error"])
            results.append(DeploymentSummary(**d))
        return results

    async def get_deployment_detail(
        self,
        deployment_id: int,
        user_id: Optional[int] = None,
    ) -> Optional[DeploymentSummary]:
        conditions = ["d.id = $1"]
        params = [deployment_id]

        if user_id is not None:
            conditions.append("d.solicitado_por = $2")
            params.append(user_id)

        query = f"""
            SELECT d.id, d.slice_id, s.nombre AS slice_nombre,
                   d.origin_version_id, d.target_version_id,
                   d.tipo, d.estado, d.workflow_id,
                   d.plan, d.error, d.solicitado_por,
                   u.codigo AS solicitado_por_codigo,
                   d.created_at, d.finished_at
            FROM slices.deployment d
            JOIN slices.slice s ON d.slice_id = s.id
            JOIN auth.usuario u ON d.solicitado_por = u.id
            WHERE {" AND ".join(conditions)}
        """

        row = await self.pool.fetchrow(query, *params)
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("plan"), str):
            d["plan"] = json.loads(d["plan"])
        if isinstance(d.get("error"), str):
            d["error"] = json.loads(d["error"])
        return DeploymentSummary(**d)

    # -------------------------------------------------------------------------
    # Vistas de Servidores, Usuarios y Métricas
    # -------------------------------------------------------------------------

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

    # -------------------------------------------------------------------------
    # Métodos Administrativos Exclusivos
    # -------------------------------------------------------------------------

    async def list_admin_quotas(self) -> List[AdminQuotaItem]:
        query = """
            SELECT c.id, c.nivel_id, n.nombre AS nivel_nombre, c.recurso, c.prometido
            FROM auth.cuota c
            JOIN auth.nivel n ON c.nivel_id = n.id
            ORDER BY c.nivel_id ASC, c.recurso ASC
        """
        rows = await self.pool.fetch(query)
        return [AdminQuotaItem(**dict(r)) for r in rows]

    async def update_admin_quota(self, nivel_id: int, recurso: str, prometido: float) -> bool:
        query = """
            UPDATE auth.cuota
            SET prometido = $1
            WHERE nivel_id = $2 AND recurso = $3
        """
        result = await self.pool.execute(query, prometido, nivel_id, recurso)
        if result != "UPDATE 0":
            try:
                await self.record_audit_event(
                    tipo_evento="QUOTA_UPDATED",
                    severidad="INFO",
                    descripcion=f"Actualización de cuota para Nivel #{nivel_id}: Recurso [{recurso}] ajustado a {prometido}",
                    entidad="CUOTA",
                    entidad_id=f"Nivel_{nivel_id}_{recurso}"
                )
            except Exception:
                pass
            return True
        return False

    async def intervene_slice(self, slice_id: int, accion: str, motivo: str, admin_id: int) -> Dict[str, Any]:
        """
        Intervención administrativa de slices justificada (STOP o DELETE).
        Registra motivo y cambia estado en slices.slice y slices.slice_nodo.
        """
        nuevo_estado = "STOPPED" if accion.upper() == "STOP" else "ELIMINATED"
        nodo_estado = "STOPPED" if accion.upper() == "STOP" else "DELETED"

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Actualizar slice
                row = await conn.fetchrow(
                    """
                    UPDATE slices.slice
                    SET estado = $1, fecha_modificacion = NOW()
                    WHERE id = $2
                    RETURNING id, nombre, estado, usuario_id
                    """,
                    nuevo_estado, slice_id
                )
                if not row:
                    return {"ok": False, "error": f"Slice #{slice_id} no encontrado."}

                # Actualizar nodos
                await conn.execute(
                    """
                    UPDATE slices.slice_nodo
                    SET estado_nodo = $1
                    WHERE slice_id = $2
                    """,
                    nodo_estado, slice_id
                )

                # Liberar reservas si se elimina
                if nuevo_estado == "ELIMINATED":
                    await conn.execute(
                        """
                        UPDATE slices.servidor s
                        SET vcpu_reserved = GREATEST(0, s.vcpu_reserved - COALESCE(sub.vcpu, 0)),
                            vram_mb_reserved = GREATEST(0, s.vram_mb_reserved - COALESCE(sub.ram, 0)),
                            vdisk_mb_reserved = GREATEST(0, s.vdisk_mb_reserved - COALESCE(sub.disk, 0))
                        FROM (
                            SELECT n.servidor_id, SUM(f.vcpu) as vcpu, SUM(f.vram_mb) as ram, SUM(f.vdisk_mb) as disk
                            FROM slices.slice_nodo n
                            JOIN slices.flavor f ON n.flavor_id = f.id
                            WHERE n.slice_id = $1 AND n.servidor_id IS NOT NULL
                            GROUP BY n.servidor_id
                        ) sub
                        WHERE s.id = sub.servidor_id
                        """,
                        slice_id
                    )

                admin_codigo = await conn.fetchval("SELECT codigo FROM auth.usuario WHERE id = $1", admin_id) or f"Admin#{admin_id}"
                await conn.execute(
                    """
                    INSERT INTO auth.auditoria_eventos (
                        usuario_id, codigo_usuario, tipo_evento, severidad,
                        entidad, entidad_id, descripcion, ip_origen
                    )
                    VALUES ($1, $2, 'ADMIN_INTERVENTION', 'WARNING', 'SLICE', $3, $4, '127.0.0.1')
                    """,
                    admin_id, admin_codigo, str(slice_id),
                    f"Intervención administrativa sobre Slice #{slice_id} ('{row['nombre']}'): Acción [{accion.upper()}] — Motivo: {motivo}"
                )

                return {
                    "ok": True,
                    "slice_id": slice_id,
                    "accion": accion.upper(),
                    "nuevo_estado": nuevo_estado,
                    "motivo": motivo,
                    "admin_id": admin_id
                }

    async def toggle_zone(self, zone_id: int) -> Dict[str, Any]:
        async with self.pool.acquire() as conn:
            current = await conn.fetchval("SELECT estado FROM slices.zona_disponibilidad WHERE id = $1", zone_id)
            if not current:
                return {"ok": False, "error": "Zona no encontrada"}
            nuevo = "DISABLED" if current == "ACTIVE" else "ACTIVE"
            await conn.execute("UPDATE slices.zona_disponibilidad SET estado = $1 WHERE id = $2", nuevo, zone_id)
            try:
                await self.record_audit_event(
                    tipo_evento="CATALOG_MODIFIED",
                    severidad="INFO",
                    descripcion=f"Zona de disponibilidad #{zone_id} cambiada a estado [{nuevo}]",
                    entidad="ZONA",
                    entidad_id=str(zone_id)
                )
            except Exception:
                pass
            return {"ok": True, "zone_id": zone_id, "estado": nuevo}

    async def toggle_flavor(self, flavor_id: int) -> Dict[str, Any]:
        async with self.pool.acquire() as conn:
            current = await conn.fetchval("SELECT activo FROM slices.flavor WHERE id = $1", flavor_id)
            if current is None:
                return {"ok": False, "error": "Flavor no encontrado"}
            nuevo = not current
            await conn.execute("UPDATE slices.flavor SET activo = $1 WHERE id = $2", nuevo, flavor_id)
            try:
                await self.record_audit_event(
                    tipo_evento="CATALOG_MODIFIED",
                    severidad="INFO",
                    descripcion=f"Sabor (Flavor) #{flavor_id} cambiado a [{ 'ACTIVO' if nuevo else 'INACTIVO' }]",
                    entidad="FLAVOR",
                    entidad_id=str(flavor_id)
                )
            except Exception:
                pass
            return {"ok": True, "flavor_id": flavor_id, "activo": nuevo}

    # -------------------------------------------------------------------------
    # Auditoría, Seguridad y Detección de Consumo Indebido
    # -------------------------------------------------------------------------

    async def list_audit_logs(
        self,
        limit: int = 100,
        tipo_evento: Optional[str] = None,
        severidad: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[AuditEvent]:
        conditions = []
        params = []
        idx = 1

        if tipo_evento and tipo_evento.upper() != "ALL":
            conditions.append(f"tipo_evento = ${idx}")
            params.append(tipo_evento)
            idx += 1

        if severidad and severidad.upper() != "ALL":
            conditions.append(f"severidad = ${idx}")
            params.append(severidad.upper())
            idx += 1

        if search and search.strip():
            conditions.append(f"(codigo_usuario ILIKE ${idx} OR descripcion ILIKE ${idx} OR entidad_id ILIKE ${idx})")
            params.append(f"%{search.strip()}%")
            idx += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT id, timestamp, usuario_id, codigo_usuario, tipo_evento, severidad,
                   entidad, entidad_id, descripcion, ip_origen, metadata
            FROM auth.auditoria_eventos
            {where_clause}
            ORDER BY timestamp DESC, id DESC
            LIMIT ${idx}
        """
        params.append(limit)

        rows = await self.pool.fetch(query, *params)
        results = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("metadata"), str):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except Exception:
                    d["metadata"] = {}
            results.append(AuditEvent(**d))
        return results

    async def record_audit_event(
        self,
        tipo_evento: str,
        severidad: str,
        descripcion: str,
        usuario_id: Optional[int] = None,
        codigo_usuario: Optional[str] = None,
        entidad: Optional[str] = None,
        entidad_id: Optional[str] = None,
        ip_origen: str = "127.0.0.1",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        query = """
            INSERT INTO auth.auditoria_eventos (
                usuario_id, codigo_usuario, tipo_evento, severidad,
                entidad, entidad_id, descripcion, ip_origen, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            RETURNING id
        """
        meta_json = json.dumps(metadata) if metadata else "{}"
        return await self.pool.fetchval(
            query, usuario_id, codigo_usuario, tipo_evento, severidad,
            entidad, entidad_id, descripcion, ip_origen, meta_json
        )

    async def purge_slice_security(
        self,
        slice_id: int,
        motivo: str,
        admin_id: int
    ) -> Dict[str, Any]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                slice_info = await conn.fetchrow(
                    """
                    SELECT s.id, s.nombre, s.usuario_id, u.codigo as usuario_codigo, s.estado
                    FROM slices.slice s
                    JOIN auth.usuario u ON s.usuario_id = u.id
                    WHERE s.id = $1
                    """,
                    slice_id
                )
                if not slice_info:
                    return {"ok": False, "error": f"Slice #{slice_id} no encontrado."}

                admin_codigo = await conn.fetchval("SELECT codigo FROM auth.usuario WHERE id = $1", admin_id) or f"Admin#{admin_id}"

                # 1. Marcar Slice como ELIMINATED
                await conn.execute("UPDATE slices.slice SET estado = 'ELIMINATED', fecha_modificacion = NOW() WHERE id = $1", slice_id)

                # 2. Marcar nodos como TERMINATED
                await conn.execute("UPDATE slices.slice_nodo SET estado_nodo = 'TERMINATED' WHERE slice_id = $1", slice_id)

                # 3. Liberar reservas en servidores físicos
                await conn.execute(
                    """
                    UPDATE slices.servidor s
                    SET vcpu_reserved = GREATEST(0, s.vcpu_reserved - COALESCE(sub.vcpu, 0)),
                        vram_mb_reserved = GREATEST(0, s.vram_mb_reserved - COALESCE(sub.ram, 0)),
                        vdisk_mb_reserved = GREATEST(0, s.vdisk_mb_reserved - COALESCE(sub.disk, 0))
                    FROM (
                        SELECT n.servidor_id, SUM(f.vcpu) as vcpu, SUM(f.vram_mb) as ram, SUM(f.vdisk_mb) as disk
                        FROM slices.slice_nodo n
                        JOIN slices.flavor f ON n.flavor_id = f.id
                        WHERE n.slice_id = $1 AND n.servidor_id IS NOT NULL
                        GROUP BY n.servidor_id
                    ) sub
                    WHERE s.id = sub.servidor_id
                    """,
                    slice_id
                )

                # 4. Cancelar despliegues activos en slices.deployment
                await conn.execute(
                    """
                    UPDATE slices.deployment
                    SET estado = 'COMPENSATED', finished_at = NOW(),
                        error = jsonb_build_object('motivo', $2, 'accion', 'SECURITY_PURGE')
                    WHERE slice_id = $1 AND estado IN ('PENDING', 'IN_PROGRESS')
                    """,
                    slice_id, motivo
                )

                # 5. Registrar evento de auditoría CRÍTICO de purga
                audit_desc = (
                    f"PURGA DE SEGURIDAD EJECUTADA: Slice #{slice_id} ('{slice_info['nombre']}') del usuario "
                    f"[{slice_info['usuario_codigo']}] fue ELIMINADO FORZOSAMENTE por el administrador [{admin_codigo}]. "
                    f"Recursos liberados inmediatamente. Justificación: {motivo}"
                )
                await conn.execute(
                    """
                    INSERT INTO auth.auditoria_eventos (
                        usuario_id, codigo_usuario, tipo_evento, severidad,
                        entidad, entidad_id, descripcion, ip_origen, metadata
                    )
                    VALUES ($1, $2, 'SECURITY_PURGE', 'CRITICAL', 'SLICE', $3, $4, '127.0.0.1', $5::jsonb)
                    """,
                    admin_id, admin_codigo, str(slice_id), audit_desc,
                    json.dumps({
                        "slice_id": slice_id,
                        "slice_nombre": slice_info["nombre"],
                        "propietario_id": slice_info["usuario_id"],
                        "propietario_codigo": slice_info["usuario_codigo"],
                        "motivo_seguridad": motivo
                    })
                )

                return {
                    "ok": True,
                    "slice_id": slice_id,
                    "slice_nombre": slice_info["nombre"],
                    "propietario": slice_info["usuario_codigo"],
                    "accion": "SECURITY_PURGE",
                    "estado_anterior": slice_info["estado"],
                    "nuevo_estado": "ELIMINATED",
                    "motivo": motivo
                }

    async def detect_security_anomalies(self) -> List[Dict[str, Any]]:
        query = """
            SELECT s.id as slice_id, s.nombre as slice_nombre, s.usuario_id, u.codigo as usuario_codigo,
                   u.estado as usuario_estado, s.estado as slice_estado,
                   COALESCE(SUM(f.vcpu), 0) as vcpu_usado,
                   COALESCE(SUM(f.vram_mb), 0) as ram_usada,
                   COALESCE(SUM(f.vdisk_mb), 0) as disk_usado,
                   q_vcpu.prometido as quota_vcpu,
                   q_ram.prometido as quota_ram,
                   q_disk.prometido as quota_disk
            FROM slices.slice s
            JOIN auth.usuario u ON s.usuario_id = u.id
            LEFT JOIN slices.slice_nodo n ON s.id = n.slice_id AND n.estado_nodo NOT IN ('TERMINATED', 'ERROR')
            LEFT JOIN slices.flavor f ON n.flavor_id = f.id
            LEFT JOIN auth.cuota q_vcpu ON u.nivel_id = q_vcpu.nivel_id AND q_vcpu.recurso = 'vcpu'
            LEFT JOIN auth.cuota q_ram ON u.nivel_id = q_ram.nivel_id AND q_ram.recurso = 'ram_mb'
            LEFT JOIN auth.cuota q_disk ON u.nivel_id = q_disk.nivel_id AND q_disk.recurso = 'disk_mb'
            WHERE s.estado IN ('RUNNING', 'UPDATING')
            GROUP BY s.id, s.nombre, s.usuario_id, u.codigo, u.estado, s.estado,
                     q_vcpu.prometido, q_ram.prometido, q_disk.prometido
        """
        rows = await self.pool.fetch(query)
        anomalies = []
        now = datetime.now()

        for r in rows:
            # 1. Chequeo de usuario inactivo / bloqueado con slices activos
            if r["usuario_estado"] in {"BLOCKED", "INACTIVE"}:
                anomalies.append({
                    "slice_id": r["slice_id"],
                    "slice_nombre": r["slice_nombre"],
                    "usuario_id": r["usuario_id"],
                    "usuario_codigo": r["usuario_codigo"],
                    "tipo_anomalia": "CUENTA_BLOQUEADA_CON_SLICES",
                    "severidad": "CRITICAL",
                    "detalle": f"El slice se encuentra en ejecución activa pero la cuenta propietaria [{r['usuario_codigo']}] está en estado {r['usuario_estado']}.",
                    "fecha_deteccion": now
                })

            # 2. Chequeo de exceso de cuota por slice individual
            if r["quota_vcpu"] and r["vcpu_usado"] > r["quota_vcpu"]:
                anomalies.append({
                    "slice_id": r["slice_id"],
                    "slice_nombre": r["slice_nombre"],
                    "usuario_id": r["usuario_id"],
                    "usuario_codigo": r["usuario_codigo"],
                    "tipo_anomalia": "EXCESO_CUOTA_VCPU",
                    "severidad": "CRITICAL",
                    "detalle": f"Consumo indebido de vCPUs ({r['vcpu_usado']} cores) superando el límite asignado por nivel ({r['quota_vcpu']} cores).",
                    "fecha_deteccion": now
                })

            if r["quota_ram"] and r["ram_usada"] > r["quota_ram"]:
                anomalies.append({
                    "slice_id": r["slice_id"],
                    "slice_nombre": r["slice_nombre"],
                    "usuario_id": r["usuario_id"],
                    "usuario_codigo": r["usuario_codigo"],
                    "tipo_anomalia": "EXCESO_CUOTA_RAM",
                    "severidad": "CRITICAL",
                    "detalle": f"Consumo indebido de Memoria RAM ({r['ram_usada']} MB) superando el límite de cuota ({r['quota_ram']} MB).",
                    "fecha_deteccion": now
                })

        return anomalies


