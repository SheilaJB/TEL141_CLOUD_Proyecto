from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from app import db
from app.images.router import router as images_router
from app.vms.router import router as vms_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.open_pool()
    yield
    db.close_pool()


app = FastAPI(
    title="Compute Manager",
    description="Imágenes y ciclo de vida de VMs",
    lifespan=lifespan,
)
app.include_router(images_router)
app.include_router(vms_router)


@app.exception_handler(PoolTimeout)
@app.exception_handler(psycopg.OperationalError)
async def db_unavailable(request, exc):
    return JSONResponse(status_code=503, content={"detail": "Base de datos no disponible"})


@app.get("/health")
def health():
    try:
        with db.tx() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "service": "Compute Manager", "db": True}
    except Exception:
        return {"status": "degraded", "service": "Compute Manager", "db": False}
