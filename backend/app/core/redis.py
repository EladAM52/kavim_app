"""Redis connection pool and cache helpers.

Redis is used for the permission cache, rate-limit counters, and (later) the
Celery broker. Everything here degrades gracefully: Redis being down must slow
the application, not break it. Rate limiting fails open with a warning and the
permission cache falls through to the database (SPEC §6.1).
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Process-wide client, created lazily on first use."""
    global _client
    if _client is None:
        _client = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
            socket_connect_timeout=3,
            socket_timeout=3,
            health_check_interval=30,
            retry_on_timeout=True,
        )
        logger.debug("redis_client_created")
    return _client


async def check_redis() -> bool:
    """Readiness probe."""
    try:
        return bool(await get_redis().ping())
    except Exception as exc:
        logger.warning("redis_unreachable", error=str(exc))
        return False


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        logger.debug("redis_client_closed")
    _client = None


# ── cache helpers ─────────────────────────────────────────────────────────
async def cache_get_json(key: str) -> Any | None:
    """Read and decode a cached JSON value. Returns ``None`` on any failure."""
    try:
        raw = await get_redis().get(key)
    except Exception as exc:
        logger.warning("cache_read_failed", key=key, error=str(exc))
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("cache_value_corrupt", key=key)
        return None


async def cache_set_json(key: str, value: Any, ttl_seconds: int) -> None:
    """Write a JSON value with a TTL. Failure is logged, never raised."""
    try:
        await get_redis().set(key, json.dumps(value), ex=ttl_seconds)
    except Exception as exc:
        logger.warning("cache_write_failed", key=key, error=str(exc))


async def cache_delete(*keys: str) -> None:
    """Explicit invalidation.

    Used whenever a role, project membership, or column definition changes — a
    revoked permission must not survive in cache (SPEC §8.4).
    """
    if not keys:
        return
    try:
        await get_redis().delete(*keys)
    except Exception as exc:
        logger.warning("cache_delete_failed", keys=keys, error=str(exc))


async def cache_delete_prefix(prefix: str) -> int:
    """Invalidate every key under a prefix.

    Uses ``SCAN`` rather than ``KEYS`` so it never blocks the Redis event loop.
    """
    deleted = 0
    try:
        client = get_redis()
        async for key in client.scan_iter(match=f"{prefix}*", count=500):
            await client.delete(key)
            deleted += 1
    except Exception as exc:
        logger.warning("cache_prefix_delete_failed", prefix=prefix, error=str(exc))
    return deleted
