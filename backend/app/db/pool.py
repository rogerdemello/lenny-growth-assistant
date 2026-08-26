"""asyncpg connection pool.

Deliberately no ORM. The only non-trivial thing this layer does is talk to
pgvector, and hand-written SQL does that more clearly than a mapper would.

The one real subtlety is Supabase's connection pooler.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import asyncpg

from app.core.errors import DatabaseUnavailableError
from app.core.logging import get_logger

log = get_logger(__name__)

# Supabase's transaction-mode pooler listens here. In transaction mode a
# connection is handed to a different backend per statement, so asyncpg's
# prepared-statement cache produces `DuplicatePreparedStatementError` on reuse.
# Disabling the cache is the documented fix.
SUPABASE_TRANSACTION_POOLER_PORT = 6543

_pool: asyncpg.Pool | None = None


def _normalize_dsn(dsn: str) -> str:
    """asyncpg does not understand the `postgresql+asyncpg://` SQLAlchemy scheme."""
    return dsn.replace("postgresql+asyncpg://", "postgresql://").replace("postgres://", "postgresql://")


def _uses_transaction_pooler(dsn: str) -> bool:
    try:
        return urlparse(dsn).port == SUPABASE_TRANSACTION_POOLER_PORT
    except (ValueError, TypeError):
        return False


async def init_pool(dsn: str, *, min_size: int = 1, max_size: int = 8) -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool

    dsn = _normalize_dsn(dsn)
    kwargs: dict[str, Any] = {"min_size": min_size, "max_size": max_size, "command_timeout": 60}

    if _uses_transaction_pooler(dsn):
        # Also disable the *server-side* statement name reuse; asyncpg needs
        # both to survive a transaction-mode pooler.
        kwargs["statement_cache_size"] = 0
        kwargs["max_cacheable_statement_size"] = 0
        log.info("db.pooler_detected", port=SUPABASE_TRANSACTION_POOLER_PORT, statement_cache="disabled")

    try:
        _pool = await asyncpg.create_pool(dsn, **kwargs)
    except (OSError, asyncpg.PostgresError) as exc:
        raise DatabaseUnavailableError(f"Could not connect to the database: {exc}") from exc

    log.info("db.pool_ready", min_size=min_size, max_size=max_size)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("db.pool_closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise DatabaseUnavailableError("Database pool is not initialised.")
    return _pool


async def healthcheck() -> dict[str, Any]:
    """Never raises — /health reports degradation rather than failing."""
    if _pool is None:
        return {"ok": False, "error": "pool not initialised"}
    try:
        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            episodes = await conn.fetchval("SELECT count(*) FROM episodes")
            chunks = await conn.fetchval("SELECT count(*) FROM chunks")
            embedded = await conn.fetchval("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL")
        return {"ok": True, "episodes": episodes, "chunks": chunks, "embedded_chunks": embedded}
    except asyncpg.UndefinedTableError:
        return {"ok": False, "error": "schema not applied — run `python -m app.db.migrate`"}
    except Exception as exc:  # noqa: BLE001 — health must not raise
        return {"ok": False, "error": str(exc)}
