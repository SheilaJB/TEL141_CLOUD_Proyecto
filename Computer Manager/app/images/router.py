"""
router.py — Catálogo de imágenes (parte "Image Manager" del Compute Manager).

Rutas bajo /image (el Gateway ya enruta "image" a este servicio).
  GET    /image/                      lista el catálogo (slices.image)
  GET    /image/{id}                  metadatos + sha256
  GET    /image/{id}/download         descarga (la usan los workers) con header X-Checksum-SHA256
  POST   /image/upload                sube una imagen qcow2 y la registra
  DELETE /image/{id}                  baja lógica (estado DELETED) si ningún nodo la usa
"""
import os
import shutil
from typing import Optional

import psycopg
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app import config, db
from app.images import store

router = APIRouter(prefix="/image", tags=["images"])

_COLS = "i.id, i.nombre, i.cluster_compatible, i.ruta_referencia, i.contador_refs, i.estado"


def _get_or_404(conn, image_id: int) -> dict:
    row = conn.execute(f"SELECT {_COLS} FROM slices.image i WHERE i.id = %s", (image_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Imagen no encontrada")
    return row


@router.get("/")
def list_images():
    with db.tx() as conn:
        rows = conn.execute(f"SELECT {_COLS} FROM slices.image i ORDER BY i.id").fetchall()
    for r in rows:
        r["available"] = os.path.exists(store.file_path(r))
    return rows


@router.get("/{image_id}")
def get_image(image_id: int):
    with db.tx() as conn:
        image = _get_or_404(conn, image_id)
    path = store.file_path(image)
    image["available"] = os.path.exists(path)
    image["sha256"] = store.sha256_cached(path) if image["available"] else None
    return image


@router.get("/{image_id}/download")
def download_image(image_id: int):
    with db.tx() as conn:
        image = _get_or_404(conn, image_id)
    path = store.file_path(image)
    if not os.path.exists(path):
        raise HTTPException(404, f"El archivo físico {os.path.basename(path)} no existe en Storage")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=os.path.basename(path),
        headers={"X-Checksum-SHA256": store.sha256_cached(path)},
    )


@router.post("/upload", status_code=201)
def upload_image(nombre: str, file: UploadFile = File(...), cluster_compatible: Optional[int] = None):
    filename = os.path.basename(file.filename or "")           # anti path traversal
    if not filename or filename.startswith("."):
        raise HTTPException(400, "Nombre de archivo inválido")

    os.makedirs(config.STORAGE_DIR, exist_ok=True)
    final_path = os.path.join(config.STORAGE_DIR, filename)
    if os.path.exists(final_path):
        raise HTTPException(409, f"Ya existe un archivo '{filename}' en Storage")

    tmp_path = final_path + ".part"
    try:
        with open(tmp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer, length=1024 * 1024)
        if not store.is_qcow2(tmp_path):
            raise HTTPException(400, "El archivo no es una imagen qcow2 válida")
        os.replace(tmp_path, final_path)                        # atómico
    except HTTPException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise HTTPException(500, f"Error guardando la imagen: {e}")

    try:
        with db.tx() as conn:
            image_id = conn.execute(
                "INSERT INTO slices.image (nombre, cluster_compatible, ruta_referencia) "
                "VALUES (%s, %s, %s) RETURNING id",
                (nombre, cluster_compatible, filename),
            ).fetchone()["id"]
    except psycopg.errors.ForeignKeyViolation:
        os.remove(final_path)
        raise HTTPException(400, "cluster_compatible no existe en slices.cluster")
    except Exception:
        os.remove(final_path)
        raise

    return {"status": "SUCCESS", "image_id": image_id, "sha256": store.sha256_cached(final_path)}


@router.delete("/{image_id}")
def delete_image(image_id: int):
    with db.tx() as conn:
        image = _get_or_404(conn, image_id)
        in_use = conn.execute(
            "SELECT count(*) AS n FROM slices.slice_nodo WHERE imagen_id = %s AND estado_nodo <> 'DELETED'",
            (image_id,),
        ).fetchone()["n"]
        if in_use:
            raise HTTPException(409, f"La imagen la usan {in_use} nodo(s); no se puede eliminar")
        conn.execute("UPDATE slices.image SET estado = 'DELETED' WHERE id = %s", (image_id,))
        shared = conn.execute(
            "SELECT count(*) AS n FROM slices.image WHERE ruta_referencia = %s AND estado <> 'DELETED'",
            (image["ruta_referencia"],),
        ).fetchone()["n"]

    removed = False
    path = store.file_path(image)
    if not shared and os.path.exists(path):
        os.remove(path)
        if os.path.exists(path + ".sha256"):
            os.remove(path + ".sha256")
        removed = True
    return {"status": "DELETED", "image_id": image_id, "file_removed": removed}
