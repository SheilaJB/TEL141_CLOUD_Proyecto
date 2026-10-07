"""
database.py — Conexión Asíncrona a PostgreSQL (asyncpg)
"""
import os
import asyncpg
from typing import AsyncGenerator

DB_URL = os.getenv("DATABASE_URL", "postgresql://cloud_admin:cloud_admin@127.0.0.1:5432/cloud_g3")

_pool = None

async def get_db_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=10)
    return _pool

async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    pool = await get_db_pool()
    async with pool.acquire() as connection:
        yield connection
