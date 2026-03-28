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
            max_idle=180,                 # recycle idle connections every 3 min (Neon kills at ~5 min)
            reconnect_timeout=30,
        )
        LOGGER.info("DB connection pool initialized (min=2, max=10)")
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


def get_connection():
    """
    Return a DB connection.
    - If the pool is running: returns a pooled connection; conn.close() returns it to the pool.
    - Otherwise: opens a direct connection (fallback for cron scripts or missing pool).
    """
    if _pool and not _pool.closed:
        conn = _pool.getconn()
        # Patch close() so callers can use conn.close() to return it to the pool
        def _return_to_pool():
            try:
                _pool.putconn(conn)
            except Exception:
                pass
        conn.close = _return_to_pool
        return conn
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg.connect(db_url)
