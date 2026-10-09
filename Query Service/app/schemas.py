from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class UserIdentity(BaseModel):
    user_id: int
    role: str
    service_level: Optional[str] = None


class FlavorResponse(BaseModel):
    id: int
    name: str
    descripcion: str
    vcpu: int
    vram_mb: int
    vdisk_mb: int
    activo: bool


class ImageResponse(BaseModel):
    id: int
    nombre: str
    cluster_compatible: Optional[int] = None
    ruta_referencia: str
    contador_refs: int
    estado: str


class ZoneResponse(BaseModel):
    id: int
    nombre: str
    cluster_id: Optional[int] = None
    estado: str
    cpu_allocation_ratio_default: float
    cpu_allocation_ratio_limit: float
    ram_allocation_ratio_default: float
    ram_allocation_ratio_limit: float
    disk_allocation_ratio_default: float
    disk_allocation_ratio_limit: float


class QuotaUsage(BaseModel):
    recurso: str
    prometido: float
    consumido_activos: float
    reservado_pendientes: float
    disponible: float


class UserQuotaSummary(BaseModel):
    usuario_id: int
    nivel: Optional[str] = None
    cuotas: List[QuotaUsage]


class SliceSummary(BaseModel):
    id: int
    nombre: str
    estado: str
    zona_id: Optional[int] = None
    zona_nombre: Optional[str] = None
    vlan_s: Optional[int] = None
    version_activa_id: Optional[int] = None
    version_activa_numero: Optional[int] = None
    usuario_id: int
    fecha_creacion: datetime
    fecha_modificacion: datetime


class SliceNodeDetail(BaseModel):
    id: str
    name: str
    flavor_id: int
    flavor_name: Optional[str] = None
    vcpu: Optional[int] = None
    vram_mb: Optional[int] = None
    vdisk_mb: Optional[int] = None
    imagen_id: int
    imagen_nombre: Optional[str] = None
    estado_nodo: str
    pid: Optional[int] = None
    puerto_vnc: Optional[int] = None
    servidor_id: Optional[int] = None
    servidor_ip: Optional[str] = None


class SlicePortDetail(BaseModel):
    id: str
    name: Optional[str] = None
    nodo_id: str
    nodo_name: Optional[str] = None
    public: bool
    mac: Optional[str] = None
    ip: Optional[str] = None
    vlan_tag: Optional[int] = None
    ns: Optional[str] = None


class SliceLinkDetail(BaseModel):
    id: str
    name: str
    puertos: List[SlicePortDetail] = []


class SliceTopology(BaseModel):
    slice_id: int
    nombre: str
    estado: str
    zona_id: Optional[int] = None
    vlan_s: Optional[int] = None
    version_id: Optional[int] = None
    numero_version: Optional[int] = None
    spec: Optional[Dict[str, Any]] = None
    nodos: List[SliceNodeDetail] = []
    enlaces: List[SliceLinkDetail] = []


class RequestSummary(BaseModel):
    id: int
    deployment_id: int
    slice_id: int
    slice_nombre: str
    usuario_id: int
    usuario_codigo: str
    estado: str
    operador_id: Optional[int] = None
    fecha_sol: datetime
    motivo: Optional[str] = None
    tipo_deployment: str


class ServerDetail(BaseModel):
    id: int
    ip_serv: str
    mac_serv: str
    total_cpu_cores: int
    total_ram_mb: int
    total_disk_mb: int
    vcpu_reserved: int
    vram_mb_reserved: int
    vdisk_mb_reserved: int
    zona_id: Optional[int] = None
    zona_nombre: Optional[str] = None
    estado: str
    cpu_usage_pct: float
    ram_usage_pct: float
    disk_usage_pct: float


class UserAdminView(BaseModel):
    id: int
    codigo: str
    rol: str
    nivel: Optional[str] = None
    estado: str
    fecha_creacion: datetime


class ClusterMetrics(BaseModel):
    total_slices: int
    running_slices: int
    draft_slices: int
    total_nodes: int
    running_nodes: int
    pending_approvals: int
    active_servers: int
    total_vcpu_allocated: int
    total_vram_mb_allocated: int
    total_vdisk_mb_allocated: int
