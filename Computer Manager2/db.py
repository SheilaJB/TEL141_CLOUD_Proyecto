"""Database pool and transaction helpers."""

from contextlib import contextmanager
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import config

pool = ConnectionPool(
    config.DATABASE_URL,
    min_size=1,
    max_size=config.DB_POOL_MAX,
    kwargs={"row_factory": dict_row},
    open=False,
)


def open_pool() -> None:
    pool.open()


def close_pool() -> None:
    pool.close()


@contextmanager
def tx() -> Iterator[psycopg.Connection]:
    with pool.connection(timeout=10) as conn:
        with conn.transaction():
            yield conn
