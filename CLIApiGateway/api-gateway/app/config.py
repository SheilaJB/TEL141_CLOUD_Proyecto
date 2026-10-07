"""
config.py — Configuración de Enrutamiento del API Gateway

Define los destinos (URLs internas) para cada servicio de la plataforma.
"""
import os

# Mapeo de prefijos de URL a los microservicios correspondientes en la red interna
ROUTE_MAP = {
    # Módulo Auth
    "auth": os.getenv("AUTH_URL", "http://auth:8000/auth"),
    
    # Módulos Query Service
    "cruds": os.getenv("CRUDS_URL", "http://cruds-service:8000/cruds"),
    
    # Módulos Slice Manager
    "slices": os.getenv("SLICE_MANAGER_URL", "http://slicemanager:8000/slices"),
    
    # Otros módulos (Imagen / Netwkoring - En proceso)
    "image": os.getenv("IMAGE_MANAGER_URL", "http://imagemanager:8000/image"),
    "network": os.getenv("NETWORK_MANAGER_URL", "http://networkmanager:8000/network")
}

# Límite de seguridad: Tamaño máximo de peticiones JSON en memoria (2MB). 
# Todo lo que exceda esto o sea multipart (ej. subida de ISOs) usará Streaming directo.
MAX_BODY_SIZE = 2 * 1024 * 1024  
