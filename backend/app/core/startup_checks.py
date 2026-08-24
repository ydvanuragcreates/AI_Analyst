"""
Environment-aware startup validation.

Phase 9 production-readiness audit finding (Critical): every "is Postgres/
Redis configured?" check elsewhere in this codebase (checkpointer_context,
RedisService, Celery's eager-mode switch) is a graceful *fallback* --
correct and desirable in development, where the whole point is running
without any of that infrastructure. In production, the same fallback is
a silent correctness/security hazard: a misconfigured `DATABASE_URL`
would silently downgrade every analysis to a non-persistent
`InMemorySaver` (losing all state on the next restart, without warning);
a missing `REDIS_URL` would silently run Celery in-process (no real
background jobs at all) and disable rate limiting; a default/short
`JWT_SECRET_KEY` would let anyone who reads this repository's source
forge valid tokens against a real deployment.

`enforce_production_requirements` is the one place that turns "silently
degraded" into "refuses to start" -- called once at process startup, by
both the API (main.py) and the worker (workers/celery_app.py), so a
misconfigured production deployment fails loudly and immediately in
either process, not partway through serving its first request.
"""

from app.core.config import Settings

_INSECURE_JWT_DEFAULTS = {"change-me-in-production", "secret", "changeme", ""}
_MIN_JWT_SECRET_LENGTH = 32


class ProductionConfigurationError(RuntimeError):
    """Raised at startup when ENVIRONMENT=production but required
    infrastructure/secrets are missing or insecure. Deliberately a plain
    RuntimeError (not an AppError/HTTP concern) -- this must abort
    process startup, not produce an HTTP response."""


def enforce_production_requirements(settings: Settings) -> None:
    """No-op outside production (development/test keep every documented
    fallback: SQLite, InMemorySaver, fakeredis, Celery eager mode).

    In production, collects every violation before raising -- so a
    misconfigured deployment sees the full list of what's wrong in one
    startup failure, not one error per restart-fix-restart cycle.
    """

    if settings.environment != "production":
        return

    errors: list[str] = []

    if not settings.database_url or not settings.database_url.startswith("postgresql"):
        errors.append(
            "DATABASE_URL must be a postgresql:// URL in production (got "
            f"{settings.database_url!r}) -- a persistent LangGraph checkpointer and durable "
            "Analysis/Dataset/User records both require it; without it every checkpoint and "
            "every job record would silently disappear on the next restart."
        )

    if not settings.redis_url:
        errors.append(
            "REDIS_URL is required in production -- it is the Celery broker (without it, "
            "background analysis jobs run eagerly, in-process, blocking the request that "
            "created them), the rate limiter's store (without it, rate limiting is a no-op), "
            "and the SSE event transport (without it, GET /api/v1/analysis/{id}/stream cannot work)."
        )

    if not settings.jwt_secret_key or settings.jwt_secret_key.lower() in _INSECURE_JWT_DEFAULTS:
        errors.append(
            "JWT_SECRET_KEY must be set to a real secret in production -- it is currently unset "
            "or a known default value, which would let anyone who has read this source forge "
            "valid access tokens for any user."
        )
    elif len(settings.jwt_secret_key) < _MIN_JWT_SECRET_LENGTH:
        errors.append(
            f"JWT_SECRET_KEY is only {len(settings.jwt_secret_key)} characters; production requires "
            f"at least {_MIN_JWT_SECRET_LENGTH} (matches PyJWT's own HMAC-SHA256 recommendation) -- "
            "a short key is brute-forceable."
        )

    if errors:
        formatted = "\n".join(f"  - {error}" for error in errors)
        raise ProductionConfigurationError(
            f"Refusing to start with ENVIRONMENT=production and invalid configuration:\n{formatted}"
        )
