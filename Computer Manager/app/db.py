from contextlib import contextmanager

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app import config

pool = ConnectionPool(
    config.DATABASE_URL,
    min_size=1,
    max_size=config.DB_POOL_MAX,
    kwargs={"row_factory": dict_row},
    open=False,
)


def open_pool():
    pool.open()


def close_pool():
    pool.close()


@contextmanager
def tx():
    with pool.connection(timeout=10) as conn:
        yield conn


@contextmanager
def node_lock(nodo_id: str):
    with psycopg.connect(config.DATABASE_URL, autocommit=True) as lock_conn:
        got = lock_conn.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (str(nodo_id),)
        ).fetchone()[0]
        if not got:
            raise HTTPException(409, "Ya hay una operación en curso sobre esta VM")
        try:
            yield
        finally:
            lock_conn.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (str(nodo_id),)
            )
