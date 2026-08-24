"""
Async SQLAlchemy engine/session management.

One engine per process, built lazily from `Settings.database_url` and
cached (same `@lru_cache` singleton pattern as every other service
factory in this codebase -- get_chat_model, get_duckdb_service, etc.),
not re-created per request. Connection pooling (pool_size/max_overflow/
pool_timeout) is configured from Settings rather than left at
SQLAlchemy's defaults, per "do not create a new database engine per
request" / "use proper connection pooling."

Async throughout: DB access is exactly the kind of I/O-bound operation
async provides a real benefit for (see the project's "don't make
everything async just because FastAPI supports it" principle) -- request
handlers can await a query instead of blocking a worker thread.
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings


class DatabaseUnavailableError(RuntimeError):
    """Raised when a v1 endpoint that requires persistence is called but
    DATABASE_URL isn't configured. Distinct from a real DatabaseError
    (a configured DB that failed) -- this is a deployment/config issue,
    not a runtime failure, and is mapped to a 503 (see core/errors.py)."""


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    if not settings.database_url:
        raise DatabaseUnavailableError("DATABASE_URL is not configured")

    # SQLite (used by the test suite) doesn't support pool_size/max_overflow,
    # and -- more importantly -- an aiosqlite connection is bound to the
    # event loop that opened it. This process's worker code intentionally
    # runs Celery tasks on their own thread with their own fresh loop
    # (see workers/analysis_worker.py::_run_async), so pooling/reusing a
    # SQLite connection across that and the request-handling loop would
    # break; NullPool opens a fresh connection per checkout instead,
    # sidestepping the issue entirely (this only applies to SQLite --
    # real Postgres in production runs the API and worker as genuinely
    # separate processes, each with its own engine and its own loop, so
    # this cross-loop-sharing concern never arises there).
    if settings.database_url.startswith("sqlite"):
        return create_async_engine(settings.database_url, poolclass=NullPool)

    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
    )


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one AsyncSession per request, always closed."""

    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


def database_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(settings.database_url)
