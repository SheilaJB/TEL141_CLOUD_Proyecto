"""
router.py — API del ciclo de vida de VMs (la llama el Slice Manager; no pasa por el Gateway).

  GET    /vms                       lista (filtros: slice_id, estado)
  GET    /vms/{nodo_id}             estado de la VM (?verify=true consulta al worker)
  POST   /vms/{nodo_id}/create      PENDING/FAILED -> RUNNING
  POST   /vms/{nodo_id}/start       STOPPED -> RUNNING
  POST   /vms/{nodo_id}/stop        RUNNING -> STOPPED
  DELETE /vms/{nodo_id}             -> DELETED (borrado inteligente)

El worker sale de slices.reserva_nodo (VM Placement). Mientras no haya reservas, start/stop/delete
aceptan `worker_ip` (body en POST, query param en DELETE) para indicar dónde está la VM.
"""
import uuid
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.vms import service

router = APIRouter(prefix="/vms", tags=["vms"])


class CreateBody(BaseModel):
    worker_ip: Optional[str] = None   # si no viene, se toma de slices.reserva_nodo (VM Placement)
    seed_iso: Optional[str] = None    # ISO de cloud-init en el worker (para imágenes Ubuntu cloud)


class StartBody(BaseModel):
    worker_ip: Optional[str] = None   # solo si el nodo no tiene reserva de Placement
    seed_iso: Optional[str] = None


class StopBody(BaseModel):
    worker_ip: Optional[str] = None   # solo si el nodo no tiene reserva de Placement


@router.get("")
def list_vms(slice_id: Optional[int] = None, estado: Optional[str] = None):
    return service.list_vms(slice_id, estado)


@router.get("/{nodo_id}")
def get_vm(nodo_id: uuid.UUID, verify: bool = False):
    return service.get_vm(str(nodo_id), verify)


@router.post("/{nodo_id}/create")
def create_vm(nodo_id: uuid.UUID, body: Optional[CreateBody] = None):
    body = body or CreateBody()
    return service.create_vm(str(nodo_id), body.worker_ip, body.seed_iso)


@router.post("/{nodo_id}/start")
def start_vm(nodo_id: uuid.UUID, body: Optional[StartBody] = None):
    body = body or StartBody()
    return service.start_vm(str(nodo_id), body.seed_iso, body.worker_ip)


@router.post("/{nodo_id}/stop")
def stop_vm(nodo_id: uuid.UUID, body: Optional[StopBody] = None):
    return service.stop_vm(str(nodo_id), body.worker_ip if body else None)


@router.delete("/{nodo_id}")
def delete_vm(nodo_id: uuid.UUID, worker_ip: Optional[str] = None):
    return service.delete_vm(str(nodo_id), worker_ip)