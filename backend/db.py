# backend/db.py
import logging
import os

import psycopg

LOGGER = logging.getLogger(__name__)

_pool = None


def init_pool() -> None:
    """Initialize the connection pool. Called once at app startup."""
    global _pool
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        LOGGER.warning("DATABASE_URL not set — pool not initialized")
        return
    try:
        from psycopg_pool import ConnectionPool
        _pool = ConnectionPool(
            db_url,
            min_size=1,
            max_size=10,
            open=True,
            kwargs={"autocommit": True},  # avoids implicit transactions + recommended by Neon
            # Ping every connection before lending it out: Neon closes connections when the
            # compute suspends, and a dead one is discarded and replaced instead of failing
            # the request with "SSL connection has been closed unexpectedly".
            check=ConnectionPool.check_connection,
            max_idle=180,                 # recycle idle connections every 3 min (Neon kills at ~5 min)
            max_lifetime=1800,
            reconnect_timeout=30,
        )
        LOGGER.info("DB connection pool initialized (min=1, max=10)")
    except Exception as exc:
        LOGGER.warning("Could not initialize pool (%s) — will use direct connections", exc)


def close_pool() -> None:
    """Close the pool on app shutdown."""
    global _pool
    if _pool:
        try:
            _pool.close()
        except Exception:
            pass
        _pool = None


class _PooledConnection:
    """
    Proxy around a pooled psycopg connection: close() hands the connection back
    to the pool instead of closing it; everything else is delegated.

    (Patching close() on the connection itself would also hijack the pool's own
    close() call when it discards a dead connection, leaking it.)
    """
    __slots__ = ("_pool", "_conn")

    def __init__(self, pool, conn):
        self._pool = pool
        self._conn = conn

    def __getattr__(self, name):
        if self._conn is None:
            raise psycopg.InterfaceError("connection already returned to the pool")
        return getattr(self._conn, name)

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            self._pool.putconn(conn)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def get_connection():
    """
    Return a DB connection.
    - If the pool is running: returns a pooled connection; conn.close() returns it to the pool.
    - Otherwise: opens a direct connection (fallback for cron scripts or missing pool).
    """
    if _pool and not _pool.closed:
        return _PooledConnection(_pool, _pool.getconn())
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg.connect(db_url)
