"""
The Celery task that actually runs an Analysis in the background.

Wraps its entire body in `asyncio.run(...)`: Celery tasks are plain sync
functions, but the multi-agent graph, its Postgres checkpointer, and the
DB session are all async (see agent/graph.py::checkpointer_context,
db/session.py) -- rather than maintain a second, sync-only checkpointer
code path just for the worker, one `asyncio.run()` per task lets the
worker reuse the exact same graph-building code the API process uses
(see main.py's lifespan). Each task creates and fully consumes its own
event loop and its own short-lived DB session/checkpointer connection --
simpler and safer than trying to share a long-lived async pool across
Celery's forked worker processes.

Progress is derived from which top-level graph node just completed (see
_PROGRESS_BY_NODE) -- never a fake incrementing counter. Events are
published to Redis pub/sub (see services/event_service.py) for the SSE
endpoint to forward; nothing here talks to FastAPI/SSE directly.
"""

import asyncio
import logging
import os
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import openai
from celery.exceptions import SoftTimeLimitExceeded
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent.deep_analysis.graph import build_deep_analysis_graph
from app.agent.graph import build_multi_agent_graph, checkpointer_context
from app.core.config import Settings, get_settings
from app.core.context import request_id_var
from app.core.token_tracking import TokenUsageTracker
from app.db.models.analysis import Analysis, AnalysisMode, AnalysisStatus
from app.db.models.analysis_job import JobStatus
from app.db.models.analysis_state_machine import TERMINAL_STATUSES
from app.db.models.analysis_message import AnalysisMessage
from app.db.repositories.dataset_repository import DatasetRepository
from app.db.repositories.deep_analysis_repository import DeepAnalysisRepository
from app.db.session import get_session_factory
from app.services.event_service import AgentEvent, publish_event
from app.services.redis_service import RedisService, get_redis_service
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_PROGRESS_BY_NODE = {
    "supervisor": 10,
    "inspect_schema": 20,
    "planner": 30,
    "trivial_plan": 30,
    "sql_analyst": 55,
    "synthesize": 60,
    "build_chart_data": 65,
    "data_analyst": 75,
    "reviewer": 85,
    "human_approval": 90,
    "generate_final_response": 100,
}

# Phase 10: the deep-analysis graph's own node names -- a completely
# different pipeline (see agent/deep_analysis/graph.py), so it gets its
# own progress mapping rather than overloading the Q&A one above with
# node names that would collide in meaning (e.g. "planner" exists in
# both graphs but does something different in each).
_DEEP_PROGRESS_BY_NODE = {
    "dataset_context": 5,
    "data_profile": 15,
    "data_quality_check": 25,
    "planner": 35,
    "hypothesis_generator": 45,
    "analysis_executor": 60,
    "insight_engine": 75,
    "evidence_validator": 80,
    "reviewer": 85,
    "report_generator": 92,
    "chart_recommendation": 97,
    "finalize": 100,
}

# Transient (retry) vs. permanent (fail immediately) OpenAI errors.
_RETRYABLE_OPENAI_ERRORS = (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError, openai.InternalServerError)


def _cancel_flag_key(analysis_id: str) -> str:
    return f"cancel_requested:{analysis_id}"


