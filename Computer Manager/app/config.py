"""
config.py — Configuración del Compute Manager (todo por variables de entorno).
"""
import os

# --- Base de datos (PostgreSQL cloud_g3, schemas auth y slices) ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/cloud_g3")
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# --- Imágenes ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# OJO: en Linux "Storage" != "storage"
STORAGE_DIR = os.getenv("STORAGE_DIR", os.path.join(BASE_DIR, "Storage"))
# URL con la que los WORKERS descargan imágenes (deben poder llegar al server 4)
PUBLIC_URL = os.getenv("COMPUTE_PUBLIC_URL", "http://10.0.10.4:8001").rstrip("/")

# --- Adaptador Linux (workers) ---
ALLOWED_WORKERS = set(os.getenv("ALLOWED_WORKERS", "10.0.10.1,10.0.10.2,10.0.10.3").split(","))
SSH_USER = os.getenv("WORKER_SSH_USER", "ubuntu")
OVS_BRIDGE = os.getenv("OVS_BRIDGE", "br-int")          # bridge OVS donde se conectan los taps
REMOTE_BASE_DIR = os.getenv("REMOTE_BASE_DIR", "/var/lib/vms/base")
REMOTE_DISKS_DIR = os.getenv("REMOTE_DISKS_DIR", "/var/lib/vms/overlays")

# --- Reintentos de creación ---
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF_S = float(os.getenv("RETRY_BACKOFF_S", "2"))
