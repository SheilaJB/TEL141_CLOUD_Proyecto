"""
schemas.py — Modelos Pydantic para el Network Manager.
Define los contratos de entrada y salida para:
1. Cálculo matemático de topología
2. Asignación y reserva de recursos de red (VLANs, IPs, MACs).
3. Generación de comandos OVS e iptables.
4. Contrato de acciones del Adaptador (CREATE_LINK, ATTACH_PORT, SET_PUBLIC).
"""
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any, Union

# 1. ESQUEMAS PARA CÁLCULO TOPOLÓGICO 
class NodeRequest(BaseModel):
    vm_id: Union[str, int] = Field(..., description="Identificador único de la VM")
    worker_id: Optional[Union[str, int]] = Field(None, description="ID del servidor físico donde reside")

class LinkRequest(BaseModel):
    source_vm: Union[str, int] = Field(..., description="VM origen del enlace")
    target_vm: Union[str, int] = Field(..., description="VM destino del enlace")
    link_name: Optional[str] = Field(None, description="Nombre descriptivo del enlace")

class NetworkDraftRequest(BaseModel):
    slice_id: int = Field(..., description="ID global del Slice")
    nodes: List[NodeRequest] = Field(..., description="Lista de VMs que componen el slice")
    links: List[LinkRequest] = Field(..., description="Lista de enlaces virtuales entre VMs")

class LinkResponse(BaseModel):
    source_vm: Union[str, int]
    target_vm: Union[str, int]
    link_name: Optional[str] = None
    vlan_inner: int = Field(..., description="VLAN interna (C-TAG) asignada para el enlace")
    is_remote: bool = Field(..., description="True si las VMs residen en distintos servidores físicos")
    is_internet: bool = Field(False, description="True si el enlace conecta a la red pública")
    subnet_cidr: Optional[str] = Field(None, description="Subred asignada al enlace (ej. 192.168.1.0/30)")

class NetworkPlanResponse(BaseModel):
    slice_id: int
    topology_type: str = Field(..., description="Tipo detectado: Lineal, Malla, Árbol, Anillo, Bus, etc.")
    is_connected: bool = Field(..., description="Indica si todas las VMs están interconectadas")
    node_count: int
    edge_count: int
    links_configured: List[LinkResponse]
    message: str = "Plan de red calculado exitosamente cumpliendo R1B."

# Compatibilidad con topology_graph legacy
class LinkInput(BaseModel):
    link_name: str
    vm_a_id: Union[str, int]
    iface_a: str
    vm_b_id: Union[str, int]
    iface_b: str

class LinkAllocationDetail(BaseModel):
    link_name: str
    vm_a_id: Union[str, int]
    iface_a: str
    vm_b_id: Union[str, int]
    iface_b: str
    vlan_inner: int
    is_remote: bool
    is_internet: bool = False
    subnet_cidr: Optional[str] = None

# 2. ESQUEMAS PARA ASIGNACIÓN DE RECURSOS (ALLOCATE / RELEASE)
class AllocateLinkRequest(BaseModel):
    link_name: str
    vm_a_id: Union[str, int]
    iface_a: str
    vm_b_id: Union[str, int]
    iface_b: str

class AllocateRequest(BaseModel):
    slice_id: int
    placement_map: Dict[str, Union[str, int]] = Field(..., description="Mapeo {vm_id: worker_id}")
    links: List[AllocateLinkRequest]

class InterfaceDetail(BaseModel):
    vm_id: Union[str, int]
    interface_name: str
    tap_name: str
    mac_address: str
    ip_address: Optional[str] = None
    bridge_name: Optional[str] = None
    worker_id: Optional[Union[str, int]] = None

class NetworkDetail(BaseModel):
    network_id: Optional[int] = None
    id: Optional[int] = None
    link_name: Optional[str] = None
    vlan_inner: int
    is_remote: bool
    subnet_cidr: Optional[str] = None
    interfaces: List[InterfaceDetail]

class AllocateResponse(BaseModel):
    slice_id: int
    vlan_slice: int = Field(..., description="S-TAG QinQ reservado para el slice en la red física")
    bridge_name: str = Field(..., description="Nombre del puente virtual privado (br-sl-{slice_id})")
    topology_type: Optional[str] = None
    networks: List[NetworkDetail]

class ReleaseRequest(BaseModel):
    slice_id: int

class VlanAvailableResponse(BaseModel):
    total: int
    available: int
    used: int

class SliceNetworkResponse(BaseModel):
    slice_id: int
    vlan_slice: int
    bridge_name: str
    networks: List[NetworkDetail]

# 3. ESQUEMAS PARA COMANDOS OVS / LINUX
class OvsWorkerCommand(BaseModel):
    worker_id: Union[str, int]
    commands: List[str]

class OvsCommandResponse(BaseModel):
    slice_id: int
    vlan_slice: int
    bridge_name: str
    workers: List[OvsWorkerCommand]
    gateway_commands: List[str] = []

# 4. ESQUEMAS PARA ACCIONES ATÓMICAS DEL ADAPTADOR
class ActionTarget(BaseModel):
    kind: str = Field(..., description="'vm', 'link', 'port'")
    id: str
    link_id: Optional[str] = None
    node_id: Optional[str] = None

class AdapterAction(BaseModel):
    id: str
    op: str = Field(..., description="CREATE_LINK, ATTACH_PORT, SET_PUBLIC, DELETE_LINK, etc.")
    executor: Optional[str] = "adapter"
    intent_id: Optional[str] = None
    target: ActionTarget
    params: Dict[str, Any] = Field(default_factory=dict)
    before: Optional[Any] = None
    after: Optional[Any] = None

class AdapterActionInput(BaseModel):
    idempotency_key: str
    deployment_id: int
    slice_id: int
    action: AdapterAction
    server_id: Optional[Union[str, int]] = None

class AdapterActionOutput(BaseModel):
    action_id: str
    operation: str
    already_existed: bool = False
    already_absent: bool = False
    facts: Dict[str, Any] = Field(default_factory=dict)
