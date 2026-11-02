"""Database connection pool. Ready to use — no changes needed here.

Usage:
    from db import get_conn, put_conn

    conn = get_conn()
    try:
        with conn:                      # commits on success, rolls back on error
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    finally:
        put_conn(conn)
"""
import os

from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL", "postgres://leaseflow:leaseflow@localhost:5432/leaseflow")

_pool = ThreadedConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)


def get_conn():
    return _pool.getconn()


def put_conn(conn):
    _pool.putconn(conn)


def dict_cursor(conn):
    """Cursor that returns rows as dicts."""
    return conn.cursor(cursor_factory=RealDictCursor)
