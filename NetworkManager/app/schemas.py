"""
schemas.py — Modelos Pydantic para el Network Manager

Define la estructura de los datos de entrada (el Draft enviado por el Slice Manager)
y la estructura de salida (El plan de red calculado).
"""
from pydantic import BaseModel, Field
from typing import List, Optional

class NodeRequest(BaseModel):
    vm_id: str = Field(..., description="Identificador único de la máquina virtual")
    worker_id: str = Field(..., description="ID del servidor físico donde reside la VM")

class LinkRequest(BaseModel):
    source_vm: str = Field(..., description="VM origen del enlace")
    target_vm: str = Field(..., description="VM destino del enlace")

class NetworkDraftRequest(BaseModel):
    slice_id: int = Field(..., description="ID global del Slice")
    nodes: List[NodeRequest] = Field(..., description="Lista de VMs que componen el slice")
    links: List[LinkRequest] = Field(..., description="Lista de conexiones entre las VMs")

class LinkResponse(BaseModel):
    source_vm: str
    target_vm: str
    vlan_inner: int = Field(..., description="VLAN interna calculada para este enlace P2P")
    is_remote: bool = Field(..., description="True si las VMs están en distintos servidores físicos")

class NetworkPlanResponse(BaseModel):
    slice_id: int
    topology_type: str = Field(..., description="Tipo detectado: Lineal, Malla, Árbol, Anillo, etc.")
    links_configured: List[LinkResponse]
    message: str = "Plan de red calculado exitosamente cumpliendo R1B."
