"""
store.py — Archivos de imagen en Storage/ + utilidades (sha256, validación qcow2).

El catálogo vive en la tabla slices.image; `ruta_referencia` es el NOMBRE DEL ARCHIVO dentro de Storage/.
"""
import hashlib
import os

from app import config

QCOW2_MAGIC = b"QFI\xfb"


def file_path(image: dict) -> str:
    """Ruta local del archivo (solo el nombre: nunca rutas arbitrarias)."""
    return os.path.join(config.STORAGE_DIR, os.path.basename(image["ruta_referencia"]))


def is_qcow2(path: str) -> bool:
    with open(path, "rb") as f:
        return f.read(4) == QCOW2_MAGIC


def sha256_cached(path: str) -> str:
    """sha256 del archivo; se cachea en <archivo>.sha256 y se recalcula si la imagen cambió."""
    side = path + ".sha256"
    try:
        if os.path.exists(side) and os.path.getmtime(side) >= os.path.getmtime(path):
            with open(side) as f:
                return f.read().strip()
    except OSError:
        pass

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    digest = h.hexdigest()
    try:
        with open(side, "w") as f:
            f.write(digest)
    except OSError:
        pass  # Storage de solo lectura: no pasa nada, se recalcula la próxima vez
    return digest
