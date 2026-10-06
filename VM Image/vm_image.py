from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
import os
import shutil

app = FastAPI(title="VM Image Service")

STORAGE_DIR = "./storage"
os.makedirs(STORAGE_DIR, exist_ok=True)

# Catálogo de imágenes físicamente almacenadas en ./storage
images_db = {
    "cirros": {
        "id": "cirros",
        "filename": "cirros-0.6.2-x86_64-disk.img",
        "format": "qcow2",
        "min_ram_mb": 256,
        "min_disk_gb": 1
    },
    "ubuntu-20.04": {
        "id": "ubuntu-20.04",
        "filename": "focal-server-cloudimg-amd64.img",
        "format": "qcow2",
        "min_ram_mb": 1024,
        "min_disk_gb": 5
    }
}

@app.get("/images")
def list_images():
    # Devuelve el catálogo de imágenes 
    return list(images_db.values())

@app.get("/images/{image_id}")
def get_image_info(image_id: str):
    # Devuelve los metadatos de una imagen específica ( Para validar que no se pasen de recursos)
    if image_id not in images_db:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    return images_db[image_id]

@app.get("/images/{image_id}/download")
def download_image(image_id: str):
    # Los Workers 1, 2 y 3 descargan la imagen base desde Server 4.
    if image_id not in images_db:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    
    file_path = os.path.join(STORAGE_DIR, images_db[image_id]["filename"])
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"El archivo físico {file_path} no existe en storage")
    
    return FileResponse(file_path, media_type="application/octet-stream", filename=images_db[image_id]["filename"])

@app.post("/images/upload")
def upload_image(image_id: str, format: str, min_ram_mb: int, min_disk_gb: int, file: UploadFile = File(...)):
    # Sube una nueva imagen de disco al repositorio
    file_path = os.path.join(STORAGE_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    images_db[image_id] = {
        "id": image_id,
        "filename": file.filename,
        "format": format,
        "min_ram_mb": min_ram_mb,
        "min_disk_gb": min_disk_gb
    }
    return {"status": "SUCCESS", "image_id": image_id}