"""
Redis service abstraction.

Every other module (rate limiting, caching, SSE pub/sub, idempotency)
goes through this instead of importing `redis.asyncio` directly -- same
"one centralized factory, not scattered raw clients" pattern as
get_chat_model()/get_duckdb_service() elsewhere in this codebase.

Redis is explicitly NOT the primary data store here (PostgreSQL is,
via db/models/) -- every method on this class is either a cache (has a
TTL, safe to lose) or coordination state (rate-limit counters, pub/sub),
never the sole copy of anything that matters.

get_redis_service() returns None (not a client) when REDIS_URL isn't
configured -- callers (rate limiting, caching) are written to treat that
as "feature disabled," not "crash," since Redis is optional in dev/tests.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import redis.asyncio as redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class RedisService:
    """Thin wrapper around a `redis.asyncio.Redis` client.

    Only the operations the rest of the app actually needs are exposed
    (get/set/delete/increment/expire, plus pub/sub for SSE) -- not the
    full redis-py surface, so call sites stay decoupled from the
    underlying client library.
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    async def get(self, key: str) -> str | None:
        return await self._client.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        await self._client.set(key, value, ex=ttl_seconds)

    async def set_if_not_exists(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        """Atomic SET NX -- used for idempotency keys, so two concurrent
        requests with the same Idempotency-Key can't both "win"."""

        return bool(await self._client.set(key, value, ex=ttl_seconds, nx=True))

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def increment(self, key: str, ttl_seconds: int | None = None) -> int:
        """Atomic INCR, with the TTL set only on the first increment (a
        fixed-window rate-limit counter) -- see middleware/rate_limit.py."""

        value = await self._client.incr(key)
        if value == 1 and ttl_seconds is not None:
            await self._client.expire(key, ttl_seconds)
        return value

    async def expire(self, key: str, ttl_seconds: int) -> None:
        await self._client.expire(key, ttl_seconds)

    async def publish(self, channel: str, message: str) -> None:
        await self._client.publish(channel, message)

    def pubsub(self):
        return self._client.pubsub()

    async def ping(self) -> bool:
        """Used by GET /health/ready -- a real, cheap connectivity check."""

        try:
            return bool(await self._client.ping())
        except Exception:
            logger.exception("Redis ping failed")
            return False

    async def close(self) -> None:
        await self._client.aclose()


@lru_cache
def get_redis_service() -> RedisService | None:
    """FastAPI dependency: the process-wide RedisService, or None if
    REDIS_URL isn't configured (see module docstring)."""

    settings = get_settings()
    if not settings.redis_url:
        return None
    return RedisService(redis.from_url(settings.redis_url, decode_responses=True))
