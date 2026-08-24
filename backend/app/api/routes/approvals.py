"""
Human-in-the-loop approval endpoints for the Phase 8 multi-agent graph.

Kept in their own router (rather than folded into analysis.py) because
they're a distinct concern -- resuming an already-paused thread, not
starting or continuing an ordinary analysis turn -- even though they
share analysis.py's URL prefix (/api/analysis/{thread_id}/...).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from openai import OpenAIError

from app.api.routes.analysis import get_request_multi_agent_graph
from app.schemas.analysis import AnalysisResponse, AnalysisStatusResponse, ApprovalRequest, ApprovalRequiredResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _interrupt_reason(state) -> str | None:
    """Pull the reason string out of the paused thread's interrupt payload.

    See agent/reviewer.py::build_human_approval_node -- that node is the
    only interrupt() call site in the graph, and always passes a `reason`
    key in its payload.
    """

    if not state.tasks:
        return None
    interrupts = state.tasks[0].interrupts
    if not interrupts:
        return None
    return interrupts[0].value.get("reason")


@router.get("/{thread_id}/status", response_model=AnalysisStatusResponse)
async def get_analysis_status(
    thread_id: str,
    graph: CompiledStateGraph = Depends(get_request_multi_agent_graph),
) -> AnalysisStatusResponse:
    """Report a thread's current status without exposing internal LangGraph state.

    Only what's needed to decide "should I poll again / call approve /
    just read the earlier response": running, waiting_for_approval (with
    the human-readable reason), completed, or failed.
    """

    config = {"configurable": {"thread_id": thread_id}}
    state = await graph.aget_state(config)

    if not state.values:
        raise HTTPException(status_code=404, detail=f"Unknown thread_id: {thread_id}")

    if state.next:
        return AnalysisStatusResponse(thread_id=thread_id, status="waiting_for_approval", reason=_interrupt_reason(state))

    if state.values.get("final_response") is not None:
        return AnalysisStatusResponse(thread_id=thread_id, status="completed")

    # No pending interrupt and no final_response: the last run on this
    # thread didn't reach either outcome cleanly (e.g. it errored before
    # generate_final_response committed -- POST /api/analysis surfaces
    # that as a 502/500 directly, but the checkpoint itself just reflects
    # "never finished").
    return AnalysisStatusResponse(thread_id=thread_id, status="failed", reason="The analysis did not complete.")


@router.post("/{thread_id}/approve", response_model=AnalysisResponse | ApprovalRequiredResponse)
async def approve_analysis(
    thread_id: str,
    payload: ApprovalRequest,
    graph: CompiledStateGraph = Depends(get_request_multi_agent_graph),
) -> AnalysisResponse | ApprovalRequiredResponse:
    """Resume a thread paused at the human_approval interrupt.

    Never starts a new analysis -- Command(resume=...) continues the
    SAME checkpointed run from exactly where interrupt() paused it (see
    build_human_approval_node), using the SAME thread_id's state,
    including whatever the SQL Analyst/Data Analyst/Reviewer already
    produced. A rejection with feedback is NOT a dead end: it flows back
    through the Planner with that feedback attached (see
    route_after_human_approval), not discarded.

    Approving (or a rejection's replan) can, in principle, hit a SECOND
    low-confidence/expensive/disagreement trigger and pause again -- so
    this returns the same Union as POST /api/analysis.
    """

    config = {"configurable": {"thread_id": thread_id}}
    state = await graph.aget_state(config)

    if not state.values:
        raise HTTPException(status_code=404, detail=f"Unknown thread_id: {thread_id}")
    if not state.next:
        raise HTTPException(status_code=409, detail="This analysis is not waiting for approval.")

    logger.info("Resuming thread %s: approved=%s", thread_id, payload.approved)

    try:
        result = await graph.ainvoke(
            Command(resume={"approved": payload.approved, "feedback": payload.feedback}), config=config
        )
    except OpenAIError:
        logger.exception("OpenAI API error while resuming thread %s", thread_id)
        raise HTTPException(status_code=502, detail="The AI service is currently unavailable. Please try again later.")
    except Exception:
        logger.exception("Unexpected error while resuming thread %s", thread_id)
        raise HTTPException(status_code=500, detail="An unexpected error occurred while processing your request.")

    new_state = await graph.aget_state(config)
    if new_state.next:
        reason = _interrupt_reason(new_state) or "Human approval required."
        logger.info("Thread %s paused again for human approval: %s", thread_id, reason)
        return ApprovalRequiredResponse(reason=reason, thread_id=thread_id)

    response: AnalysisResponse = result["final_response"]
    return response.model_copy(update={"thread_id": thread_id})