# hostname:pid -- which real worker process (of possibly many, in a real
# deployment) handled a given task, for observability (Phase 9
# production-readiness audit finding: nothing previously identified
# WHICH worker touched an analysis, only that "the worker" did).
_WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def _run_async(coro, request_id: str | None = None):
    """Run an async function to completion from this (synchronous) Celery
    task, on its own dedicated thread with its own fresh event loop.

    Plain `asyncio.run(coro)` works fine when a real Celery worker calls
    this (a separate OS process, nothing else running an event loop on
    its main thread) -- but Celery's `task_always_eager` mode (used
    automatically in tests/dev without REDIS_URL, see workers/celery_app.py)
    executes the task SYNCHRONOUSLY, inline, wherever `.delay()` was
    called -- which, when that caller is an async FastAPI route already
    running inside TestClient's event loop, means `asyncio.run()` would
    be nested inside an already-running loop (RuntimeError). Running on a
    fresh thread sidesteps that regardless of which context called in.

    `request_id`: `contextvars.ContextVar`s do NOT cross process
    boundaries (a real Celery worker) NOR thread boundaries (this
    ThreadPoolExecutor thread starts with a fresh context, even in eager
    mode) -- it has to be explicitly re-set here, from a plain argument
    the caller passed through `.delay()`, for every log line this task
    emits to carry the SAME request_id the original HTTP request did
    (see api/routes/analysis_jobs.py, which reads it off `request.state`).
    """

    def runner():
        if request_id is not None:
            request_id_var.set(request_id)
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(runner).result()


@celery_app.task(bind=True, name="run_analysis_job", max_retries=get_settings().job_max_retries)
def run_analysis_job(self, analysis_id: str, request_id: str | None = None) -> None:
    try:
        _run_async(_run_analysis_job(analysis_id), request_id=request_id)
    except SoftTimeLimitExceeded:
        logger.error("Analysis %s exceeded its time limit", analysis_id, extra={"analysis_id": analysis_id})
        _run_async(_mark_failed(analysis_id, "Analysis exceeded the maximum execution time."), request_id=request_id)
    except _RETRYABLE_OPENAI_ERRORS as exc:
        _handle_retryable_error(self, analysis_id, exc, request_id)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: this is the job's own top-level error boundary
        logger.exception("Analysis %s failed", analysis_id, extra={"analysis_id": analysis_id})
        _run_async(_mark_failed(analysis_id, str(exc) or "The analysis could not be completed."), request_id=request_id)


def _handle_retryable_error(task, analysis_id: str, exc: Exception, request_id: str | None) -> None:
    """Shared by run_analysis_job/resume_analysis_job.

    Production-readiness audit finding, confirmed by
    tests/test_celery_retry.py: `self.retry(exc=exc, ...)` called with
    `exc=` set, from inside an `except` block, RE-RAISES `exc` ITSELF
    once retries are exhausted -- NOT `MaxRetriesExceededError` (see
    Celery's own `Task.retry` docstring: "If this argument is set and
    retry is called while an exception was raised... it will attempt to
    re-raise the current exception"). The original code's
    `except self.MaxRetriesExceededError:` around that call was
    therefore dead code that could never actually catch anything.
    Fixed by checking the retry budget explicitly BEFORE ever calling
    `self.retry()`, so `_mark_failed` is reached deterministically on
    exhaustion instead of depending on which exception type Celery
    happens to raise.
    """

    logger.warning(
        "Transient error running analysis %s (attempt %d): %s",
        analysis_id, task.request.retries, exc, extra={"analysis_id": analysis_id},
    )
    if task.request.retries >= task.max_retries:
        _run_async(
            _mark_failed(analysis_id, "The analysis could not be completed after multiple attempts."),
            request_id=request_id,
        )
        return
    raise task.retry(exc=exc, countdown=min(2**task.request.retries, 30))


async def _mark_failed(analysis_id: str, error: str) -> None:
    """Best-effort failure recording, used from contexts (the outer sync
    task, after asyncio.run already raised) where the original session is
    gone -- opens a fresh one just to write the failure."""

    session_factory = get_session_factory()
    async with session_factory() as session:
        analysis = await _get_analysis_any_owner(session, analysis_id)
        if analysis is None:
            return

        # Defense in depth alongside _safe_publish_event: never let an
        # error-handling path overwrite a run that actually reached a
        # terminal status already (e.g. completed just before an
        # unrelated exception surfaced elsewhere in the same task).
        if analysis.status in TERMINAL_STATUSES:
            logger.warning(
                "_mark_failed(%s) called but analysis is already %s -- not overwriting",
                analysis_id, analysis.status, extra={"analysis_id": analysis_id},
            )
            return

        analysis.status = AnalysisStatus.FAILED
        if analysis.job:
            analysis.job.status = JobStatus.FAILED
            analysis.job.error = error
            analysis.job.completed_at = _utcnow()
        await session.commit()

        redis_service = get_redis_service()
        await _safe_publish_event(
            redis_service,
            AgentEvent(event_type="analysis_failed", analysis_id=analysis_id, thread_id=analysis.thread_id, payload={"error": error}),
        )


