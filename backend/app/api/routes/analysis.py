"""
Analytical query endpoints.

Routes stay thin: validate the request via Pydantic, call a service (or
the agent graph), return a typed result. SQL validation and execution
live in DuckDBService / utils/sql_utils.py. Every blocking service call
is offloaded via run_in_threadpool (DuckDB execution) or awaited natively
(the agent graph, whose nodes are already async) so a slow request never
stalls FastAPI's event loop for other in-flight requests.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from langgraph.graph.state import CompiledStateGraph
from openai import OpenAIError

from app.agent.graph import get_multi_agent_graph
from app.core.config import Settings, get_settings
from app.schemas.analysis import (
    AgentQueryRequest,
    AnalysisResponse,
    ApprovalRequiredResponse,
    DatasetSchema,
    QueryRequest,
    QueryResult,
    SampleDataResponse,
    WorkflowDebugInfo,
)
from app.services.dataset_service import DatasetService, get_dataset_service
from app.services.duckdb_service import DuckDBService, get_duckdb_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/query", response_model=QueryResult)
async def run_query(
    payload: QueryRequest,
    duckdb_service: DuckDBService = Depends(get_duckdb_service),
    settings: Settings = Depends(get_settings),
) -> QueryResult:
    """Execute a validated, read-only SQL query against one dataset's table."""

    return await run_in_threadpool(
        duckdb_service.execute_query,
        dataset_id=payload.dataset_id,
        query=payload.query,
        max_rows=settings.max_query_rows,
        timeout_seconds=settings.max_query_execution_seconds,
    )


@router.get("/{dataset_id}/schema", response_model=DatasetSchema)
async def get_dataset_schema(
    dataset_id: str,
    duckdb_service: DuckDBService = Depends(get_duckdb_service),
) -> DatasetSchema:
    """Return the DuckDB column names/types for one dataset's table."""

    return await run_in_threadpool(duckdb_service.get_schema, dataset_id)


@router.get("/{dataset_id}/sample", response_model=SampleDataResponse)
async def get_dataset_sample(
    dataset_id: str,
    duckdb_service: DuckDBService = Depends(get_duckdb_service),
    settings: Settings = Depends(get_settings),
) -> SampleDataResponse:
    """Return a small preview of one dataset's rows straight from DuckDB."""

    return await run_in_threadpool(duckdb_service.get_sample, dataset_id, settings.dataset_preview_rows)


def _build_debug_info(result: dict) -> WorkflowDebugInfo:
    """Translate the workflow's final internal state into safe, structured
    debug metadata -- the artifacts each node itself already produced
    (question analysis, plan, per-step SQL/results, retry count), never
    raw prompts or chain-of-thought."""

    dataset_schema = result.get("dataset_schema")

    return WorkflowDebugInfo(
        question_analysis=result.get("question_analysis"),
        schema_columns=[c.name for c in dataset_schema.columns] if dataset_schema else [],
        analysis_plan=result.get("analysis_plan"),
        current_step_index=result.get("current_step_index", 0),
        generated_sql=result.get("generated_sql"),
        validated_sql=result.get("validated_sql"),
        executed_queries=result.get("executed_queries", []),
        step_result_validation=result.get("step_result_validation"),
        retry_count=result.get("retry_count", 0),
    )


def _build_multi_agent_debug_info(result: dict) -> WorkflowDebugInfo:
    """The multi-agent graph's equivalent of _build_debug_info -- same
    "only what each node/agent itself already produced" rule, extended
    with the Phase 8 fields on WorkflowDebugInfo (current_agent,
    supervisor_decision, review, review_retry_count,
    human_approval_required, human_approved). Deliberately excludes
    human_feedback (a human's own free-text input, not internal LangGraph
    state, but also not something every debug caller needs echoed back)
    and findings (superseded by the final `answer`/`key_findings` once
    approved -- exposing it separately would just duplicate the response).
    """

    dataset_schema = result.get("dataset_schema")

    return WorkflowDebugInfo(
        schema_columns=[c.name for c in dataset_schema.columns] if dataset_schema else [],
        analysis_plan=result.get("analysis_plan"),
        current_step_index=result.get("current_step_index", 0),
        generated_sql=result.get("generated_sql"),
        validated_sql=result.get("validated_sql"),
        executed_queries=result.get("executed_queries", []),
        step_result_validation=result.get("step_result_validation"),
        retry_count=result.get("retry_count", 0),
        current_agent=result.get("current_agent"),
        supervisor_decision=result.get("supervisor_decision"),
        review=result.get("review"),
        review_retry_count=result.get("review_retry_count", 0),
        human_approval_required=result.get("human_approval_required", False),
        human_approved=result.get("human_approved"),
    )


