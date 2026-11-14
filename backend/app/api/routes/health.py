"""
Health check endpoints.

/health (unchanged since Phase 1, mounted at /api/health): a bare
liveness indicator, no dependency checks at all.

Phase 9 adds the container-orchestrator-shaped pair, mounted at the
bare root (no /api prefix -- this is the literal path a Kubernetes/Docker
healthcheck or load balancer probes, conventionally outside any API
versioning):
- GET /health/live: is the process alive at all -- no dependency checks,
  deliberately as cheap as /health. A liveness probe that touched the
  database would restart the container for a DB blip that has nothing to
  do with whether the process itself is stuck.
- GET /health/ready: can this instance actually serve traffic -- checks
  Postgres and Redis (the two real external dependencies), each with a
  cheap query (SELECT 1 / PING), never an expensive operation.
"""

import logging

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine
from app.services.redis_service import get_redis_service

logger = logging.getLogger(__name__)
router = APIRouter()
root_router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Return a simple liveness indicator."""

    return {"status": "ok"}


@root_router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@root_router.get("/health/ready")
async def readiness(response: Response) -> dict[str, object]:
    settings = get_settings()
    checks: dict[str, str] = {}

    if settings.database_url:
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001 -- readiness must never 500
            logger.warning("Readiness check: database unavailable: %s", exc)
            checks["database"] = "unavailable"
    else:
        checks["database"] = "not_configured"

    redis_service = get_redis_service()
    if redis_service:
        checks["redis"] = "ok" if await redis_service.ping() else "unavailable"
    else:
        checks["redis"] = "not_configured"

    unhealthy = [name for name, status in checks.items() if status == "unavailable"]
    if unhealthy:
        response.status_code = 503
    return {"status": "ready" if not unhealthy else "not_ready", "checks": checks}


# TEMPORARY diagnostic (to be removed): shows which proxy headers carry the
# caller's IP on the hosting platform, so client identification can use one
# the caller can't spoof. Echoes only the caller's own connection details.
from fastapi import Request  # noqa: E402


@root_router.get("/health/_client-ip-debug", include_in_schema=False)
async def client_ip_debug(request: Request) -> dict:
    names = ("x-forwarded-for", "true-client-ip", "cf-connecting-ip", "x-real-ip", "forwarded", "x-envoy-external-address")
    return {
        "request_client_host": request.client.host if request.client else None,
        "headers": {name: request.headers.get(name) for name in names if request.headers.get(name)},
    }