async def _get_analysis_any_owner(session: AsyncSession, analysis_id: str) -> Analysis | None:
    """The worker isn't acting on behalf of any particular HTTP caller,
    so it looks up the Analysis by id alone -- ownership was already
    enforced once, when the API route created the row in the first
    place (see api/routes/analysis_jobs.py). Eager-loads `job` (an
    AsyncSession has no implicit lazy-loading -- accessing an unloaded
    relationship outside an active IO context raises MissingGreenlet)."""

    result = await session.execute(
        select(Analysis).where(Analysis.id == uuid.UUID(analysis_id)).options(selectinload(Analysis.job))
    )
    return result.scalar_one_or_none()


async def _run_analysis_job(analysis_id: str) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    redis_service = get_redis_service()

    async with session_factory() as session:
        analysis = await _get_analysis_any_owner(session, analysis_id)
        if analysis is None:
            logger.error("Analysis %s not found; job cannot run", analysis_id)
            return

        # Duplicate-execution guard: Celery (with a Redis broker) gives
        # at-least-once delivery, not exactly-once -- a worker crash
        # between finishing this task and the broker recording that ack
        # causes a redelivery of the SAME task. Without this check, a
        # redelivered run_analysis_job would silently re-run an already-
        # completed analysis from scratch: duplicate LLM calls, a
        # duplicate AnalysisMessage, and (worse) a real chance of
        # clobbering a genuine "completed" result with whatever the
        # re-run happens to produce. See db/models/analysis_state_machine.py.
        if analysis.status in TERMINAL_STATUSES:
            logger.warning(
                "run_analysis_job %s: already %s -- skipping (likely a redelivered/duplicate task)",
                analysis_id, analysis.status, extra={"analysis_id": analysis_id},
            )
            return

        dataset_repo = DatasetRepository(session)
        dataset = await dataset_repo.get_owned(analysis.dataset_id, analysis.user_id)
        if dataset is None:
            analysis.status = AnalysisStatus.FAILED
            analysis.job.status = JobStatus.FAILED
            analysis.job.error = "The dataset for this analysis no longer exists."
            await session.commit()
            return

        analysis.status = AnalysisStatus.RUNNING
        analysis.job.status = JobStatus.RUNNING
        analysis.job.started_at = _utcnow()
        await session.commit()

        if analysis.mode == AnalysisMode.DEEP:
            await _stream_deep_analysis_graph_run(session, analysis, settings, redis_service)
            return

        initial_state = {
            "dataset_id": str(dataset.id),
            "user_question": analysis.question,
            "conversation_context": None,
            "current_agent": None,
            "supervisor_decision": None,
            "dataset_schema": None,
            "analysis_plan": None,
            "current_step_index": 0,
            "completed_steps": [],
            "generated_sql": None,
            "validated_sql": None,
            "executed_queries": [],
            "step_result_validation": None,
            "retry_reason": None,
            "retry_count": 0,
            "findings": None,
            "review": None,
            "review_retry_count": 0,
            "human_approval_required": False,
            "human_approval_reason": None,
            "human_approved": None,
            "human_feedback": None,
            "synthesis_context": None,
            "chart": None,
            "final_response": None,
        }

        await _stream_graph_run(session, analysis, settings, redis_service, graph_input=initial_state)


