from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import os
import time
from libvirt_driver import KVMRemoteDriver

app = FastAPI(title="VM Manager Service")

IMAGE_SERVICE_URL = os.getenv("IMAGE_SERVICE_URL", "http://10.0.10.4:8001")

# Directorios dentro de los Workers
REMOTE_BASE_DIR = "/var/lib/libvirt/images/base"
REMOTE_DISKS_DIR = "/var/lib/libvirt/images/overlays"

class CreateVMRequest(BaseModel):
    vm_id: str
    image_id: str
    worker_ip: str  # 10.0.10.1, 10.0.10.2 o 10.0.10.3
    vcpus: int = 1
    memory_ram_mb: int = 1024

@app.post("/vms/create")
def create_vm(req: CreateVMRequest):
    # Obtener los metadatos de la imagen desde VM Image Service
    res = requests.get(f"{IMAGE_SERVICE_URL}/images/{req.image_id}")
    if res.status_code != 200:
        raise HTTPException(status_code=400, detail="La imagen no existe en el catálogo de VM Image")
    
    img_info = res.json()
    filename = img_info["filename"]
    
    # rutas
    base_image_path = f"{REMOTE_BASE_DIR}/{filename}"
    overlay_path = f"{REMOTE_DISKS_DIR}/{req.vm_id}.qcow2"
    download_url = f"{IMAGE_SERVICE_URL}/images/{req.image_id}/download"

    driver = KVMRemoteDriver(worker_ip=req.worker_ip)

    # Reintentos
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            print(f"[*] Intento {retry_count + 1}: Desplegando {req.vm_id} en Worker {req.worker_ip}...")
            
            # Descargar imagen base si falta y crear disco overlay
            driver.prepare_qcow2_overlay(
                image_download_url=download_url,
                base_path=base_image_path,
                overlay_path=overlay_path
            )
            
            # Encender VM y capturar PID
            vm_data = driver.define_and_start_vm(
                vm_name=req.vm_id,
                memory_mb=req.memory_ram_mb,
                vcpus=req.vcpus,
                disk_path=overlay_path
            )
            
            # Respuesta JSON
            return {
                "status": "SUCCESS",
                "vm_id": vm_data["vm_id"],
                "worker": vm_data["worker"],
                "process_id": vm_data["pid"]
            }

        except Exception as e:
            retry_count += 1
            print(f"[!] Fallo en intento {retry_count}: {str(e)}")
            time.sleep(2)

    raise HTTPException(
        status_code=500, 
        detail=f"Fallo el despliegue de {req.vm_id} en {req.worker_ip} tras {max_retries} reintentos."
    )

@app.delete("/vms/{worker_ip}/{vm_id}")
def delete_vm(worker_ip: str, vm_id: str):
    # Elimina la VM y aplica borrado inteligente sobre el archivo .qcow2 del Worker
    overlay_path = f"{REMOTE_DISKS_DIR}/{vm_id}.qcow2"
    driver = KVMRemoteDriver(worker_ip=worker_ip)
    
    try:
        driver.destroy_and_smart_clean(vm_name=vm_id, overlay_path=overlay_path)
        return {
            "status": "DELETED", 
            "vm_id": vm_id, 
            "worker": worker_ip, 
            "cleaned_disk": overlay_path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al limpiar la VM en {worker_ip}: {str(e)}")