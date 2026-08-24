"""
The Celery application: Redis as both broker and result backend.

`task_always_eager` is turned on automatically when REDIS_URL isn't
configured -- this is what lets the test suite call
`run_analysis_job.delay(...)` and have it execute synchronously in-process
with no real broker connection needed (Celery's eager mode never talks to
the broker at all), while production (REDIS_URL set) dispatches to a real
worker process exactly as normal. No separate "test mode" flag to
remember to set -- it falls out of the same "Redis optional" rule the
rest of Phase 9 follows (see services/redis_service.py).
"""

from celery import Celery

from app.core.config import get_settings
from app.core.startup_checks import enforce_production_requirements

settings = get_settings()

# Same fail-fast rule as the API process (main.py) -- a production
# worker started without a real Postgres/Redis/JWT secret is exactly as
# dangerous as a production API started that way (silently-losing
# checkpoints, or a worker that's actually running eager/in-process
# because REDIS_URL was never set). No-op outside production.
enforce_production_requirements(settings)

celery_app = Celery(
    "ai_data_analyst_agent",
    broker=settings.redis_url or "memory://",
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_always_eager=not settings.redis_url,
    task_eager_propagates=True,
    task_track_started=True,
    worker_max_tasks_per_child=100,
    task_soft_time_limit=settings.analysis_timeout_seconds,
    task_time_limit=settings.analysis_timeout_seconds + 10,
    # So `celery -A app.workers.celery_app worker` finds the @celery_app.task
    # definitions without relying on Django-style autodiscovery conventions.
    imports=("app.workers.analysis_worker",),
)
