"""
Event abstraction connecting LangGraph execution to SSE clients.

    LangGraph events -> AgentEvent -> Redis pub/sub -> SSE endpoint -> Client

`AgentEvent` is the one typed shape both the worker (publisher) and the
SSE route (subscriber) agree on -- neither touches LangGraph internals or
Redis's raw pub/sub API directly outside this module, which is the
"event abstraction, don't tightly couple LangGraph internals to FastAPI
response code" the spec asks for.

Redis Pub/Sub, not a list/stream: events are transient progress
notifications, not something that needs to be replayed after the fact
(a client that connects to the SSE endpoint after a job already
finished gets the current status via GET .../{id}, not a backlog of
missed events) -- exactly the "safe to lose" cache-only use of Redis
this project's Redis usage is scoped to elsewhere too.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

# How long subscribe_events waits for a message before yielding an SSE
# keep-alive comment instead -- without this, a connection that goes
# quiet (Redis message simply never arrives, e.g. the publishing side
# died) blocks forever with no output at all, which looks identical to
# "still working" from the client's side. A keep-alive lets the client
# (and any intermediate proxy that times out idle connections) tell
# "still connected, just nothing new yet" apart from "the connection died."
_HEARTBEAT_SECONDS = 15.0

EventType = Literal[
    "analysis_started",
    "agent_started",
    "planner_completed",
    "sql_started",
    "sql_completed",
    "review_started",
    "approval_required",
    "analysis_completed",
    "analysis_failed",
    "analysis_cancelled",
    # Phase 10: emitted only by the deep-analysis worker path
    # (workers/analysis_worker.py::_publish_deep_node_event) -- added
    # here because AgentEvent.event_type is a closed Literal, so
    # publishing any of these without a matching entry here would raise
    # a ValidationError at publish time, not silently succeed.
    "profiling_completed",
    "data_quality_completed",
    "hypotheses_generated",
    "analysis_step_completed",
    "insights_generated",
    "report_completed",
]

TERMINAL_EVENT_TYPES = frozenset({"analysis_completed", "analysis_failed", "analysis_cancelled", "approval_required"})


class AgentEvent(BaseModel):
    """One safe, structured progress event -- never chain-of-thought or
    raw prompts, only "which stage just happened" (see
    workers/analysis_worker.py for what payload each event_type carries).

    `event_id` gives every event a stable identity for tracing/dedup on
    the client side -- Pub/Sub itself has no concept of message identity,
    so without this, a client reconnecting mid-stream (or logging events
    for debugging) has no way to tell "the same event twice" from "two
    different events."
    """

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    event_type: EventType
    analysis_id: str
    thread_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)


def _channel(analysis_id: str) -> str:
    return f"analysis:{analysis_id}:events"


async def publish_event(redis_service: RedisService, event: AgentEvent) -> None:
    await redis_service.publish(_channel(event.analysis_id), event.model_dump_json())
    logger.info(
        "Agent event published",
        extra={"analysis_id": event.analysis_id, "thread_id": event.thread_id, "event": event.event_type},
    )


class StreamUnavailableError(Exception):
    """Raised by subscribe_events when Redis itself is unreachable (not
    when there's simply no message yet) -- distinct from a plain timeout
    so the SSE route can tell "Redis died" from "still waiting" and
    surface each appropriately (see api/routes/analysis_jobs.py)."""


async def subscribe_events(redis_service: RedisService, analysis_id: str) -> AsyncIterator[AgentEvent | None]:
    """Yield events published for `analysis_id` as they arrive, or `None`
    every `_HEARTBEAT_SECONDS` of silence (an SSE keep-alive -- see that
    constant's docstring) so a stalled connection is never silently
    indistinguishable from a live one with nothing to say.

    A plain `async for` generator so the SSE route (see
    api/routes/analysis_jobs.py) can `async for event in subscribe_events(...)`
    without knowing anything about redis-py's pubsub API. Always
    unsubscribes and closes the pubsub connection on the way out --
    whether that's the caller breaking the loop (client disconnected,
    terminal event seen) or an exception -- so a client that disconnects
    mid-stream never leaks a server-side subscription.
    """

    pubsub = redis_service.pubsub()
    try:
        await pubsub.subscribe(_channel(analysis_id))
    except Exception as exc:
        raise StreamUnavailableError(f"Could not subscribe to analysis {analysis_id}'s event stream") from exc

    try:
        while True:
            try:
                # redis-py's own `timeout=` (seconds) returns None if
                # nothing arrives in time -- that's the heartbeat tick;
                # no separate asyncio-level timeout wrapper needed.
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=_HEARTBEAT_SECONDS)
            except Exception as exc:
                raise StreamUnavailableError(f"Lost connection to analysis {analysis_id}'s event stream") from exc

            if message is None:
                yield None  # heartbeat -- see _HEARTBEAT_SECONDS
                continue
            if message.get("type") != "message":
                continue
            try:
                yield AgentEvent.model_validate_json(message["data"])
            except ValueError:
                logger.warning("Dropped malformed event on channel %s", _channel(analysis_id))
    finally:
        try:
            await pubsub.unsubscribe(_channel(analysis_id))
            await pubsub.aclose()
        except Exception:  # noqa: BLE001 -- best-effort cleanup, never mask whatever caused us to get here
            logger.warning("Failed to cleanly unsubscribe from analysis %s's event stream", analysis_id)


def format_sse(event: AgentEvent | None) -> str:
    """Render one AgentEvent as a Server-Sent Events wire frame, or a
    heartbeat (`event=None`) as a bare SSE comment line -- valid SSE per
    spec (any line starting with ':' is a comment, ignored by the client's
    EventSource parser but enough to keep the connection demonstrably
    alive through idle-timeout proxies)."""

    if event is None:
        return ": keep-alive\n\n"

    data = json.dumps({"analysis_id": event.analysis_id, "event_id": event.event_id, **event.payload})
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {data}\n\n"
