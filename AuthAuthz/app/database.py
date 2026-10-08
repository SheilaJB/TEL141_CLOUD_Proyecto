"""
database.py — Conexión Asíncrona a PostgreSQL (asyncpg)
"""
import os
import asyncpg
from typing import AsyncGenerator

RAW_DB_URL = os.getenv("DATABASE_URL", "postgresql://cloud_admin:cloud_admin@postgres:5432/cloud_g3")
# Limpieza de esquemas sqlalchemy para compatibilidad directa con asyncpg
DB_URL = RAW_DB_URL.replace("postgresql+psycopg://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")

_pool = None

async def init_db_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=15)
    return _pool

async def close_db_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

async def get_db_pool():
    global _pool
    if _pool is None:
        _pool = await init_db_pool()
    return _pool

async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    pool = await get_db_pool()
    async with pool.acquire() as connection:
        yield connection
