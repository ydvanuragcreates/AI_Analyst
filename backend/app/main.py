"""
FastAPI application entry point.

Responsibilities:
1. Configure logging before anything else runs.
2. Eagerly load settings so a misconfigured environment (e.g. a missing
   OPENAI_API_KEY) fails loudly at startup instead of on the first request.
3. Create the FastAPI app, its middleware, and its lifespan (Phase 9: the
   Postgres-backed LangGraph checkpointer, when configured).
4. Register routers.
5. Register exception handlers so errors never leak internal stack traces
   to API clients.

This file intentionally contains no business logic, no routing logic, and
no agent/dataset logic — those live in api/routes/, agent/, services/, etc.
main.py only wires things together.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.graph import build_multi_agent_graph, checkpointer_context
from app.api.routes import agent, analysis, analysis_jobs, approvals, datasets, datasets_v1, health
from app.core.config import get_settings
from app.core.context import get_request_id
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.core.startup_checks import enforce_production_requirements
from app.middleware.request_id import RequestIDMiddleware
from app.services.exceptions import (
    DatasetNotFoundError,
    DatasetServiceError,
    DatasetTooLargeError,
    EmptyFileError,
    FileTooLargeError,
    InvalidDatasetError,
    QueryExecutionError,
    QueryTimeoutError,
    QueryValidationError,
    UnsupportedFileTypeError,
)

configure_logging()
logger = logging.getLogger(__name__)

# Fail fast: if required env vars (e.g. OPENAI_API_KEY) are missing,
# this raises a clear Pydantic ValidationError at startup.
_settings = get_settings()

# Fail fast, part two: ENVIRONMENT=production with Postgres/Redis
# missing must refuse to start, not silently fall
# back to InMemorySaver/eager Celery. No-op
# outside production -- see core/startup_checks.py.
enforce_production_requirements(_settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the Postgres-backed multi-agent graph once for the process's
    lifetime, when Settings.database_url is configured for Postgres --
    exactly the "same checkpointer instance shared across requests" rule
    Phase 7/8 already relied on with @lru_cache'd InMemorySaver graphs,
    extended to a real, restart-surviving checkpointer connection that
    needs its own async setup/teardown (which @lru_cache can't express).

    `app.state.multi_agent_graph` stays None whenever no Postgres is
    configured (all of dev/test today) -- api/routes/analysis.py's
    get_request_multi_agent_graph() falls back to the existing, already-
    tested get_multi_agent_graph() (InMemorySaver, @lru_cache) in that
    case, so this changes nothing about local dev or the test suite.
    """

    settings = get_settings()
    if settings.database_url and settings.database_url.startswith("postgresql"):
        async with checkpointer_context(settings.database_url) as checkpointer:
            app.state.multi_agent_graph = build_multi_agent_graph(checkpointer=checkpointer)
            logger.info("Multi-agent graph initialized with a persistent Postgres checkpointer")
            yield
    else:
        app.state.multi_agent_graph = None
        yield


app = FastAPI(
    title="AI Data Analyst Agent",
    description="Backend for the AI Data Analyst Agent portfolio project (Phase 9: production infrastructure).",
    version="0.9.0",
    lifespan=lifespan,
)

app.add_middleware(RequestIDMiddleware)

# Required for the Next.js frontend (a separate origin in dev and
# typically in production too) to call this API from a browser at all --
# without this, every request from the browser fails the CORS preflight
# before it ever reaches a route. Origins are configured, never wildcard,
# so credentialed requests still work -- browsers reject
# `Access-Control-Allow-Origin: *` combined with credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Phase 1-8 routers, entirely unversioned and unchanged.
app.include_router(health.router, prefix="/api", tags=["health"])
# Off on public deployments (ENABLE_LEGACY_API=false): these call the LLM
# synchronously with no daily cap, and the web app only uses /api/v1.
if _settings.enable_legacy_api:
    app.include_router(agent.router, prefix="/api", tags=["agent"])
    app.include_router(datasets.router, prefix="/api/datasets", tags=["datasets"])
    app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
    app.include_router(approvals.router, prefix="/api/analysis", tags=["approvals"])

# Phase 9 routers. NOT mounted as "/api/v1" duplicates of the routers
# above -- /api/v1/analysis and /api/v1/datasets are genuinely different
# contracts (job-based, persisted), not a path-prefix rename
# of the synchronous/unauthenticated Phase 1-8 ones, so dual-mounting the
# old routers under /api/v1 would create colliding, ambiguous routes
# rather than the harmless compatibility aliases the spec allows for.
app.include_router(health.root_router, tags=["health"])
app.include_router(datasets_v1.router, prefix="/api/v1/datasets", tags=["datasets-v1"])
app.include_router(analysis_jobs.router, prefix="/api/v1/analysis", tags=["analysis-v1"])

# Client-error subclasses of DatasetServiceError, mapped to their HTTP
# status. Anything raised as a bare DatasetServiceError/DatasetStorageError/
# DuckDBRegistrationError (not listed here) is treated as an unexpected
# server-side failure.
_DATASET_CLIENT_ERROR_STATUS: dict[type[DatasetServiceError], int] = {
    UnsupportedFileTypeError: 400,
    EmptyFileError: 400,
    FileTooLargeError: 400,
    InvalidDatasetError: 400,
    DatasetTooLargeError: 400,
    DatasetNotFoundError: 404,
    QueryValidationError: 400,
    QueryExecutionError: 400,
    QueryTimeoutError: 408,
}


@app.exception_handler(DatasetServiceError)
async def dataset_service_error_handler(request: Request, exc: DatasetServiceError) -> JSONResponse:
    """Map Phase 1-8 dataset-service errors to HTTP responses without leaking internals."""

    status_code = _DATASET_CLIENT_ERROR_STATUS.get(type(exc))
    if status_code is None:
        logger.error("Unexpected dataset service error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Phase 9's v1 error envelope: {"error": {"code", "message", "request_id"}}
    -- see core/errors.py for the full AppError hierarchy this covers."""

    if exc.status_code >= 500:
        logger.error("Unhandled application error: %s", exc.code, exc_info=exc)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "request_id": get_request_id()}},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler: log the real error, return a generic message to the client."""

    logger.exception("Unhandled error while processing %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "Internal server error", "request_id": get_request_id()}},
    )
