"""
database.py — Conexión Asíncrona a PostgreSQL (asyncpg)

Proporciona la gestión del pool de conexiones a la base de datos PostgreSQL.
Garantiza que las conexiones se abran y cierren eficientemente por cada petición HTTP.
"""

import os
import asyncpg
from typing import AsyncGenerator

# URL de conexión leída desde las variables de entorno (con valor por defecto para docker-compose)
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/cloud_db")

_pool = None


async def get_db_pool():
    """Obtiene o crea el pool de conexiones de asyncpg."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=10)
    return _pool


async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Inyector de dependencia para FastAPI (Depends(get_db)).
    Entrega una conexión limpia del pool y la regresa al finalizar la petición.
    """
    pool = await get_db_pool()
    async with pool.acquire() as connection:
        yield connection