@celery_app.task(bind=True, name="resume_analysis_job", max_retries=get_settings().job_max_retries)
def resume_analysis_job(self, analysis_id: str, approved: bool, feedback: str | None, request_id: str | None = None) -> None:
    """Resume a thread paused at the human_approval interrupt -- the
    Celery-task counterpart of api/routes/analysis_jobs.py's approve
    endpoint. Same top-level error handling as run_analysis_job."""

    try:
        _run_async(_resume_analysis_job(analysis_id, approved, feedback), request_id=request_id)
    except SoftTimeLimitExceeded:
        _run_async(_mark_failed(analysis_id, "Analysis exceeded the maximum execution time."), request_id=request_id)
    except _RETRYABLE_OPENAI_ERRORS as exc:
        _handle_retryable_error(self, analysis_id, exc, request_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Resuming analysis %s failed", analysis_id, extra={"analysis_id": analysis_id})
        _run_async(_mark_failed(analysis_id, str(exc) or "The analysis could not be completed."), request_id=request_id)


async def _resume_analysis_job(analysis_id: str, approved: bool, feedback: str | None) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    redis_service = get_redis_service()

    async with session_factory() as session:
        analysis = await _get_analysis_any_owner(session, analysis_id)
        if analysis is None:
            logger.error("Analysis %s not found; resume cannot run", analysis_id)
            return

        # Same duplicate-execution guard as run_analysis_job, but for
        # resume specifically: only a thread genuinely paused for
        # approval is valid to resume. A redelivered resume_analysis_job
        # (or one that raced a cancellation) would otherwise silently
        # re-run graph.astream(Command(resume=...)) against a thread
        # that's already moved on.
        if analysis.status != AnalysisStatus.WAITING_FOR_APPROVAL:
            logger.warning(
                "resume_analysis_job %s: status is %s, not waiting_for_approval -- skipping "
                "(likely a redelivered/duplicate task, or it was cancelled)",
                analysis_id, analysis.status, extra={"analysis_id": analysis_id},
            )
            return

        analysis.status = AnalysisStatus.RUNNING
        analysis.job.status = JobStatus.RUNNING
        await session.commit()

        resume_input = Command(resume={"approved": approved, "feedback": feedback})
        await _stream_graph_run(session, analysis, settings, redis_service, graph_input=resume_input)


async def _safe_publish_event(redis_service: RedisService | None, event: AgentEvent) -> None:
    """publish_event, but a Redis failure here must NEVER propagate.

    Production-readiness audit finding: this function's callers all run
    AFTER the real outcome (completed/failed/waiting_for_approval/
    cancelled) is already committed to Postgres. Before this wrapper, a
    Redis hiccup during the post-commit `publish_event` call would raise,
    propagate up through run_analysis_job's top-level `except Exception`
    handler, and call `_mark_failed(...)` -- silently overwriting an
    already-successful "completed" status with "failed" purely because a
    best-effort progress notification didn't make it. Event delivery is
    documented (services/event_service.py) as "safe to lose"; this is
    what actually makes that true in the failure-handling path too.
    """

    if redis_service is None:
        return
    try:
        await publish_event(redis_service, event)
    except Exception:  # noqa: BLE001 -- deliberately swallowed, see docstring
        logger.warning(
            "Failed to publish %s event for analysis %s (Redis unavailable?) -- continuing",
            event.event_type, event.analysis_id, extra={"analysis_id": event.analysis_id},
        )


async def _safe_check_cancel_requested(redis_service: RedisService | None, analysis_id: str) -> bool:
    """Same failure-isolation rule as _safe_publish_event: a Redis blip
    while checking the cooperative-cancel flag must not be mistaken for
    "cancelled," and must not crash an otherwise-healthy run."""

    if redis_service is None:
        return False
    try:
        return bool(await redis_service.get(_cancel_flag_key(analysis_id)))
    except Exception:  # noqa: BLE001
        logger.warning("Failed to check cancellation flag for analysis %s -- continuing", analysis_id, extra={"analysis_id": analysis_id})
        return False


async def _stream_graph_run(session: AsyncSession, analysis: Analysis, settings: Settings, redis_service: RedisService | None, *, graph_input) -> None:
    """Shared by both the initial run and a human-approval resume: stream
    the graph, translate node completions into progress/events, and
    write the terminal outcome (completed / waiting_for_approval /
    cancelled) to Postgres. `graph_input` is either the fresh initial
    state dict or a `Command(resume=...)` -- `.astream()` accepts both."""

    analysis_id = str(analysis.id)
    thread_id = analysis.thread_id

    await _safe_publish_event(redis_service, AgentEvent(event_type="analysis_started", analysis_id=analysis_id, thread_id=thread_id))

    tracker = TokenUsageTracker()
    config = {"configurable": {"thread_id": thread_id}, "callbacks": [tracker]}

    cancelled = False
    async with checkpointer_context(settings.database_url) as checkpointer:
        graph = build_multi_agent_graph(checkpointer=checkpointer)

        async for update in graph.astream(graph_input, config=config, stream_mode="updates"):
            for node_name in update:
                progress = _PROGRESS_BY_NODE.get(node_name)
                if progress is not None:
                    analysis.job.progress = max(analysis.job.progress, progress)
                await _publish_node_event(redis_service, analysis_id, thread_id, node_name)
            await session.commit()

            if await _safe_check_cancel_requested(redis_service, analysis_id):
                cancelled = True
                break

        if cancelled:
            analysis.status = AnalysisStatus.CANCELLED
            analysis.job.status = JobStatus.CANCELLED
            analysis.job.completed_at = _utcnow()
            await session.commit()
            if redis_service:
                try:
                    await redis_service.delete(_cancel_flag_key(analysis_id))
                except Exception:  # noqa: BLE001
                    logger.warning("Failed to clear cancellation flag for analysis %s", analysis_id, extra={"analysis_id": analysis_id})
            await _safe_publish_event(
                redis_service,
                AgentEvent(event_type="analysis_cancelled", analysis_id=analysis_id, thread_id=thread_id),
            )
            return

        state = await graph.aget_state(config)
        if state.next:
            reason = None
            if state.tasks and state.tasks[0].interrupts:
                reason = state.tasks[0].interrupts[0].value.get("reason")
            analysis.status = AnalysisStatus.WAITING_FOR_APPROVAL
            analysis.job.status = JobStatus.WAITING_FOR_APPROVAL
            analysis.job.progress = _PROGRESS_BY_NODE["human_approval"]
            await session.commit()
            await _safe_publish_event(
                redis_service,
                AgentEvent(event_type="approval_required", analysis_id=analysis_id, thread_id=thread_id, payload={"reason": reason}),
            )
            return

        final_response = state.values.get("final_response")
        analysis.status = AnalysisStatus.COMPLETED
        analysis.answer = final_response.answer if final_response else None
        analysis.confidence = final_response.confidence if final_response else None
        analysis.token_usage = tracker.usage
        analysis.completed_at = _utcnow()
        analysis.job.status = JobStatus.COMPLETED
        analysis.job.progress = 100
        analysis.job.completed_at = _utcnow()
        if final_response:
            session.add(AnalysisMessage(analysis_id=analysis.id, role="assistant", content=final_response.answer))
        await session.commit()

        # Basic metrics via structured log fields (see core/logging.py's
        # JSONFormatter) -- analysis/queue duration, token usage, worker
        # identity -- not a separate metrics system; deliberately not
        # over-engineered for this project's scale.
        queue_duration_ms = None
        run_duration_ms = None
        if analysis.job.started_at is not None:
            run_duration_ms = (_as_aware(analysis.job.completed_at) - _as_aware(analysis.job.started_at)).total_seconds() * 1000
            queue_duration_ms = (_as_aware(analysis.job.started_at) - _as_aware(analysis.created_at)).total_seconds() * 1000
        logger.info(
            "Analysis completed",
            extra={
                "analysis_id": analysis_id, "thread_id": thread_id, "worker_id": _WORKER_ID,
                "status": "completed", "queue_duration_ms": queue_duration_ms, "run_duration_ms": run_duration_ms,
                "token_usage": tracker.usage,
            },
        )

        await _safe_publish_event(
            redis_service,
            AgentEvent(event_type="analysis_completed", analysis_id=analysis_id, thread_id=thread_id, payload={"status": "completed"}),
        )


async def _publish_node_event(redis_service: RedisService | None, analysis_id: str, thread_id: str, node_name: str) -> None:
    if not redis_service:
        return

    event_type = {
        "supervisor": "agent_started",
        "planner": "planner_completed",
        "trivial_plan": "planner_completed",
        "sql_analyst": "sql_completed",
        "reviewer": "review_started",
    }.get(node_name)
    if event_type is None:
        return

    payload = {"node": node_name}
    if node_name == "sql_analyst":
        await _safe_publish_event(
            redis_service, AgentEvent(event_type="sql_started", analysis_id=analysis_id, thread_id=thread_id, payload=payload)
        )
    await _safe_publish_event(
        redis_service, AgentEvent(event_type=event_type, analysis_id=analysis_id, thread_id=thread_id, payload=payload)
    )


async def _stream_deep_analysis_graph_run(session: AsyncSession, analysis: Analysis, settings: Settings, redis_service: RedisService | None) -> None:
    """Phase 10 counterpart of _stream_graph_run, for Analysis.mode ==
    'deep': streams agent/deep_analysis/graph.py instead of the Q&A
    multi-agent graph, and persists its rich DeepAnalysisResponse into a
    DeepAnalysisResult row (see db/models/deep_analysis_result.py)
    instead of just answer/confidence. Cancellation, progress, and event
    publishing follow the exact same pattern as _stream_graph_run --
    duplicated rather than parameterized into one mega-function, since the
    two graphs' initial state shapes and terminal persistence are
    genuinely different (this graph never pauses for human approval, so
    there is no waiting_for_approval branch here at all)."""

    analysis_id = str(analysis.id)
    thread_id = analysis.thread_id

    await _safe_publish_event(redis_service, AgentEvent(event_type="analysis_started", analysis_id=analysis_id, thread_id=thread_id))

    tracker = TokenUsageTracker()
    config = {"configurable": {"thread_id": thread_id}, "callbacks": [tracker]}

    initial_state = {
        "dataset_id": str(analysis.dataset_id),
        "user_question": analysis.question or None,
        "dataset_schema": None,
        "profile": None,
        "data_quality": None,
        "analysis_plan": None,
        "hypotheses": [],
        "hypothesis_results": [],
        "trends": [],
        "anomalies": [],
        "correlations": [],
        "segments": [],
        "insights": [],
        "review": None,
        "review_retry_count": 0,
        "report": None,
        "recommendations": [],
        "visualizations": [],
        "budget": None,
        "trace": [],
        "start_time": 0.0,
        "final_response": None,
    }

    cancelled = False
    async with checkpointer_context(settings.database_url) as checkpointer:
        graph = build_deep_analysis_graph(checkpointer=checkpointer)

        async for update in graph.astream(initial_state, config=config, stream_mode="updates"):
            for node_name in update:
                progress = _DEEP_PROGRESS_BY_NODE.get(node_name)
                if progress is not None:
                    analysis.job.progress = max(analysis.job.progress, progress)
                await _publish_deep_node_event(redis_service, analysis_id, thread_id, node_name)
            await session.commit()

            if await _safe_check_cancel_requested(redis_service, analysis_id):
                cancelled = True
                break

        if cancelled:
            analysis.status = AnalysisStatus.CANCELLED
            analysis.job.status = JobStatus.CANCELLED
            analysis.job.completed_at = _utcnow()
            await session.commit()
            if redis_service:
                try:
                    await redis_service.delete(_cancel_flag_key(analysis_id))
                except Exception:  # noqa: BLE001
                    logger.warning("Failed to clear cancellation flag for analysis %s", analysis_id, extra={"analysis_id": analysis_id})
            await _safe_publish_event(redis_service, AgentEvent(event_type="analysis_cancelled", analysis_id=analysis_id, thread_id=thread_id))
            return

        state = await graph.aget_state(config)
        final_response = state.values.get("final_response")

        analysis.status = AnalysisStatus.COMPLETED
        analysis.answer = final_response.summary if final_response else None
        analysis.confidence = final_response.review.confidence if final_response and final_response.review else None
        analysis.token_usage = tracker.usage
        analysis.completed_at = _utcnow()
        analysis.job.status = JobStatus.COMPLETED
        analysis.job.progress = 100
        analysis.job.completed_at = _utcnow()

        if final_response:
            session.add(AnalysisMessage(analysis_id=analysis.id, role="assistant", content=final_response.summary))
            await DeepAnalysisRepository(session).create(
                analysis_id=analysis.id,
                fields={
                    "status": final_response.status,
                    "summary": final_response.summary,
                    "profile": final_response.profile.model_dump(mode="json") if final_response.profile else None,
                    "data_quality": [i.model_dump(mode="json") for i in final_response.data_quality],
                    "insights": [i.model_dump(mode="json") for i in final_response.insights],
                    "trends": [t.model_dump(mode="json") for t in final_response.trends],
                    "anomalies": [a.model_dump(mode="json") for a in final_response.anomalies],
                    "correlations": [c.model_dump(mode="json") for c in final_response.correlations],
                    "segments": [s.model_dump(mode="json") for s in final_response.segments],
                    "hypothesis_results": [h.model_dump(mode="json") for h in final_response.hypothesis_results],
                    "recommendations": [r.model_dump(mode="json") for r in final_response.recommendations],
                    "visualizations": [v.model_dump(mode="json") for v in final_response.visualizations],
                    "limitations": final_response.limitations,
                    "report": final_response.report.model_dump(mode="json") if final_response.report else None,
                    "trace": [t.model_dump(mode="json") for t in final_response.trace],
                    "budget": final_response.budget.model_dump(mode="json") if final_response.budget else None,
                },
            )
        await session.commit()

        logger.info(
            "Deep analysis completed",
            extra={
                "analysis_id": analysis_id, "thread_id": thread_id, "worker_id": _WORKER_ID,
                "status": final_response.status if final_response else "unknown",
                "insight_count": len(final_response.insights) if final_response else 0,
                "token_usage": tracker.usage,
            },
        )
        await _safe_publish_event(redis_service, AgentEvent(event_type="analysis_completed", analysis_id=analysis_id, thread_id=thread_id, payload={"status": "completed"}))


async def _publish_deep_node_event(redis_service: RedisService | None, analysis_id: str, thread_id: str, node_name: str) -> None:
    if not redis_service:
        return

    event_type = {
        "dataset_context": "agent_started",
        "data_profile": "profiling_completed",
        "data_quality_check": "data_quality_completed",
        "planner": "planner_completed",
        "hypothesis_generator": "hypotheses_generated",
        "analysis_executor": "analysis_step_completed",
        "insight_engine": "insights_generated",
        "reviewer": "review_started",
        "report_generator": "report_completed",
    }.get(node_name)
    if event_type is None:
        return

    await _safe_publish_event(redis_service, AgentEvent(event_type=event_type, analysis_id=analysis_id, thread_id=thread_id, payload={"node": node_name}))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    """SQLite (unlike Postgres) silently drops tzinfo on DateTime(timezone=True)
    columns on read, so a value loaded from the DB can come back naive even
    though it was stored via _utcnow(). Assume naive == UTC before comparing/
    subtracting against a freshly-created aware datetime."""

    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
