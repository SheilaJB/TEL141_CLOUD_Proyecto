from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db import get_db_pool
from app.dependencies import get_user_identity
from app.repository import QueryRepository
from app.schemas import (
    ClusterMetrics,
    DeploymentSummary,
    FlavorResponse,
    ImageResponse,
    RequestSummary,
    ServerDetail,
    SliceSummary,
    SliceTopology,
    TemplateResponse,
    UserAdminView,
    UserIdentity,
    UserQuotaSummary,
    ZoneResponse,
    AdminQuotaItem,
    UpdateQuotaRequest,
    SliceInterventionRequest,
    AuditEvent,
    SecurityPurgeRequest,
    SecurityAnomaly,
)

router = APIRouter(tags=["Consultas de Estado y Catálogo"])


async def get_repo() -> QueryRepository:
    pool = await get_db_pool()
    return QueryRepository(pool)


# -----------------------------------------------------------------------------
# Catálogo común (Flavors, Imágenes, Zonas, Plantillas)
# -----------------------------------------------------------------------------

@router.get("/flavors", response_model=List[FlavorResponse])
async def list_flavors(
    active_only: bool = Query(True, description="Filtrar solo flavors activos"),
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    return await repo.list_flavors(active_only=active_only)


@router.get("/images", response_model=List[ImageResponse])
async def list_images(
    active_only: bool = Query(True, description="Filtrar solo imágenes activas"),
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    return await repo.list_images(active_only=active_only)


@router.get("/zones", response_model=List[ZoneResponse])
async def list_zones(
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    is_admin = identity.role in {"admin", "operador"}
    return await repo.list_zones(
        service_level=identity.service_level,
        is_admin=is_admin,
    )


@router.get("/templates", response_model=List[TemplateResponse])
async def list_templates(
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    user_filter = identity.user_id if identity.role == "consumidor" else None
    return await repo.list_templates(user_id=user_filter)


# -----------------------------------------------------------------------------
# Cuotas de usuario
# -----------------------------------------------------------------------------

@router.get("/quota/me", response_model=UserQuotaSummary)
async def get_my_quota(
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    return await repo.get_user_quota(user_id=identity.user_id)


@router.get("/quota/{user_id}", response_model=UserQuotaSummary)
async def get_user_quota_by_id(
    user_id: int,
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    if identity.role not in {"admin", "operador"} and identity.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No autorizado para consultar la cuota de otro usuario.",
        )
    return await repo.get_user_quota(user_id=user_id)


# -----------------------------------------------------------------------------
# Consultas de Slices y Topología
# -----------------------------------------------------------------------------

@router.get("/slices", response_model=List[SliceSummary])
async def list_slices(
    estado: Optional[str] = Query(None, description="Filtrar por estado del slice"),
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    user_filter = identity.user_id if identity.role == "consumidor" else None
    return await repo.list_slices(user_id=user_filter, estado=estado)


@router.get("/slices/{slice_id}", response_model=SliceSummary)
async def get_slice(
    slice_id: int,
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    user_filter = identity.user_id if identity.role == "consumidor" else None
    slice_item = await repo.get_slice_detail(slice_id=slice_id, user_id=user_filter)
    if not slice_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Slice con ID {slice_id} no encontrado o no accesible.",
        )
    return slice_item


@router.get("/slices/{slice_id}/topology", response_model=SliceTopology)
async def get_slice_topology(
    slice_id: int,
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    user_filter = identity.user_id if identity.role == "consumidor" else None
    topology = await repo.get_slice_topology(slice_id=slice_id, user_id=user_filter)
    if not topology:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topología del Slice {slice_id} no encontrada o no accesible.",
        )
    return topology


# -----------------------------------------------------------------------------
# Consultas de Solicitudes de Aprobación y Despliegues
# -----------------------------------------------------------------------------

@router.get("/requests", response_model=List[RequestSummary])
async def list_requests(
    estado: Optional[str] = Query(None, description="Filtrar por estado (PENDING, APPROVED, etc.)"),
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    user_filter = identity.user_id if identity.role == "consumidor" else None
    return await repo.list_requests(user_id=user_filter, estado=estado)


@router.get("/deployments", response_model=List[DeploymentSummary])
async def list_deployments(
    slice_id: Optional[int] = Query(None, description="Filtrar por slice ID"),
    estado: Optional[str] = Query(None, description="Filtrar por estado del despliegue"),
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    user_filter = identity.user_id if identity.role == "consumidor" else None
    return await repo.list_deployments(
        user_id=user_filter,
        slice_id=slice_id,
        estado=estado,
    )


@router.get("/deployments/{deployment_id}", response_model=DeploymentSummary)
async def get_deployment(
    deployment_id: int,
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    user_filter = identity.user_id if identity.role == "consumidor" else None
    item = await repo.get_deployment_detail(
        deployment_id=deployment_id,
        user_id=user_filter,
    )
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Despliegue con ID {deployment_id} no encontrado o no accesible.",
        )
    return item


# -----------------------------------------------------------------------------
# Vistas exclusivas de Operador y Administrador
# -----------------------------------------------------------------------------

@router.get("/servers", response_model=List[ServerDetail])
async def list_servers(
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    if identity.role not in {"admin", "operador"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido a operadores o administradores.",
        )
    return await repo.list_servers()


@router.get("/users", response_model=List[UserAdminView])
async def list_users(
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    if identity.role not in {"admin", "operador"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido a operadores o administradores.",
        )
    return await repo.list_users()


@router.get("/metrics/overview", response_model=ClusterMetrics)
async def get_cluster_metrics(
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    if identity.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso exclusivo para rol administrador.",
        )
    return await repo.get_cluster_metrics()


# -----------------------------------------------------------------------------
# Endpoints Administrativos Exclusivos (Políticas, Cuotas, Zonas, Intervención)
# -----------------------------------------------------------------------------

@router.get("/admin/quotas", response_model=List[AdminQuotaItem])
async def get_admin_quotas(
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    if identity.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso exclusivo para rol administrador.")
    return await repo.list_admin_quotas()


@router.put("/admin/quotas")
async def update_admin_quota(
    body: UpdateQuotaRequest,
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    if identity.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso exclusivo para rol administrador.")
    ok = await repo.update_admin_quota(body.nivel_id, body.recurso, body.prometido)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cuota no encontrada para el nivel y recurso.")
    return {"ok": True, "nivel_id": body.nivel_id, "recurso": body.recurso, "prometido": body.prometido}


@router.post("/admin/slices/{slice_id}/intervene")
async def admin_intervene_slice(
    slice_id: int,
    body: SliceInterventionRequest,
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    if identity.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo administradores pueden intervenir slices.")
    if not body.motivo or len(body.motivo.strip()) < 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe proporcionar un motivo administrativo justificado y documentado para la intervención."
        )
    if body.accion.upper() not in {"STOP", "DELETE"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Acción inválida. Use STOP o DELETE.")

    res = await repo.intervene_slice(slice_id, body.accion, body.motivo.strip(), identity.user_id)
    if not res.get("ok"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=res.get("error", "Error en intervención."))
    return res


@router.patch("/admin/zones/{zone_id}/toggle")
async def admin_toggle_zone(
    zone_id: int,
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    if identity.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso exclusivo para rol administrador.")
    res = await repo.toggle_zone(zone_id)
    if not res.get("ok"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=res.get("error"))
    return res


@router.patch("/admin/flavors/{flavor_id}/toggle")
async def admin_toggle_flavor(
    flavor_id: int,
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    if identity.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso exclusivo para rol administrador.")
    res = await repo.toggle_flavor(flavor_id)
    if not res.get("ok"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=res.get("error"))
    return res


# -----------------------------------------------------------------------------
# Endpoints de Auditoría, Logs y Seguridad de Plataforma
# -----------------------------------------------------------------------------

@router.get("/admin/audit-logs", response_model=List[AuditEvent])
async def list_audit_logs(
    limit: int = Query(100, ge=1, le=500),
    tipo_evento: Optional[str] = Query(None, description="Filtrar por tipo de evento"),
    severidad: Optional[str] = Query(None, description="Filtrar por severidad: INFO, WARNING, CRITICAL"),
    search: Optional[str] = Query(None, description="Búsqueda por usuario o descripción"),
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    if identity.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso exclusivo para rol administrador.")
    return await repo.list_audit_logs(limit=limit, tipo_evento=tipo_evento, severidad=severidad, search=search)


@router.get("/admin/security/anomalies", response_model=List[SecurityAnomaly])
async def detect_security_anomalies(
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    if identity.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso exclusivo para rol administrador.")
    return await repo.detect_security_anomalies()


@router.post("/admin/security/purge")
async def admin_security_purge(
    body: SecurityPurgeRequest,
    repo: QueryRepository = Depends(get_repo),
    identity: UserIdentity = Depends(get_user_identity),
):
    if identity.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo administradores pueden ejecutar purgas de seguridad.")
    if not body.motivo or len(body.motivo.strip()) < 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe proporcionar un motivo de seguridad documentado para justificar la purga."
        )

    res = await repo.purge_slice_security(body.slice_id, body.motivo.strip(), identity.user_id)
    if not res.get("ok"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=res.get("error", "Error en purga."))
    return res

