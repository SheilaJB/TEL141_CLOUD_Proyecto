from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
import hashlib
import json
import os
import re
import shutil
import threading


app = FastAPI(title="VM Image Service")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "Storage")
CATALOG_FILE = os.path.join(STORAGE_DIR, "catalog.json")
os.makedirs(STORAGE_DIR, exist_ok=True)

ALLOWED_FORMATS = {"qcow2", "raw"}
ID_REGEX = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_lock = threading.Lock()

# Catálogo de imágenes en storage
DEFAULT_IMAGES = {
    "cirros": {
        "id": "cirros",
        "filename": "cirros-0.6.2-x86_64-disk.img",
        "format": "qcow2",
        "min_ram_mb": 256,
        "min_disk_gb": 1,
    },
    "ubuntu-20.04": {
        "id": "ubuntu-20.04",
        "filename": "focal-server-cloudimg-amd64.img",
        "format": "qcow2",
        "min_ram_mb": 1024,
        "min_disk_gb": 5,
    },
}


# para persistencia uu
def _save_catalog(catalog: dict) -> None:
    tmp = CATALOG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(catalog, f, indent=2)
    os.replace(tmp, CATALOG_FILE)
 
 
def _load_catalog() -> dict:
    if os.path.exists(CATALOG_FILE):
        with open(CATALOG_FILE, "r") as f:
            return json.load(f)
    _save_catalog(DEFAULT_IMAGES)
    return dict(DEFAULT_IMAGES)
 
 
images_db = _load_catalog()
 
 
def _file_path(image: dict) -> str:
    return os.path.join(STORAGE_DIR, image["filename"])
 
 
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
 
 
def _get_or_404(image_id: str) -> dict:
    if image_id not in images_db:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    return images_db[image_id]
 
 
def _ensure_sha(image: dict) -> str:
    # Un sha256 y lo guarda en el catálogo
    path = _file_path(image)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"El archivo físico {path} no existe en storage")
    with _lock:
        if not image.get("sha256"):
            image["sha256"] = _sha256(path)
            _save_catalog(images_db)
    return image["sha256"]
 



# ENDPOINTS 
@app.get("/health")
def health():
    return {"status": "ok", "service": "Image Manager"}


@app.get("/images")
def list_images():
    # Catálogo Y COMPRUEBA si el archivo existe
    return [
        {**img, "available": os.path.exists(_file_path(img))}
        for img in images_db.values()
    ]


@app.get("/images/{image_id}")
def get_image_info(image_id: str):
    # Metadatos (para validar recursos mínimos)
    image = _get_or_404(image_id)
    _ensure_sha(image)
    return image


@app.get("/images/{image_id}/download")
def download_image(image_id: str):
    # Los Workers 1, 2 y 3 descargan la imagen base desde Server 4
    image = _get_or_404(image_id)
    sha = _ensure_sha(image)
    return FileResponse(
        _file_path(image),
        media_type="application/octet-stream",
        filename=image["filename"],
        headers={"X-Checksum-SHA256": sha},
    )

@app.post("/images/upload", status_code=201)
def upload_image(
    image_id: str,
    img_format: str,
    min_ram_mb: int,
    min_disk_gb: int,
    file: UploadFile = File(...),
):
    # Validaciones
    if not ID_REGEX.match(image_id):
        raise HTTPException(400, "image_id inválido (use letras, números, '.', '_' o '-')")
    if img_format not in ALLOWED_FORMATS:
        raise HTTPException(400, f"Formato no soportado. Use: {sorted(ALLOWED_FORMATS)}")
    if min_ram_mb <= 0 or min_disk_gb <= 0:
        raise HTTPException(400, "min_ram_mb y min_disk_gb deben ser > 0")
 
    # Anti path traversal
    filename = os.path.basename(file.filename or "")
    if not filename:
        raise HTTPException(400, "Nombre de archivo inválido")
 
    with _lock:
        if image_id in images_db:
            raise HTTPException(409, f"Ya existe una imagen con id '{image_id}'")
        if any(img["filename"] == filename for img in images_db.values()):
            raise HTTPException(409, f"Ya existe una imagen con el archivo '{filename}'")
 
    final_path = os.path.join(STORAGE_DIR, filename)
    tmp_path = final_path + ".part"
 
    try:
        with open(tmp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer, length=1024 * 1024)
        os.replace(tmp_path, final_path)  # atómico
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise HTTPException(500, f"Error guardando la imagen: {e}")
 
    with _lock:
        images_db[image_id] = {
            "id": image_id,
            "filename": filename,
            "format": img_format,
            "min_ram_mb": min_ram_mb,
            "min_disk_gb": min_disk_gb,
            "sha256": _sha256(final_path),
        }
        _save_catalog(images_db)
 
    return {"status": "SUCCESS", "image_id": image_id, "sha256": images_db[image_id]["sha256"]}
 
 
@app.delete("/images/{image_id}")
def delete_image(image_id: str):
    image = _get_or_404(image_id)
    path = _file_path(image)
 
    with _lock:
        del images_db[image_id]
        _save_catalog(images_db)
        # Solo borrar el archivo
        still_used = any(img["filename"] == image["filename"] for img in images_db.values())
        if not still_used and os.path.exists(path):
            os.remove(path)
 
    return {"status": "DELETED", "image_id": image_id}