def get_request_multi_agent_graph(
    request: Request, fallback: CompiledStateGraph = Depends(get_multi_agent_graph)
) -> CompiledStateGraph:
    """Prefer the lifespan-built, Postgres-checkpointed graph (see
    main.py) when one was built (Settings.database_url configured for
    Postgres); otherwise fall back to get_multi_agent_graph() -- the
    existing @lru_cache'd, InMemorySaver-backed graph Phase 8 already
    used. `fallback` is still requested via Depends() specifically so
    tests' `app.dependency_overrides[get_multi_agent_graph] = ...` keeps
    working unchanged: app.state.multi_agent_graph is None in every test
    (DATABASE_URL is never set in tests), so this always resolves to
    `fallback`, i.e. the exact same override point Phase 8's tests
    already use -- see agent/graph.py::checkpointer_context.
    """

    return getattr(request.app.state, "multi_agent_graph", None) or fallback


@router.post("", response_model=AnalysisResponse | ApprovalRequiredResponse)
async def ask_agent(
    payload: AgentQueryRequest,
    dataset_service: DatasetService = Depends(get_dataset_service),
    graph: CompiledStateGraph = Depends(get_request_multi_agent_graph),
) -> AnalysisResponse | ApprovalRequiredResponse:
    """Answer a natural-language question about a dataset via the Phase 8
    multi-agent graph (Supervisor -> Planner/trivial plan -> SQL Analyst
    subgraph -> Data Analyst -> Reviewer -> optional human approval ->
    final response -- see agent/graph.py::build_multi_agent_graph).

    Confirms the dataset exists first (a clean 404 beats discovering this
    deep inside the graph), then invokes it. This route has no analysis
    logic of its own -- every stage lives in a dedicated agent/node.
    `graph` is injected via Depends specifically so tests can swap in a
    graph built with fake models via FastAPI's dependency_overrides.

    Conversation continuity works exactly as it did for the Phase 6/7
    workflow (see ConversationContext): only the curated previous
    question/answer/findings summary carries forward, looked up via the
    checkpointer before a fresh per-turn state is built.

    Two outcomes: if the graph reaches its end, `result["final_response"]`
    is returned as normal. If it instead paused at the human_approval
    node (interrupt()), `graph.aget_state(config).next` is non-empty --
    that's the one reliable signal (confirmed empirically: neither
    `ainvoke`'s return value nor a missing `final_response` alone is as
    explicit), and this returns ApprovalRequiredResponse instead, whose
    `thread_id` the caller then passes to
    POST /api/analysis/{thread_id}/approve to resume the SAME run rather
    than starting over.
    """

    thread_id = payload.thread_id or str(uuid.uuid4())

    logger.info(
        "Multi-agent request received: dataset_id=%s question=%r debug=%s thread_id=%s",
        payload.dataset_id,
        payload.question,
        payload.debug,
        thread_id,
    )

    # Raises DatasetNotFoundError -> handled globally as 404 (main.py).
    dataset_service.get_dataset(payload.dataset_id)

    config = {"configurable": {"thread_id": thread_id}}

    conversation_context = None
    if payload.thread_id:
        previous_state = await graph.aget_state(config)
        conversation_context = previous_state.values.get("conversation_context")

    initial_state = {
        "dataset_id": payload.dataset_id,
        "user_question": payload.question,
        "conversation_context": conversation_context,
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

    try:
        result = await graph.ainvoke(initial_state, config=config)
    except OpenAIError:
        logger.exception("OpenAI API error while handling /api/analysis request")
        raise HTTPException(
            status_code=502,
            detail="The AI service is currently unavailable. Please try again later.",
        )
    except Exception:
        logger.exception("Unexpected error while handling /api/analysis request")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred while processing your request.",
        )

    state = await graph.aget_state(config)
    if state.next:
        reason = state.tasks[0].interrupts[0].value.get("reason", "Human approval required.") if state.tasks else "Human approval required."
        logger.info("Analysis paused for human approval: thread_id=%s reason=%s", thread_id, reason)
        return ApprovalRequiredResponse(reason=reason, thread_id=thread_id)

    response: AnalysisResponse = result["final_response"]
    response = response.model_copy(update={"thread_id": thread_id})
    if payload.debug:
        response = response.model_copy(update={"debug": _build_multi_agent_debug_info(result)})

    return response
