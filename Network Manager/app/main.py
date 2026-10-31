"""
main.py — Servicio NetworkManager.
La asignación de red la hace el worker de Temporal (temporal_worker.py); aquí solo queda el health check.
"""
from fastapi import FastAPI

from app.config import config

app = FastAPI(title=f"{config.service_name} API", version="2.0.0")


@app.get("/health", tags=["Salud del Sistema"])
async def health_check():
    return {"status": "healthy", "service": config.service_name}
