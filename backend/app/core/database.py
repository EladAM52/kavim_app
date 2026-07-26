"""Async database engine and session management.

One transaction per request. The ``get_db`` dependency commits on a clean
return and rolls back on any exception, so a handler that raises can never
leave a partial write behind — which is what makes the "audit row, history row,
outbox row, and domain change all commit together" guarantee in SPEC §5.4 hold.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _engine_kwargs() -> dict[str, Any]:
    return {
        "echo": settings.DATABASE_ECHO,
        "pool_size": settings.DATABASE_POOL_SIZE,
        "max_overflow": settings.DATABASE_MAX_OVERFLOW,
        # Recycle before typical managed-Postgres idle timeouts, and verify a
        # connection before handing it out — cheap insurance against the
        # "server closed the connection unexpectedly" class of failure.
        "pool_recycle": 1800,
        "pool_pre_ping": True,
        "connect_args": {
            "server_settings": {
                "application_name": f"kavim-{settings.APP_ENV}",
                "timezone": "UTC",
            },
            # asyncpg caches prepared statements per connection, which breaks
            # behind transaction-mode poolers such as pgbouncer.
            "statement_cache_size": 0,
        },
    }


def get_engine() -> AsyncEngine:
    """Process-wide engine, created lazily on first use."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs())
        logger.debug("database_engine_created", pool_size=settings.DATABASE_POOL_SIZE)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,  # objects stay usable after commit
            autoflush=False,
        )
    return _sessionmaker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a session with transaction-per-request.

    Usage::

        async def handler(db: Annotated[AsyncSession, Depends(get_db)]) -> ...:
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database() -> bool:
    """Readiness probe: can we reach the database and run a statement?"""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("database_unreachable", error=str(exc))
        return False


async def get_connection() -> AsyncGenerator[AsyncConnection, None]:
    """Raw connection, for the LISTEN/NOTIFY listener and maintenance tasks."""
    async with get_engine().connect() as conn:
        yield conn


async def dispose_engine() -> None:
    """Close the pool on shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        logger.debug("database_engine_disposed")
    _engine = None
    _sessionmaker = None
