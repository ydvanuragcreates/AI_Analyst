"""
Unit tests for services/event_service.py -- the SSE reliability fixes
from the Phase 9 production-readiness audit (event ids, heartbeats,
graceful Redis-failure handling). Exercised directly against the module,
not through the full HTTP SSE endpoint (see test_v1_api.py's
test_stream_* tests for that level) -- faster and more precise for
proving these specific mechanics.
"""

import asyncio

import pytest

from app.services.event_service import (
    AgentEvent,
    StreamUnavailableError,
    format_sse,
    publish_event,
    subscribe_events,
)
from app.services.redis_service import RedisService


def test_agent_event_gets_a_unique_event_id() -> None:
    a = AgentEvent(event_type="analysis_started", analysis_id="x", thread_id="t")
    b = AgentEvent(event_type="analysis_started", analysis_id="x", thread_id="t")
    assert a.event_id != b.event_id


def test_every_worker_event_type_is_a_valid_agent_event() -> None:
    """Regression test: workers/analysis_worker.py's two node-name ->
    event_type mappings (_publish_node_event for the Q&A graph,
    _publish_deep_node_event for the Phase 10 deep-analysis graph) must
    only ever emit event_types this Literal actually accepts --
    AgentEvent.event_type is a closed Literal, so publishing anything
    else raises ValidationError at publish time. Caught here directly
    against both mappings' literal values, rather than relying on a
    fake_redis-wired end-to-end test to happen to exercise every event
    type (which is exactly how this slipped through once already: see
    the deep-analysis mapping's addition in the same commit as this test)."""

    from app.workers.analysis_worker import _publish_deep_node_event, _publish_node_event

    import asyncio

    import fakeredis

    from app.services.redis_service import RedisService

    async def _run() -> None:
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        service = RedisService(client)
        try:
            q_a_nodes = ["supervisor", "planner", "trivial_plan", "sql_analyst", "reviewer", "unknown_node"]
            deep_nodes = [
                "dataset_context", "data_profile", "data_quality_check", "planner",
                "hypothesis_generator", "analysis_executor", "insight_engine", "reviewer",
                "report_generator", "unknown_node",
            ]
            for node in q_a_nodes:
                await _publish_node_event(service, "a", "t", node)
            for node in deep_nodes:
                await _publish_deep_node_event(service, "a", "t", node)
        finally:
            await client.aclose()

    asyncio.run(_run())  # raises ValidationError (via _safe_publish_event's own re-raise path) if any mapping is invalid


def test_format_sse_includes_event_id_and_type() -> None:
    event = AgentEvent(event_type="analysis_completed", analysis_id="abc", thread_id="t", payload={"status": "completed"})
    frame = format_sse(event)
    assert f"id: {event.event_id}" in frame
    assert "event: analysis_completed" in frame
    assert '"status": "completed"' in frame or '"status":"completed"' in frame.replace(" ", "")


def test_format_sse_none_is_a_heartbeat_comment() -> None:
    frame = format_sse(None)
    assert frame.startswith(":")
    assert "keep-alive" in frame


def test_subscribe_events_yields_published_events(fake_redis: RedisService) -> None:
    """A real (fakeredis-backed) publish/subscribe round trip -- proves
    the plumbing, not just the types."""

    async def scenario():
        analysis_id = "test-analysis-1"
        received: list[AgentEvent] = []

        async def subscriber():
            async for event in subscribe_events(fake_redis, analysis_id):
                if event is not None:
                    received.append(event)
                    break

        subscriber_task = asyncio.create_task(subscriber())
        await asyncio.sleep(0.05)  # let the subscribe() call land before publishing
        await publish_event(
            fake_redis, AgentEvent(event_type="analysis_started", analysis_id=analysis_id, thread_id="t1")
        )
        await asyncio.wait_for(subscriber_task, timeout=5)
        return received

    received = asyncio.run(scenario())
    assert len(received) == 1
    assert received[0].event_type == "analysis_started"
    assert received[0].analysis_id == "test-analysis-1"


def test_subscribe_events_raises_stream_unavailable_on_redis_failure() -> None:
    """A Redis connection that's actually broken must surface as
    StreamUnavailableError (which api/routes/analysis_jobs.py catches and
    turns into a clean stream-close note), never an unhandled exception
    that would 500 the SSE endpoint mid-stream."""

    class BrokenPubSub:
        async def subscribe(self, channel):
            raise ConnectionError("simulated Redis outage")

        async def unsubscribe(self, channel):
            pass

        async def aclose(self):
            pass

    class BrokenRedisService:
        def pubsub(self):
            return BrokenPubSub()

    async def scenario():
        with pytest.raises(StreamUnavailableError):
            async for _ in subscribe_events(BrokenRedisService(), "any-id"):  # noqa: B007
                pass

    asyncio.run(scenario())
