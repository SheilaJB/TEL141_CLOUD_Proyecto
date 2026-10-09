from typing import Optional
import asyncpg
from app.config import settings

pool: Optional[asyncpg.Pool] = None


async def init_db_pool() -> asyncpg.Pool:
    global pool
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
    )
    return pool


async def close_db_pool() -> None:
    global pool
    if pool is not None:
        await pool.close()
        pool = None


async def get_db_pool() -> asyncpg.Pool:
    global pool
    if pool is None:
        pool = await init_db_pool()
    return pool
