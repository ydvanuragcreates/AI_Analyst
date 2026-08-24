"""
Phase 9 v1 analysis endpoints: job-based (create returns immediately;
the actual multi-agent run happens in a Celery worker -- see
workers/analysis_worker.py), plus status/history/cancel/approve/stream.

Routes here never touch the LangGraph graph or its checkpointer directly
-- only the worker does. The API process's job is entirely: validate,
authorize, read/write Postgres rows, and enqueue/signal the worker.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.api.deps import PageParams, get_current_user
from app.core.config import Settings, get_settings
from app.core.errors import AnalysisNotFoundError, DatasetNotFoundError, IdempotencyConflictError, ValidationError
from app.db.models.analysis import Analysis, AnalysisMode, AnalysisStatus
from app.db.models.analysis_job import JobStatus
from app.db.models.deep_analysis_result import DeepAnalysisResult
from app.db.models.user import User
from app.db.repositories.analysis_repository import AnalysisRepository
from app.db.repositories.dataset_repository import DatasetRepository
from app.db.repositories.deep_analysis_repository import DeepAnalysisRepository
from app.db.session import get_db_session
from app.middleware.rate_limit import rate_limit
from app.schemas.analysis_v1 import (
    AnalysisDetailResponse,
    AnalysisHistoryResponse,
    AnalysisJobResponse,
    AnalysisStatusV1Response,
    ApproveV1Request,
    CreateAnalysisRequest,
)
from app.schemas.deep_analysis import Insight, Report, VisualizationSpec
from app.services.event_service import format_sse, subscribe_events
from app.services.redis_service import RedisService, get_redis_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _delay_tolerating_eager_retry(task, *args, **kwargs):
    """`task.delay(...)`, but tolerant of a Celery quirk specific to
    `task_always_eager` mode (dev/test without REDIS_URL -- see
    workers/celery_app.py): a task calling `self.retry(...)` on a
    transient error (see workers/analysis_worker.py's
    `_RETRYABLE_OPENAI_ERRORS` handling) raises `celery.exceptions.Retry`
    -- which a REAL worker's own harness catches to reschedule the task
    via the broker, but eager mode has no broker to reschedule through,
    so the exception propagates all the way back to whoever called
    `.delay()`. In real (non-eager) production this never happens at
    all: `.delay()` there only enqueues and returns immediately, it
    never executes the task inline. Caught and logged here so a
    dev/test-only Celery scheduling quirk can't crash the HTTP request
    that created the analysis -- the job's DB status already reflects
    whatever the task committed before hitting the retryable error
    (see api/routes/analysis_jobs.py's refresh-after-delay calls).
    """

    from celery.exceptions import Retry

    try:
        return task.delay(*args, **kwargs)
    except Retry as exc:
        logger.warning(
            "%s.delay() raised Retry during eager execution (dev/test-only Celery quirk, see "
            "_delay_tolerating_eager_retry's docstring): %s", task.name, exc,
        )
        return None


def _to_detail(analysis: Analysis) -> AnalysisDetailResponse:
    return AnalysisDetailResponse(
        analysis_id=analysis.id,
        dataset_id=analysis.dataset_id,
        question=analysis.question,
        mode=analysis.mode,
        status=analysis.status,
        progress=analysis.job.progress if analysis.job else 0,
        answer=analysis.answer,
        confidence=analysis.confidence,
        error=analysis.job.error if analysis.job else None,
        token_usage=analysis.token_usage,
        created_at=analysis.created_at,
        completed_at=analysis.completed_at,
    )


@router.post("", response_model=AnalysisJobResponse, status_code=202, dependencies=[Depends(rate_limit("analysis", "analysis_rate_limit"))])
async def create_analysis_v1(
    request: Request,
    payload: CreateAnalysisRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AnalysisJobResponse:
    if len(payload.question) > settings.max_question_length:
        raise ValidationError(f"question exceeds the maximum length of {settings.max_question_length} characters")

    dataset_repo = DatasetRepository(session)
    dataset = await dataset_repo.get_owned(payload.dataset_id, current_user.id)
    if dataset is None:
        raise DatasetNotFoundError(f"Dataset '{payload.dataset_id}' was not found")

    analysis_repo = AnalysisRepository(session)

    if idempotency_key:
        existing = await analysis_repo.get_by_idempotency_key_owned(idempotency_key, current_user.id)
        if existing is not None:
            # SQLite (used in this test suite -- see the module docstring
            # on why there's no real Postgres here) drops tzinfo on
            # DateTime(timezone=True) columns even though Postgres itself
            # preserves it; normalize to UTC so this subtraction works
            # identically on both.
            created_at = existing.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - created_at
            if age > timedelta(seconds=settings.idempotency_key_ttl_seconds):
                # Expired: this key is safe to reuse for a genuinely new
                # request. Frees it on the old row (rather than deleting
                # history) so the new INSERT below doesn't collide with
                # it under the (user_id, idempotency_key) unique
                # constraint (see db/models/analysis.py).
                logger.info(
                    "Idempotency-Key %s expired (age %s > TTL) -- treating as a new request", idempotency_key, age
                )
                existing.idempotency_key = None
                await session.flush()
            elif existing.dataset_id != payload.dataset_id or existing.question != payload.question or existing.mode != payload.mode:
                raise IdempotencyConflictError(
                    "This Idempotency-Key was already used for a different dataset_id/question"
                )
            else:
                logger.info("Idempotent replay: returning existing analysis %s", existing.id)
                return AnalysisJobResponse(analysis_id=existing.id, status=existing.status)

    try:
        analysis = await analysis_repo.create(
            user_id=current_user.id,
            dataset_id=dataset.id,
            dataset_version=dataset.version,
            thread_id=str(uuid.uuid4()),
            question=payload.question,
            idempotency_key=idempotency_key,
            mode=payload.mode,
        )
        await session.commit()
    except IntegrityError:
        # The DB-level unique constraint (user_id, idempotency_key) is
        # what actually closes the race the application-level check
        # above can't: two concurrent requests with the SAME key can
        # both pass the "does it exist yet?" check before either
        # commits. Whichever loses the race lands here -- roll back and
        # return the winner's row, the same as an ordinary idempotent
        # replay, rather than surfacing a raw database error.
        await session.rollback()
        if not idempotency_key:
            raise
        existing = await analysis_repo.get_by_idempotency_key_owned(idempotency_key, current_user.id)
        if existing is None:
            raise
        logger.info("Idempotency-Key %s lost a concurrent create race -- returning the winner %s", idempotency_key, existing.id)
        return AnalysisJobResponse(analysis_id=existing.id, status=existing.status)

    # Imported lazily: importing workers.celery_app at module load time
    # would build a Celery app (and, in eager mode, run its config) as a
    # side effect of merely importing this router -- deferred so routes
    # that never create an analysis never pay that cost.
    from app.workers.analysis_worker import run_analysis_job

    # One request_id flows from this HTTP request into the worker's logs
    # (contextvars.ContextVar does not cross the process/thread boundary
    # on its own -- see workers/analysis_worker.py::_run_async).
    async_result = _delay_tolerating_eager_retry(
        run_analysis_job, str(analysis.id), request_id=getattr(request.state, "request_id", None)
    )

    # In real (non-eager) production this is a no-op refresh (the worker
    # hasn't started yet). In Celery's eager mode (tests/dev without
    # REDIS_URL -- see workers/celery_app.py) `.delay()` above already
    # ran the WHOLE job to completion, synchronously, through its OWN,
    # separate DB session -- this session's in-memory `analysis`/`job`
    # are now stale relative to that, and committing them un-refreshed
    # would silently clobber the worker's write with the stale
    # "queued" status still held here.
    await session.refresh(
        analysis, attribute_names=["status", "answer", "confidence", "token_usage", "completed_at", "job"]
    )
    if analysis.job is not None and async_result is not None:
        analysis.job.celery_task_id = async_result.id
        await session.commit()

    logger.info("Analysis job created", extra={"user_id": str(current_user.id), "analysis_id": str(analysis.id)})
    return AnalysisJobResponse(analysis_id=analysis.id, status=analysis.status)


@router.get("/{analysis_id}", response_model=AnalysisDetailResponse)
async def get_analysis_v1(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AnalysisDetailResponse:
    analysis = await AnalysisRepository(session).get_owned(analysis_id, current_user.id)
    if analysis is None:
        raise AnalysisNotFoundError(f"Analysis '{analysis_id}' was not found")
    return _to_detail(analysis)


@router.get("", response_model=AnalysisHistoryResponse)
async def list_analyses_v1(
    dataset_id: uuid.UUID | None = None,
    status: str | None = None,
    mode: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    page: int = Query(default=1),
    page_size: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AnalysisHistoryResponse:
    params = PageParams(page=page, page_size=page_size)
    analyses, total = await AnalysisRepository(session).list_for_user(
        current_user.id, dataset_id=dataset_id, status=status, mode=mode,
        created_after=created_after, created_before=created_before,
        page=params.page, page_size=params.page_size,
    )
    return AnalysisHistoryResponse(
        analyses=[_to_detail(a) for a in analyses], total=total, page=params.page, page_size=params.page_size
    )


@router.post("/{analysis_id}/cancel", response_model=AnalysisStatusV1Response)
async def cancel_analysis_v1(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    redis_service: RedisService | None = Depends(get_redis_service),
) -> AnalysisStatusV1Response:
    """Best-effort cancellation, never a worker-process kill.

    Queued-not-yet-started: revoked outright (it will never run).
    Running: a cooperative flag the worker checks BETWEEN graph node
    steps (see workers/analysis_worker.py) -- the current node still
    finishes, nothing is killed mid-write. Already terminal: returns the
    existing status unchanged, per "if the job has already completed,
    return an appropriate response."
    """

    analysis = await AnalysisRepository(session).get_owned(analysis_id, current_user.id)
    if analysis is None:
        raise AnalysisNotFoundError(f"Analysis '{analysis_id}' was not found")

    job = analysis.job
    if job is None or job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        return AnalysisStatusV1Response(analysis_id=analysis.id, status=analysis.status, progress=job.progress if job else 0, error=job.error if job else None)

    if redis_service:
        settings = get_settings()
        await redis_service.set(f"cancel_requested:{analysis_id}", "1", ttl_seconds=settings.analysis_timeout_seconds)

    if job.celery_task_id:
        from app.workers.celery_app import celery_app

        celery_app.control.revoke(job.celery_task_id, terminate=False)

    if job.status == JobStatus.QUEUED:
        # Revoked before it ever started -- no worker will observe the
        # cooperative flag, so mark it cancelled immediately. The worker
        # never runs in this branch, so this is also the only place that
        # can publish the terminal event for it -- without this, a
        # client streaming a queued-then-cancelled analysis would wait
        # forever for an event nothing would ever send.
        analysis.status = AnalysisStatus.CANCELLED
        job.status = JobStatus.CANCELLED
        await session.commit()
        if redis_service:
            from app.services.event_service import AgentEvent, publish_event

            try:
                await publish_event(
                    redis_service, AgentEvent(event_type="analysis_cancelled", analysis_id=str(analysis.id), thread_id=analysis.thread_id)
                )
            except Exception:  # noqa: BLE001 -- best-effort, never fail the cancel request over a Redis blip
                logger.warning("Failed to publish analysis_cancelled event for %s", analysis_id)

    logger.info("Analysis cancellation requested", extra={"user_id": str(current_user.id), "analysis_id": str(analysis_id)})
    return AnalysisStatusV1Response(analysis_id=analysis.id, status=analysis.status, progress=job.progress, error=job.error)


@router.post("/{analysis_id}/approve", response_model=AnalysisJobResponse)
async def approve_analysis_v1(
    request: Request,
    analysis_id: uuid.UUID,
    payload: ApproveV1Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AnalysisJobResponse:
    """Resume a thread paused at human_approval -- see
    workers/analysis_worker.py::resume_analysis_job. Ownership is
    verified here (against the SAME user_id the analysis was created
    under) before the worker ever touches the checkpointed thread."""

    analysis = await AnalysisRepository(session).get_owned(analysis_id, current_user.id)
    if analysis is None:
        raise AnalysisNotFoundError(f"Analysis '{analysis_id}' was not found")
    if analysis.status != AnalysisStatus.WAITING_FOR_APPROVAL:
        raise ValidationError("This analysis is not waiting for approval")

    from app.workers.analysis_worker import resume_analysis_job

    async_result = _delay_tolerating_eager_retry(
        resume_analysis_job, str(analysis.id), payload.approved, payload.feedback,
        request_id=getattr(request.state, "request_id", None),
    )

    # Same "don't clobber an eager-mode job that already finished with a
    # stale in-memory status" concern as create_analysis_v1.
    await session.refresh(
        analysis, attribute_names=["status", "answer", "confidence", "token_usage", "completed_at", "job"]
    )
    if analysis.job is not None and async_result is not None:
        analysis.job.celery_task_id = async_result.id
        await session.commit()

    logger.info("Analysis approval resumed", extra={"user_id": str(current_user.id), "analysis_id": str(analysis_id), "approved": payload.approved})
    return AnalysisJobResponse(analysis_id=analysis.id, status=analysis.status)


async def _get_deep_result_owned(analysis_id: uuid.UUID, current_user: User, session: AsyncSession) -> DeepAnalysisResult:
    """Shared by the three Phase 10 read endpoints below: ownership is
    checked via the SAME AnalysisRepository.get_owned every other v1
    route already uses (never a direct, unscoped DeepAnalysisResult
    lookup) -- a DeepAnalysisResult is only ever reachable through an
    Analysis the caller owns."""

    analysis = await AnalysisRepository(session).get_owned(analysis_id, current_user.id)
    if analysis is None:
        raise AnalysisNotFoundError(f"Analysis '{analysis_id}' was not found")
    if analysis.mode != AnalysisMode.DEEP:
        raise ValidationError("This analysis was not run in deep-analysis mode")

    result = await DeepAnalysisRepository(session).get_by_analysis_id(analysis_id)
    if result is None:
        raise ValidationError(f"Deep analysis results are not available yet (status: {analysis.status})")
    return result


@router.get("/{analysis_id}/insights", response_model=list[Insight])
async def get_analysis_insights_v1(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[Insight]:
    result = await _get_deep_result_owned(analysis_id, current_user, session)
    return [Insight.model_validate(i) for i in result.insights]


@router.get("/{analysis_id}/report", response_model=Report)
async def get_analysis_report_v1(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Report:
    result = await _get_deep_result_owned(analysis_id, current_user, session)
    if result.report is None:
        raise ValidationError("This analysis did not produce a report")
    return Report.model_validate(result.report)


@router.get("/{analysis_id}/visualizations", response_model=list[VisualizationSpec])
async def get_analysis_visualizations_v1(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[VisualizationSpec]:
    result = await _get_deep_result_owned(analysis_id, current_user, session)
    return [VisualizationSpec.model_validate(v) for v in result.visualizations]


_TERMINAL_ANALYSIS_STATUSES_TO_EVENT: dict[str, str] = {
    AnalysisStatus.COMPLETED: "analysis_completed",
    AnalysisStatus.FAILED: "analysis_failed",
    AnalysisStatus.CANCELLED: "analysis_cancelled",
    AnalysisStatus.WAITING_FOR_APPROVAL: "approval_required",
}


@router.get("/{analysis_id}/stream")
async def stream_analysis_v1(
    request: Request,
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    redis_service: RedisService | None = Depends(get_redis_service),
) -> StreamingResponse:
    """Server-Sent Events: forwards the AgentEvents the worker publishes
    for this analysis_id (see services/event_service.py) -- no LangGraph
    internals, no chain-of-thought, only the same safe event types listed
    in the Phase 9 spec (analysis_started, agent_started,
    planner_completed, sql_started, sql_completed, review_started,
    approval_required, analysis_completed, analysis_failed,
    analysis_cancelled).

    Production-readiness audit finding: Redis Pub/Sub does not replay
    missed messages. If the analysis already reached a terminal state (or
    is waiting for approval) BEFORE the client ever subscribes -- the
    completely ordinary case of a fast analysis finishing before the
    client's stream request lands -- subscribing to Pub/Sub alone would
    wait forever for an event that already happened and will never be
    resent. The current DB status is checked FIRST; if it's already
    terminal, a synthetic terminal event reflecting reality is sent
    immediately and the stream closes without ever touching Redis.

    Only genuinely in-flight analyses (queued/running) subscribe to the
    live event stream, which now also emits a periodic keep-alive (see
    event_service._HEARTBEAT_SECONDS) so a connection that goes quiet is
    never indistinguishable from one that's simply hung, and closes
    cleanly (not with a raw 500) if Redis itself becomes unreachable
    mid-stream.
    """

    analysis = await AnalysisRepository(session).get_owned(analysis_id, current_user.id)
    if analysis is None:
        raise AnalysisNotFoundError(f"Analysis '{analysis_id}' was not found")

    terminal_event_type = _TERMINAL_ANALYSIS_STATUSES_TO_EVENT.get(analysis.status)
    if terminal_event_type is not None:
        from app.services.event_service import AgentEvent

        async def already_done_generator():
            yield format_sse(
                AgentEvent(
                    event_type=terminal_event_type, analysis_id=str(analysis.id), thread_id=analysis.thread_id,
                    payload={"status": analysis.status},
                )
            )

        return StreamingResponse(already_done_generator(), media_type="text/event-stream")

    if redis_service is None:
        raise ValidationError("Real-time streaming requires Redis to be configured")

    async def event_generator():
        from app.services.event_service import StreamUnavailableError

        try:
            async for event in subscribe_events(redis_service, str(analysis_id)):
                if await request.is_disconnected():
                    break
                yield format_sse(event)
                if event is not None and event.event_type in (
                    "analysis_completed", "analysis_failed", "analysis_cancelled", "approval_required",
                ):
                    break
        except StreamUnavailableError:
            logger.warning("Event stream for analysis %s became unavailable (Redis down?)", analysis_id)
            # A best-effort note, not a raw 500 -- the client can fall
            # back to polling GET /api/v1/analysis/{id} for the real status.
            yield ": stream unavailable, poll GET /api/v1/analysis/{id} instead\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
