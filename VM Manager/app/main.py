from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import os
import re
from typing import List, Optional
import time
from libvirt_driver import KVMRemoteDriver

app = FastAPI(title="VM Manager Service")

IMAGE_SERVICE_URL = os.getenv("IMAGE_SERVICE_URL", "http://10.0.10.4:8001")
ALLOWED_WORKERS = set(
    os.getenv("ALLOWED_WORKERS", "10.0.10.1,10.0.10.2,10.0.10.3").split(",")
)


# Directorios dentro de los Workers
REMOTE_BASE_DIR = "/var/lib/libvirt/images/base"
REMOTE_DISKS_DIR = "/var/lib/libvirt/images/overlays"

MAX_RETRIES = 3
VM_ID_REGEX = re.compile(r"^[A-Za-z0-9._-]{1,48}$")

class NicSpec(BaseModel):
    bridge: str
    vlan: Optional[int] = None 

class CreateVMRequest(BaseModel):
    vm_id: str
    image_id: str
    worker_ip: str  # 10.0.10.1, 10.0.10.2 o 10.0.10.3
    vcpus: int = 1
    memory_ram_mb: int = 1024
    interfaces: List[NicSpec] = []
    seed_iso: Optional[str] = None

def _validate(vm_id: str, worker_ip: str):
    if not VM_ID_REGEX.match(vm_id):
        raise HTTPException(400, "vm_id inválido (use letras, números, '.', '_' o '-')")
    if worker_ip not in ALLOWED_WORKERS:
        raise HTTPException(400, f"worker_ip no permitido. Workers válidos: {sorted(ALLOWED_WORKERS)}")



@app.get("/health")
def health():
    return {"status": "ok", "service": "VM Manager"}



@app.post("/vms/create")
def create_vm(req: CreateVMRequest):
    _validate(req.vm_id, req.worker_ip)
 
    # Metadatos de la imagen desde el Image Manager
    try:
        res = requests.get(f"{IMAGE_SERVICE_URL}/images/{req.image_id}", timeout=15)
    except requests.RequestException as e:
        raise HTTPException(502, f"No se pudo contactar al Image Manager: {e}")
    if res.status_code != 200:
        raise HTTPException(400, "La imagen no existe en el catálogo de VM Image")
 
    img_info = res.json()
    if req.memory_ram_mb < img_info["min_ram_mb"]:
        raise HTTPException(
            400,
            f"RAM insuficiente: la imagen '{req.image_id}' requiere al menos {img_info['min_ram_mb']} MB",
        )
 
    base_image_path = f"{REMOTE_BASE_DIR}/{img_info['filename']}"
    overlay_path = f"{REMOTE_DISKS_DIR}/{req.vm_id}.qcow2"
    download_url = f"{IMAGE_SERVICE_URL}/images/{req.image_id}/download"
 
    driver = KVMRemoteDriver(worker_ip=req.worker_ip)
    last_error = None
 
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"[*] Intento {attempt}/{MAX_RETRIES}: desplegando {req.vm_id} en Worker {req.worker_ip}...")
 
            # Descargar imagen base y crea disco
            driver.prepare_qcow2_overlay(
                image_download_url=download_url,
                base_path=base_image_path,
                overlay_path=overlay_path,
                expected_sha256=img_info.get("sha256"),
            )
 
            # Encender VM y capturar PID + puerto VNC
            vm_data = driver.define_and_start_vm(
                vm_name=req.vm_id,
                memory_mb=req.memory_ram_mb,
                vcpus=req.vcpus,
                disk_path=overlay_path,
                interfaces=[n.dict() for n in req.interfaces],
                seed_iso=req.seed_iso,
            )
 
            return {
                "status": "SUCCESS",
                "vm_id": vm_data["vm_id"],
                "worker": vm_data["worker"],
                "process_id": vm_data["pid"],
                "vnc_port": vm_data["vnc_port"],
                "attempts": attempt,
            }
 
        except Exception as e:
            last_error = str(e)
            print(f"[!] Fallo en intento {attempt}: {last_error}")
            # Dejar el worker limpio antes de reintentar
            driver.cleanup_partial(req.vm_id, overlay_path)
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)
 
    raise HTTPException(
        status_code=500,
        detail=(
            f"Falló el despliegue de {req.vm_id} en {req.worker_ip} "
            f"tras {MAX_RETRIES} intentos. Último error: {last_error}"
        ),
    )



@app.delete("/vms/{worker_ip}/{vm_id}")
def delete_vm(worker_ip: str, vm_id: str):
    # Elimina la VM y aplica borrado + imagen base si nadie más la usa
    _validate(vm_id, worker_ip)
 
    overlay_path = f"{REMOTE_DISKS_DIR}/{vm_id}.qcow2"
    driver = KVMRemoteDriver(worker_ip=worker_ip)
 
    try:
        result = driver.destroy_and_smart_clean(vm_name=vm_id, overlay_path=overlay_path)
        return {"status": "DELETED", "vm_id": vm_id, "worker": worker_ip, **result}
    except Exception as e:
        raise HTTPException(500, f"Error al limpiar la VM en {worker_ip}: {e}")
