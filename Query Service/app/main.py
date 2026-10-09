from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router as cruds_router
from app.db import close_db_pool, init_db_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db_pool()
    yield
    await close_db_pool()


app = FastAPI(
    title="Query Service - Read-Only Data Engine",
    description="Servicio de consultas optimizadas para catálogo, cuotas, topologías y métricas",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cruds_router, prefix="/cruds")
app.include_router(cruds_router, prefix="")


@app.get("/health", tags=["Monitoreo"])
async def health():
    return {"status": "ok", "service": "query-service"}
