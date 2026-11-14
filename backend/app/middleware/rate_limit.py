"""
Redis-backed rate limiting.

A FastAPI dependency factory, not ASGI middleware -- rate limits here are
deliberately per-route and per-value ("Apply limits to expensive
endpoints," not every endpoint), which `Depends()` expresses far more
directly than a blanket middleware would (no per-path branching logic
needed; each route just declares the limiter it wants). Lives under
middleware/ anyway since it's the same cross-cutting-request-processing
concern as request_id.py.

Fixed-window counter: `INCR ratelimit:{bucket}:{identity}` with a TTL
set on the first increment (see RedisService.increment) -- simple,
not a sliding window, which is a reasonable, explicitly-acknowledged
trade-off for "protect an expensive endpoint from abuse," not "meter
billing precisely to the second."

A no-op (never blocks) when Redis isn't configured -- consistent with
RedisService.get_redis_service() returning None in that case; rate
limiting is a defense-in-depth feature, not something dev/tests should
have to stand up Redis for.
"""

import logging
import re

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.client_ip import client_key
from app.core.errors import RateLimitExceededError
from app.services.redis_service import RedisService, get_redis_service

logger = logging.getLogger(__name__)

_LIMIT_SPEC = re.compile(r"^(\d+)/(second|minute|hour)$")
_WINDOW_SECONDS = {"second": 1, "minute": 60, "hour": 3600}


def _parse_limit(spec: str) -> tuple[int, int]:
    match = _LIMIT_SPEC.match(spec)
    if not match:
        raise ValueError(f"Invalid rate limit spec '{spec}', expected '<count>/<second|minute|hour>'")
    count, unit = match.groups()
    return int(count), _WINDOW_SECONDS[unit]


def _client_identity(request: Request, settings: Settings) -> str:
    """Keyed by `request.state.user_id` when something upstream sets it;
    otherwise (every request, now that authentication is removed) by the
    caller's hashed IP -- resolved by core/client_ip.py, NOT
    request.client.host, which a caller can spoof via X-Forwarded-For."""

    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    return f"client:{client_key(request, settings)}"


def rate_limit(bucket: str, limit_setting: str):
    """Build a dependency enforcing `getattr(Settings, limit_setting)` (e.g.
    "analysis_rate_limit") requests per window, per caller, for `bucket`.

    `limit_setting` names a Settings field rather than a literal count so
    the limit stays configurable via environment variables (ANALYSIS_RATE_LIMIT,
    UPLOAD_RATE_LIMIT, AUTH_RATE_LIMIT) without touching route code.
    """

    async def dependency(
        request: Request,
        settings: Settings = Depends(get_settings),
        redis_service: RedisService | None = Depends(get_redis_service),
    ) -> None:
        if redis_service is None:
            return  # Redis not configured -- rate limiting disabled, not enforced-and-failing.

        limit, window_seconds = _parse_limit(getattr(settings, limit_setting))
        identity = _client_identity(request, settings)
        key = f"ratelimit:{bucket}:{identity}"

        count = await redis_service.increment(key, ttl_seconds=window_seconds)
        if count > limit:
            logger.warning("Rate limit exceeded: bucket=%s identity=%s count=%d limit=%d", bucket, identity, count, limit)
            raise RateLimitExceededError(f"Rate limit exceeded: {limit} requests per {window_seconds}s")

    return dependency
