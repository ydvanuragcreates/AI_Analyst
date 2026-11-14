"""
Centralized exception hierarchy for the Phase 9 (v1) API surface.

Phase 1-8's DatasetServiceError family (services/exceptions.py) and its
existing handler in main.py are untouched -- this is a SEPARATE hierarchy
for the new v1 auth/job/persistence code, registered as an additional
FastAPI exception handler. Splitting them avoids "unnecessarily
rewriting" the Phase 1-8 error contract while still giving every new v1
error a single consistent envelope:

    {"error": {"code": "...", "message": "...", "request_id": "..."}}

`request_id` is read from `request.state.request_id`, set by
middleware/request_id.py -- every error response carries the same id
that request's log lines do, which is the whole point of request IDs.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for every Phase 9 application error.

    `code` is a stable, machine-readable string (never changes across
    refactors, unlike the human-readable `message`) -- clients should
    branch on `code`, not on parsing `message`.
    """

    code: str = "APP_ERROR"
    status_code: int = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 422


class AuthenticationError(AppError):
    """Missing, malformed, or expired credentials (401) -- e.g. no/garbled
    Authorization header. NOT for wrong-email-or-password at login (see
    api/routes/auth.py, which raises this same class but the route
    intentionally never distinguishes 'no such user' from 'wrong
    password' in the message, to avoid user enumeration)."""

    code = "AUTHENTICATION_ERROR"
    status_code = 401


class AuthorizationError(AppError):
    """The caller is authenticated but not allowed to do this specific
    thing. Reserved for non-resource-ownership cases; ownership checks on
    a specific dataset/analysis raise *NotFoundError instead (see their
    docstrings) so a 403 never leaks that the resource exists at all."""

    code = "AUTHORIZATION_ERROR"
    status_code = 403


class DatasetNotFoundError(AppError):
    """No dataset with this id, OR it exists but belongs to another user
    -- deliberately indistinguishable from the caller's perspective."""

    code = "DATASET_NOT_FOUND"
    status_code = 404


class AnalysisNotFoundError(AppError):
    """Same ownership-hiding rule as DatasetNotFoundError."""

    code = "ANALYSIS_NOT_FOUND"
    status_code = 404


class SQLValidationError(AppError):
    code = "SQL_VALIDATION_FAILED"
    status_code = 400


class SQLExecutionError(AppError):
    code = "SQL_EXECUTION_FAILED"
    status_code = 400


class AgentExecutionError(AppError):
    """The LangGraph run itself failed unexpectedly (not a modeled retry
    -- those are handled inside the graph; this is what the worker raises
    when the graph raises)."""

    code = "AGENT_EXECUTION_FAILED"
    status_code = 502


class DailyLimitReachedError(AppError):
    code = "DAILY_LIMIT_REACHED"
    status_code = 429


class RateLimitExceededError(AppError):
    code = "RATE_LIMIT_EXCEEDED"
    status_code = 429


class JobError(AppError):
    """A background job (Celery task) failed in a way not covered by a
    more specific error above."""

    code = "JOB_ERROR"
    status_code = 500


class DatabaseError(AppError):
    """A configured database rejected an operation (connection lost,
    constraint violation surfaced as a 500, etc.) -- distinct from
    db.session.DatabaseUnavailableError, which means 'no database is
    configured at all' (a deployment issue, mapped to 503 separately)."""

    code = "DATABASE_ERROR"
    status_code = 500


class IdempotencyConflictError(AppError):
    """An Idempotency-Key is being reused with a materially different
    request body -- see api/routes/analysis_jobs.py."""

    code = "IDEMPOTENCY_KEY_CONFLICT"
    status_code = 409
