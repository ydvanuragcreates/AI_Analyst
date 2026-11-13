"""
LangGraph node implementations.

A "node" is just a function (sync or async) that takes the current state and
returns a dict of updates to merge into it. Phase 1 has exactly one node:
`process_question`, which calls the OpenAI chat model via the centralized
model factory (`get_chat_model`) and stores its reply in `response`.

The node is async and awaits `model.ainvoke(...)` rather than the sync
`model.invoke(...)` so that a slow OpenAI call never blocks the FastAPI
event loop.

Phase 4 adds a second node, `agent_node`, for the tool-calling graph (see
agent/graph.py). It follows the same shape (state in, partial state out)
but calls a *tool-bound* model instead of a plain one.

Phase 5 adds two more: `build_tools_node` (a thin wrapper around
LangGraph's own ToolNode that additionally captures the actual executed
SQL into state) and `build_final_analysis_node` (a *second*, separately
configured model call -- via with_structured_output(), not bind_tools()
-- that turns the gathered tool results into a validated AnalysisResponse
once the tool-calling loop is done).

Phase 6 adds a deterministic, non-agentic workflow: single-purpose nodes
(analyze_question, inspect_schema, create a plan, generate SQL, validate,
execute, validate the result, respond) plus routing functions for the
graph's conditional edges. Unlike the Phase 4 agent, no node here decides
"should I call a tool" -- the sequence is fixed; only *which node comes
next on failure* is decided at runtime (see route_after_* below and the
retry design in state.py).

Phase 7 extends this from "one SQL query" to "a plan of 1-5 steps, each
its own SQL query": create_sql_plan/generate_sql/execute_sql/
validate_result become create_analysis_plan/generate_step_sql/
execute_step_sql/validate_step_result, each aware of *which* step it's
working on. Two new nodes, synthesize and build_chart_data, are
deliberately non-LLM (pure Python) -- see their docstrings for why.
analyze_question also gains conversation-context awareness for follow-up
questions and picks a chart type suggestion.
"""

import json
import logging
from functools import lru_cache
from typing import Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel

from app.agent.prompts import (
    ANALYSIS_PLANNING_PROMPT,
    ANALYST_SYSTEM_PROMPT,
    FINAL_ANALYSIS_PROMPT,
    MULTI_AGENT_FINAL_RESPONSE_PROMPT,
    QUESTION_ANALYSIS_PROMPT,
    STEP_RESULT_VALIDATION_PROMPT,
    STEP_SQL_GENERATION_PROMPT,
    SYSTEM_PROMPT,
    WORKFLOW_FINAL_RESPONSE_PROMPT,
)
from app.agent.state import AgentState, AnalystState, MultiAgentState, WorkflowState
from app.agent.tools.analysis_tools import execute_sql
from app.agent.tools.dataset_tools import get_dataset_schema, get_sample_rows
from app.core.config import get_settings
from app.core.llm import get_chat_model
from app.schemas.analysis import (
    AnalysisPlan,
    AnalysisResponse,
    AnalysisStep,
    ConversationContext,
    DatasetSchema,
    ExecutedQuery,
    GeneratedSQL,
    QuestionAnalysis,
    ResultValidation,
    SQLAnalysisResult,
)
from app.schemas.visualization import ChartData
from app.services.duckdb_service import get_duckdb_service
from app.services.exceptions import DatasetServiceError, QueryValidationError
from app.utils.sql_utils import validate_query, quote_identifier

logger = logging.getLogger(__name__)

# The tool list is defined once, here, and reused by both the manual
# bind_tools() graph and the create_react_agent() variant (graph.py) --
# one source of truth for "which tools this agent has."
AGENT_TOOLS = [get_dataset_schema, get_sample_rows, execute_sql]


async def process_question(state: AnalystState) -> AnalystState:
    """Send the user's question to the chat model and record its response."""

    model = get_chat_model()
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=state["user_question"]),
    ]

    logger.debug("Invoking chat model for question: %s", state["user_question"])
    result = await model.ainvoke(messages)

    return {"response": str(result.content)}


@lru_cache
def get_model_with_tools() -> BaseChatModel:
    """The centralized model, bound to the dataset tools.

    `bind_tools()` doesn't create a second model configuration -- it
    wraps the *same* model produced by get_chat_model() (still driven by
    OPENAI_MODEL/OPENAI_API_KEY) in a RunnableBinding that attaches the
    tools' JSON schemas to every request sent to OpenAI. Cached with
    @lru_cache for the same reason get_chat_model()/get_agent_graph() are:
    a process-wide singleton, built once.
    """

    return get_chat_model().bind_tools(AGENT_TOOLS)


def build_agent_node(model_with_tools: BaseChatModel) -> Callable[[AgentState], dict]:
    """Build the 'agent' node for the tool-calling graph.

    Factored out (instead of hardcoding get_model_with_tools() inside the
    node body) so tests can inject a fake/mocked model without touching
    global state -- see build_dataset_agent_graph() in graph.py.
    """

    def agent_node(state: AgentState) -> dict:
        # The system prompt is prepended on every call rather than stored
        # in state["messages"]: state only accumulates the *conversation*
        # (human/AI/tool messages), so this node would otherwise re-add it
        # on every agent<->tools round trip.
        messages = [SystemMessage(content=ANALYST_SYSTEM_PROMPT), *state["messages"]]
        response = model_with_tools.invoke(messages)
        return {"messages": [response]}

    return agent_node


def build_tools_node() -> Callable[[AgentState], dict]:
    """Build the 'tools' node: LangGraph's own ToolNode, plus SQL capture.

    ToolNode does the real work (matching each tool_call to a tool,
    resolving InjectedState args, executing, wrapping results as
    ToolMessages) -- this doesn't reimplement any of that. It only reads
    the ToolMessages ToolNode already produced and, if one came from
    execute_sql with no error, copies the query text it carried (see
    analysis_tools.execute_sql) into `executed_sql` state. That's the
    *only* source AnalysisResponse.sql_used is ever allowed to come from
    -- never the model's own memory of what it ran.
    """

    tool_node = ToolNode(AGENT_TOOLS)

    def tools_node(state: AgentState) -> dict:
        result = tool_node.invoke(state)
        updates: dict = dict(result)

        for message in result.get("messages", []):
            if not isinstance(message, ToolMessage):
                continue

            logger.info("Tool executed: %s (dataset_id=%s)", message.name, state.get("dataset_id"))

            if message.name != "execute_sql":
                continue
            try:
                payload = json.loads(message.content)
            except (TypeError, ValueError):
                continue
            if "error" not in payload and payload.get("query"):
                updates["executed_sql"] = payload["query"]

        return updates

    return tools_node


@lru_cache
def get_structured_model_for(schema: type[BaseModel]) -> Runnable:
    """The centralized model, configured to force a reply conforming to `schema`.

    Compare with get_model_with_tools() above: bind_tools() leaves the
    model free to reply with plain text OR a tool call. with_structured_output()
    is the opposite kind of constraint -- the model MUST return something
    that satisfies the given schema, or LangChain raises rather than let a
    malformed reply through. Still the same centralized model from
    get_chat_model(); this is a separate *configuration* of it per schema,
    not a separate model. Phase 6 needs several different schemas
    (QuestionAnalysis, SQLPlan, GeneratedSQL, ResultValidation,
    AnalysisResponse) at different stages, hence this being generic over
    `schema` rather than hardcoded to one -- cached per schema class via
    lru_cache, same as every other factory in this module.
    """

    return get_chat_model().with_structured_output(schema)


@lru_cache
def get_structured_model() -> Runnable:
    """Phase 5's AnalysisResponse-specific structured model.

    Kept as its own function (delegating to get_structured_model_for)
    purely so Phase 4/5 code and tests that already call
    get_structured_model() by name keep working unmodified.
    """

    return get_structured_model_for(AnalysisResponse)


def build_final_analysis_node(structured_model: Runnable) -> Callable[[AgentState], dict]:
    """Build the 'final_response' node: turn gathered tool results into AnalysisResponse.

    Runs once, after the agent/tools loop exits (tools_condition routed to
    END, remapped to this node instead -- see graph.py). Takes the whole
    transcript (the original question plus every tool call and result
    that happened) and FINAL_ANALYSIS_PROMPT, and asks the structured
    model to summarize it. `sql_used` in the model's own reply is then
    unconditionally overwritten with `state["executed_sql"]` -- the model
    is never trusted to transcribe that field correctly, even though the
    prompt also tells it what the real SQL was, for context.

    AnalysisResponse.sql_used became a list[str] in Phase 7 (a single
    workflow run can now execute several queries); this agent still only
    ever tracks one (`executed_sql: str | None`), so it's wrapped in a
    single-item list here (or an empty list if nothing executed) to stay
    valid against that shared schema.
    """

    def final_analysis_node(state: AgentState) -> dict:
        executed_sql = state.get("executed_sql")
        dataset_id = state.get("dataset_id")

        prompt_messages: list[BaseMessage] = [
            SystemMessage(content=FINAL_ANALYSIS_PROMPT),
            *state["messages"],
        ]
        if executed_sql:
            prompt_messages.append(
                SystemMessage(
                    content=(
                        "The actual SQL query executed by the system was:\n\n"
                        f"{executed_sql}\n\n"
                        "This is ground truth. Reference it; do not alter it."
                    )
                )
            )

        try:
            response: AnalysisResponse = structured_model.invoke(prompt_messages)
        except Exception:
            logger.exception("Structured-output generation failed for dataset %s", dataset_id)
            raise

        # Deterministic override -- see docstring above.
        response = response.model_copy(update={"sql_used": [executed_sql] if executed_sql else []})

        logger.info(
            "Structured analysis generated for dataset %s: confidence=%.2f sql_used=%s",
            dataset_id,
            response.confidence,
            bool(executed_sql),
        )

        return {"final_analysis": response}

    return final_analysis_node


# ====================================================================== #
# Deterministic analysis workflow (Phase 6, extended to multi-step in
# Phase 7)
#
# Every node below takes WorkflowState and returns a partial update, same
# shape as every node above. What's different from the Phase 4/5 agent:
# there is no node that decides "should I call a tool" -- the sequence
# (analyze -> schema -> plan -> [generate -> validate -> execute ->
# validate step result] x1-5 -> synthesize -> chart -> respond) is fixed.
# The only runtime decisions are the route_after_* functions below, each
# choosing between "move forward," "retry the current step," or "give up
# on this step and synthesize whatever succeeded" (bounded by
# MAX_SQL_RETRIES per step and MAX_ANALYSIS_STEPS overall) -- see
# agent/graph.py for how they're wired as conditional edges.
# ====================================================================== #


def _format_schema_for_prompt(schema: DatasetSchema) -> str:
    """Render a DatasetSchema as plain 'name (TYPE)' lines for a prompt.

    Deliberately just column name + type -- not the table_name (an
    internal detail the model has no use for and should never see) and
    not sample rows (that would risk the model treating a handful of
    example rows as more representative than they are).

    The one exception is `sample_values`: for a low-cardinality text
    column, DuckDBService.get_schema() attaches its full, closed set of
    distinct values (never populated for anything high-cardinality, so
    never row-level/PII data -- see _distinct_values' docstring). Without
    it, two similarly-named columns that both look like "the state" but
    use different labels for the same place are indistinguishable from
    name + type alone, and the model has no way to know which one (or
    which exact value) actually matches the user's question.

    Column names are shown already double-quoted, exactly as they must be
    written in SQL. Real datasets have names like `State / City` or
    `Product Price`; unquoted, `State / City` parses as State divided by
    City, and smaller models in particular copy names verbatim.
    """

    lines = []
    for column in schema.columns:
        line = f"{quote_identifier(column.name)} ({column.type})"
        if column.sample_values:
            line += f" -- values: {', '.join(column.sample_values)}"
        lines.append(line)
    return "\n".join(lines)


def _current_step(state: WorkflowState) -> AnalysisStep:
    plan = state["analysis_plan"]
    index = state.get("current_step_index", 0)
    return plan.steps[index]


def build_analyze_question_node(structured_model: Runnable) -> Callable[[WorkflowState], dict]:
    """Build the 'analyze_question' node: understand intent, before the schema is known.

    Phase 7 addition: if `conversation_context` is present (a follow-up
    question on an existing thread_id), it's included so the model can
    interpret things like "what about the second highest?" -- see
    ConversationContext and agent/graph.py's checkpointer wiring for how
    that context reaches here across separate API requests.
    """

    def analyze_question_node(state: WorkflowState) -> dict:
        messages: list[BaseMessage] = [SystemMessage(content=QUESTION_ANALYSIS_PROMPT)]

        context = state.get("conversation_context")
        if context and context.previous_question:
            messages.append(
                SystemMessage(
                    content=(
                        "Context from the previous turn in this conversation:\n"
                        f"Previous question: {context.previous_question}\n"
                        f"Previous answer: {context.previous_answer}\n"
                        f"Relevant findings: {context.relevant_findings}\n\n"
                        "Use this only if the current question actually reads as a follow-up."
                    )
                )
            )
        messages.append(HumanMessage(content=state["user_question"]))

        analysis: QuestionAnalysis = structured_model.invoke(messages)

        logger.info(
            "Question analyzed (dataset_id=%s): intent=%s answerable=%s multi_step=%s chart=%s",
            state.get("dataset_id"),
            analysis.intent,
            analysis.is_answerable,
            analysis.requires_multiple_steps,
            analysis.suggested_chart_type,
        )
        return {"question_analysis": analysis}

    return analyze_question_node


def inspect_schema_node(state: WorkflowState) -> dict:
    """The 'inspect_schema' node: no LLM call, a direct Phase 3 service lookup.

    Reuses DuckDBService.get_schema() exactly as the GET
    /api/analysis/{dataset_id}/schema route does -- no DuckDB logic is
    reimplemented here, and only column names/types are exposed to later
    nodes' prompts, never the dataset's actual rows.
    """

    schema = get_duckdb_service().get_schema(state["dataset_id"])
    return {"dataset_schema": schema}


def build_create_analysis_plan_node(structured_model: Runnable) -> Callable[[WorkflowState], dict]:
    """Build the 'create_analysis_plan' node: ground the question against the real schema.

    Produces 1 step for a simple question, up to MAX_ANALYSIS_STEPS for a
    genuinely multi-part one. If the model over-produces, the plan is
    *capped* (not rejected) at Settings.max_analysis_steps -- "prefer
    simplifying the plan," per the spec, is a deterministic code decision
    here, not left to the model's discretion.
    """

    def create_analysis_plan_node(state: WorkflowState) -> dict:
        # `.get`, not subscript: MultiAgentState (Phase 8) reuses this node
        # as the Planner but has no question_analysis field at all -- the
        # Supervisor's SupervisorDecision is its equivalent triage step.
        analysis = state.get("question_analysis")
        schema = state["dataset_schema"]

        content = (
            f"User question: {state['user_question']}\n\n"
            f"Question analysis: {analysis.model_dump_json() if analysis else '(none)'}\n\n"
            "Dataset schema (only these columns exist -- never plan around any others):\n"
            f"{_format_schema_for_prompt(schema)}"
        )
        messages: list[BaseMessage] = [SystemMessage(content=ANALYSIS_PLANNING_PROMPT), HumanMessage(content=content)]
        if state.get("retry_reason"):
            messages.append(
                SystemMessage(
                    content=(
                        "The previous plan/query did not work. Reason:\n"
                        f"{state['retry_reason']}\n\n"
                        "Produce a corrected plan that addresses this."
                    )
                )
            )

        plan: AnalysisPlan = structured_model.invoke(messages)

        max_steps = get_settings().max_analysis_steps
        if len(plan.steps) > max_steps:
            logger.warning(
                "Analysis plan for dataset %s had %d steps; capping at %d",
                state.get("dataset_id"),
                len(plan.steps),
                max_steps,
            )
            plan = plan.model_copy(update={"steps": plan.steps[:max_steps]})

        logger.info(
            "Analysis plan created (dataset_id=%s): %d step(s) -- %s",
            state.get("dataset_id"),
            len(plan.steps),
            plan.objective,
        )
        # A fresh plan means step progress resets too -- relevant when
        # this node is re-entered from route_after_validate_step_result
        # (a step's result was insufficient and no single-step retry
        # could fix it, so we re-plan from scratch).
        return {
            "analysis_plan": plan,
            "current_step_index": 0,
            "completed_steps": [],
            "retry_reason": None,
            "retry_count": 0,
        }

    return create_analysis_plan_node


def build_generate_step_sql_node(structured_model: Runnable) -> Callable[[WorkflowState], dict]:
    """Build the 'generate_step_sql' node: turn the CURRENT step's objective into SQL.

    Unlike Phase 6's single-query generate_sql, this is step-aware: it
    reads `analysis_plan.steps[current_step_index]` rather than a single
    top-level plan, and if that step `depends_on` earlier steps, their
    actual results (from `executed_queries`) are included as grounding
    context -- e.g. so a "compare to step 1" step can reference step 1's
    real numbers instead of guessing them.
    """

    def generate_step_sql_node(state: WorkflowState) -> dict:
        plan = state["analysis_plan"]
        schema = state["dataset_schema"]
        step = _current_step(state)

        content_parts = [
            f"User question: {state['user_question']}",
            f"Overall analysis objective: {plan.objective}",
            f"Dataset schema (only these columns exist):\n{_format_schema_for_prompt(schema)}",
            f"Current step ({step.step_id} of {len(plan.steps)}): {step.objective}",
            "Refer to the dataset as table name 'dataset'.",
        ]
        if step.depends_on:
            prior = [q for q in state.get("executed_queries", []) if q.step_id in step.depends_on]
            if prior:
                lines = [f"  Step {q.step_id} result -- columns: {q.columns}, rows: {q.rows}" for q in prior]
                content_parts.append("Results from steps this one depends on:\n" + "\n".join(lines))

        messages: list[BaseMessage] = [
            SystemMessage(content=STEP_SQL_GENERATION_PROMPT),
            HumanMessage(content="\n\n".join(content_parts)),
        ]
        if state.get("generated_sql") and state.get("retry_reason"):
            messages.append(
                SystemMessage(
                    content=(
                        "Your previous attempt at this step failed. Previous SQL:\n"
                        f"{state['generated_sql']}\n\n"
                        f"Failure reason:\n{state['retry_reason']}\n\n"
                        "Correct the query -- do not repeat the same mistake."
                    )
                )
            )

        result: GeneratedSQL = structured_model.invoke(messages)
        retry_count = state.get("retry_count", 0) + 1

        logger.info(
            "SQL generated (dataset_id=%s, step=%d, attempt %d): %s",
            state.get("dataset_id"),
            step.step_id,
            retry_count,
            result.sql,
        )

        # Reset validated_sql/retry_reason: a fresh attempt starts with a
        # clean slate. validate_sql (next) is the only node allowed to set
        # validated_sql again; it will re-set retry_reason if this new
        # attempt also fails.
        return {
            "generated_sql": result.sql,
            "validated_sql": None,
            "retry_reason": None,
            "retry_count": retry_count,
        }

    return generate_step_sql_node


def validate_sql_node(state: WorkflowState) -> dict:
    """The 'validate_sql' node: pure Phase 3 validation, no LLM call, no DuckDB call.

    Unchanged from Phase 6 -- validate_query() doesn't care which step
    produced the SQL it's checking. Reuses utils.sql_utils.validate_query()
    exactly, the same function the Phase 3 /api/analysis/query endpoint
    and the Phase 4 execute_sql tool both use.
    """

    try:
        validate_query(state["generated_sql"])
    except QueryValidationError as exc:
        logger.info(
            "SQL validation failed (dataset_id=%s, attempt %d): %s",
            state.get("dataset_id"),
            state.get("retry_count", 0),
            exc,
        )
        return {"retry_reason": str(exc)}

    return {"validated_sql": state["generated_sql"]}


def route_after_validate_sql(state: WorkflowState) -> str:
    """Conditional edge: validate_sql -> execute_step_sql | generate_step_sql | synthesize."""

    if not state.get("retry_reason"):
        return "execute_step_sql"
    if state.get("retry_count", 0) >= get_settings().max_sql_retries:
        return "synthesize"
    return "generate_step_sql"


def execute_step_sql_node(state: WorkflowState) -> dict:
    """The 'execute_step_sql' node: run the current step's validated SQL via DuckDBService.

    Deliberately does NOT go through the LangChain execute_sql *tool*
    (agent/tools/analysis_tools.py) -- this workflow has no LLM deciding
    whether to call a tool, so there is nothing for the tool-calling
    machinery to add here. On success, the result is appended (as an
    ExecutedQuery, tagged with this step's step_id) to `executed_queries`
    -- the growing, real history that sql_used and chart data are built
    from, never overwritten like Phase 6's single `query_result` was.
    """

    settings = get_settings()
    step = _current_step(state)
    try:
        result = get_duckdb_service().execute_query(
            dataset_id=state["dataset_id"],
            query=state["validated_sql"],
            max_rows=settings.max_query_rows,
            timeout_seconds=settings.max_query_execution_seconds,
        )
    except DatasetServiceError as exc:
        logger.info(
            "SQL execution failed (dataset_id=%s, step=%d, attempt %d): %s",
            state.get("dataset_id"),
            step.step_id,
            state.get("retry_count", 0),
            exc,
        )
        return {"retry_reason": str(exc)}

    logger.info(
        "SQL executed (dataset_id=%s, step=%d): %d row(s) in %.1fms",
        state.get("dataset_id"),
        step.step_id,
        result.row_count,
        result.execution_time_ms,
    )

    executed_query = ExecutedQuery(
        step_id=step.step_id,
        sql=state["validated_sql"],
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        execution_time_ms=result.execution_time_ms,
    )
    return {
        "executed_queries": [*state.get("executed_queries", []), executed_query],
        "retry_reason": None,
    }


def route_after_execute_sql(state: WorkflowState) -> str:
    """Conditional edge: execute_step_sql -> validate_step_result | generate_step_sql | synthesize."""

    if not state.get("retry_reason"):
        return "validate_step_result"
    if state.get("retry_count", 0) >= get_settings().max_sql_retries:
        return "synthesize"
    return "generate_step_sql"


def build_validate_step_result_node(structured_model: Runnable) -> Callable[[WorkflowState], dict]:
    """Build the 'validate_step_result' node: does this step's result satisfy ITS objective?

    Separate concern from "does the whole original question have an
    answer yet" -- that's synthesis/generate_final_response's job, once
    all steps (or as many as succeeded) are in. On success, this node
    also *advances* the plan: increments current_step_index, records the
    finished step_id, and resets the per-step retry budget -- routing
    (route_after_validate_step_result) then just reads whether
    current_step_index is still within the plan's bounds.
    """

    def validate_step_result_node(state: WorkflowState) -> dict:
        step = _current_step(state)
        result = state["executed_queries"][-1]  # this step's just-executed result

        content = (
            f"Step objective: {step.objective}\n\n"
            f"SQL executed:\n{result.sql}\n\n"
            f"Result: {result.row_count} row(s)\n"
            f"Columns: {result.columns}\n"
            f"Rows: {result.rows}"
        )
        messages = [SystemMessage(content=STEP_RESULT_VALIDATION_PROMPT), HumanMessage(content=content)]

        validation: ResultValidation = structured_model.invoke(messages)
        logger.info(
            "Step result validated (dataset_id=%s, step=%d): sufficient=%s confidence=%.2f",
            state.get("dataset_id"),
            step.step_id,
            validation.is_sufficient,
            validation.confidence,
        )

        updates: dict = {"step_result_validation": validation}
        if validation.is_sufficient:
            current_index = state.get("current_step_index", 0)
            updates["completed_steps"] = [*state.get("completed_steps", []), step.step_id]
            updates["current_step_index"] = current_index + 1

            plan = state.get("analysis_plan")
            if plan and updates["current_step_index"] < len(plan.steps):
                # More steps remain -- fresh retry budget for the next one.
                updates["retry_count"] = 0
                updates["generated_sql"] = None
                updates["validated_sql"] = None
            # else: this was the last step. Deliberately leave retry_count/
            # generated_sql/validated_sql as they are -- they now reflect
            # how many attempts the final successful step took and what
            # its SQL was, which is more useful (e.g. in debug info) than
            # resetting to 0/None for a run that's actually finished.
        else:
            missing = ", ".join(validation.missing_information) or "unspecified"
            updates["retry_reason"] = f"{validation.reason} Missing: {missing}"
        return updates

    return validate_step_result_node


def route_after_validate_step_result(state: WorkflowState) -> str:
    """Conditional edge: validate_step_result -> generate_step_sql | synthesize.

    Three cases collapse to two targets: a sufficient result with more
    steps remaining advances to the next step's SQL generation; an
    insufficient result with retries left retries the SAME step's SQL
    generation -- both land on "generate_step_sql," just with different
    state already set up by validate_step_result_node. Only "no more
    steps" or "retries exhausted" route to synthesize.
    """

    validation = state.get("step_result_validation")
    plan = state.get("analysis_plan")

    if validation is not None and validation.is_sufficient:
        if plan and state.get("current_step_index", 0) < len(plan.steps):
            return "generate_step_sql"
        return "synthesize"
    if state.get("retry_count", 0) >= get_settings().max_sql_retries:
        return "synthesize"
    return "generate_step_sql"


def synthesize_node(state: WorkflowState) -> dict:
    """The 'synthesize' node: build a controlled, structured summary of every
    step's actual result -- deliberately NOT an LLM call. This is the
    "controlled representation" the spec asks for instead of blindly
    dumping every raw query result into the final prompt: plain
    formatting, grouped by step and labeled with that step's objective,
    nothing invented (there's no model here to invent anything).
    Reasoning over this (e.g. computing growth between two steps) is
    generate_final_response's job, once, rather than duplicated here.
    """

    plan = state.get("analysis_plan")
    queries = state.get("executed_queries", [])
    steps_by_id = {step.step_id: step for step in plan.steps} if plan else {}

    if not queries:
        return {"synthesis_context": "No step executed successfully."}

    blocks = []
    for query in queries:
        step = steps_by_id.get(query.step_id)
        objective = step.objective if step else f"step {query.step_id}"
        blocks.append(f"Step {query.step_id} ({objective}):\nColumns: {query.columns}\nRows: {query.rows}")

    return {"synthesis_context": "\n\n".join(blocks)}


def build_chart_data_node(state: WorkflowState) -> dict:
    """The 'build_chart_data' node: deterministically build ChartData (or None).

    The ONLY model influence on the chart is `suggested_chart_type`,
    decided earlier by analyze_question from the question's wording
    alone -- never from data. Every value in `data` here comes directly
    from an already-executed query's actual rows. If the suggested type
    exists but the data shape can't support a chart (e.g. a single
    scalar result), this node overrides the suggestion with None rather
    than trusting it blindly.
    """

    question_analysis = state.get("question_analysis")
    queries = state.get("executed_queries", [])
    plan = state.get("analysis_plan")

    chart_type = question_analysis.suggested_chart_type if question_analysis else None
    if not chart_type or not queries:
        return {"chart": None}

    result = queries[-1]
    if result.row_count < 1 or len(result.columns) < 2:
        # A single scalar (e.g. "total revenue: 500000") has no shape to chart.
        return {"chart": None}

    chart = ChartData(
        chart_type=chart_type,
        title=plan.objective if plan else state["user_question"],
        x_axis=result.columns[0],
        y_axis=result.columns[1],
        data=result.rows,
    )
    return {"chart": chart}


def build_generate_final_response_node(structured_model: Runnable) -> Callable[[WorkflowState], dict]:
    """Build the 'generate_final_response' node: produce the AnalysisResponse.

    Runs whether the workflow fully succeeded or gave up on a step (in
    the latter case, WORKFLOW_FINAL_RESPONSE_PROMPT instructs the model
    to say so honestly, grounded in whatever DID succeed, rather than
    guess). Exactly like Phase 5/6, sql_used and chart are unconditionally
    overwritten after the model replies -- never trusted from the model's
    own text:

    - sql_used lists every query that actually executed, in order --
      simpler than Phase 6's single-query "only if the result was also
      judged sufficient" gate, and deliberately so: a multi-step analysis
      routinely has some steps succeed even when the overall question
      couldn't be fully answered, and hiding that real, citable SQL would
      throw away genuine provenance. Empty only when nothing ever executed.
    - chart is whatever build_chart_data_node already computed (or None).

    Also updates `conversation_context` for the *next* turn -- a small,
    curated summary (not the full plan/queries/retries of this turn).
    """

    def generate_final_response_node(state: WorkflowState) -> dict:
        dataset_id = state.get("dataset_id")
        queries = state.get("executed_queries", [])
        plan = state.get("analysis_plan")

        context_parts = [f"User question: {state['user_question']}"]
        if state.get("question_analysis"):
            context_parts.append(f"Question analysis: {state['question_analysis'].model_dump_json()}")
        if plan:
            context_parts.append(f"Analysis plan objective: {plan.objective} ({len(plan.steps)} step(s))")
        context_parts.append(f"Step results:\n{state.get('synthesis_context') or 'No step executed successfully.'}")
        if state.get("step_result_validation"):
            context_parts.append(f"Last step's sufficiency judgment: {state['step_result_validation'].model_dump_json()}")
        if state.get("retry_reason"):
            context_parts.append(f"Final unresolved issue: {state['retry_reason']}")

        conversation_context = state.get("conversation_context")
        if conversation_context and conversation_context.previous_question:
            context_parts.append(
                "Earlier in this conversation:\n"
                f"Previous question: {conversation_context.previous_question}\n"
                f"Previous answer: {conversation_context.previous_answer}"
            )

        messages = [
            SystemMessage(content=WORKFLOW_FINAL_RESPONSE_PROMPT),
            HumanMessage(content="\n\n".join(context_parts)),
        ]

        try:
            response: AnalysisResponse = structured_model.invoke(messages)
        except Exception:
            logger.exception("Structured final response generation failed for dataset %s", dataset_id)
            raise

        # Deterministic overrides -- see docstring above.
        response = response.model_copy(
            update={"sql_used": [query.sql for query in queries], "chart": state.get("chart")}
        )

        new_context = ConversationContext(
            previous_question=state["user_question"],
            previous_answer=response.answer,
            relevant_findings=response.key_findings[:3],
        )

        logger.info(
            "Workflow finished (dataset_id=%s): confidence=%.2f retry_count=%d steps_executed=%d sql_used=%d",
            dataset_id,
            response.confidence,
            state.get("retry_count", 0),
            len(queries),
            len(response.sql_used),
        )

        return {"final_response": response, "conversation_context": new_context}

    return generate_final_response_node


# ====================================================================== #
# Phase 8: multi-agent architecture
#
# The Supervisor (agent/supervisor.py), Data Analyst (agent/data_analyst.py),
# and Reviewer (agent/reviewer.py) each get their own module -- see the
# project write-up for why those three specifically, and not
# agent/planner.py or agent/sql_analyst.py: the Planner IS
# build_create_analysis_plan_node above (reused unchanged, just with the
# new `complexity` field on its output), and the SQL Analyst is a
# subgraph built entirely from functions already in this file (see
# agent/subgraphs/sql_workflow.py) -- neither needed new node logic, only
# new wiring, so neither got a near-empty new file.
#
# The two small pieces below belong here because they're direct
# companions to what's already in this file: build_trivial_plan_node is
# the deterministic alternative to the Planner (build_create_analysis_plan_node)
# for questions that don't need real planning, and summarize_sql_analysis
# turns the SQL Analyst subgraph's raw executed_queries into the typed
# SQLAnalysisResult contract the Data Analyst and Reviewer both consume.
# ====================================================================== #


def build_trivial_plan_node() -> Callable[[MultiAgentState], dict]:
    """Build the 'build_trivial_plan' node: a deterministic, non-LLM
    alternative to the Planner for questions the Supervisor judged don't
    need real multi-step planning.

    The SQL Analyst subgraph structurally requires `analysis_plan` to
    exist (see SQLAnalystState) regardless of which path led to it -- this
    is what lets "Supervisor -> SQL Analyst" (skipping the Planner
    entirely, per the spec's own simple-question example) still work
    without SQL Analyst needing two different code paths for "has a plan"
    vs. "doesn't." No LLM call happens here; wrapping the raw question as
    a single step costs nothing extra.
    """

    def build_trivial_plan_node(state: MultiAgentState) -> dict:
        plan = AnalysisPlan(
            objective=state["user_question"],
            steps=[AnalysisStep(step_id=1, objective=state["user_question"])],
            complexity="simple",
        )
        logger.info("Trivial 1-step plan built (dataset_id=%s), no Planner call needed", state.get("dataset_id"))
        return {
            "current_agent": "planner",
            "analysis_plan": plan,
            "current_step_index": 0,
            "completed_steps": [],
            "retry_reason": None,
            "retry_count": 0,
        }

    return build_trivial_plan_node


def route_after_supervisor(state: MultiAgentState) -> str:
    """Decide what happens right after the Supervisor routes.

    is_answerable=False is the one case that skips the entire rest of the
    pipeline -- there is nothing for the SQL Analyst to query or the Data
    Analyst to interpret, so going straight to generate_final_response
    (which handles a missing `findings` by producing an honest "can't
    answer" response, no model call -- see
    build_multi_agent_final_response_node) is correct, not a shortcut
    that skips real work.
    """

    decision = state["supervisor_decision"]
    if not decision.is_answerable:
        return "generate_final_response"
    return "inspect_schema"


def route_after_schema(state: MultiAgentState) -> str:
    """Decide Planner vs. the deterministic trivial-plan shortcut.

    Mirrors SupervisorDecision.requires_planning directly -- the
    Supervisor already made this call; this just acts on it.
    """

    decision = state["supervisor_decision"]
    return "planner" if decision.requires_planning else "trivial_plan"


def summarize_sql_analysis(state: MultiAgentState) -> SQLAnalysisResult:
    """Build the SQL Analyst's output contract from its raw executed_queries.

    Shared by the Data Analyst and Reviewer (both need to see what SQL
    actually ran and what it returned) so there is exactly one place that
    knows how to turn ExecutedQuery history into a SQLAnalysisResult --
    not reimplemented in each agent.
    """

    queries = state.get("executed_queries", [])
    return SQLAnalysisResult(
        executed_queries=[query.sql for query in queries],
        results=[
            {"step_id": query.step_id, "row_count": query.row_count, "columns": query.columns, "rows": query.rows}
            for query in queries
        ],
        success=bool(queries),
        errors=[state["retry_reason"]] if state.get("retry_reason") and not queries else [],
    )


def build_multi_agent_chart_node(state: MultiAgentState) -> dict:
    """The multi-agent graph's chart-building node.

    Same "never invented, built only from real executed rows" rule as
    build_chart_data_node above, but there is no QuestionAnalysis in this
    graph (the Supervisor's SupervisorDecision doesn't pick a chart type --
    it wasn't asked to, per the spec's exact SupervisorDecision fields) to
    supply a suggested type. Rather than never charting at all, this
    defaults to "bar" whenever the last executed query's shape could
    plausibly support one, and to None otherwise -- still entirely
    determined by the real result shape, never by a model.
    """

    queries = state.get("executed_queries", [])
    if not queries:
        return {"chart": None}

    result = queries[-1]
    if result.row_count < 1 or len(result.columns) < 2:
        return {"chart": None}

    plan = state.get("analysis_plan")
    chart = ChartData(
        chart_type="bar",
        title=plan.objective if plan else state["user_question"],
        x_axis=result.columns[0],
        y_axis=result.columns[1],
        data=result.rows,
    )
    return {"chart": chart}


def build_multi_agent_final_response_node(structured_model: Runnable) -> Callable[[MultiAgentState], dict]:
    """Build the 'generate_final_response' node for the multi-agent graph.

    Distinct from build_generate_final_response_node (WorkflowState):
    this one restates already-reviewed-and-approved AnalyticalFindings
    (via MULTI_AGENT_FINAL_RESPONSE_PROMPT) rather than reasoning over
    synthesis_context from scratch -- the Data Analyst already did that
    reasoning, and the Reviewer (plus, if triggered, a human) already
    checked it. Same deterministic overrides as the workflow version:
    sql_used and chart are never trusted from the model.
    """

    def multi_agent_final_response_node(state: MultiAgentState) -> dict:
        dataset_id = state.get("dataset_id")
        queries = state.get("executed_queries", [])
        findings = state.get("findings")

        if findings is None:
            # The Supervisor judged this question unanswerable from this
            # dataset (SupervisorDecision.is_answerable = False) and
            # routed straight here -- no SQL ever ran, so there is
            # nothing for the Data Analyst/Reviewer to have produced.
            # Deterministic, no model call: there is nothing grounded to
            # restate, and asking the model to explain "why not" here
            # would risk it inventing dataset facts it was never given.
            reason = (
                state.get("supervisor_decision").reasoning
                if state.get("supervisor_decision")
                else "This question could not be answered from this dataset."
            )
            response = AnalysisResponse(
                answer=f"I can't answer this from the current dataset: {reason}",
                key_findings=[],
                metrics=[],
                sql_used=[],
                confidence=0.0,
                chart=None,
                follow_up_suggestions=[],
            )
            logger.info("Multi-agent question judged unanswerable (dataset_id=%s): %s", dataset_id, reason)
            return {"current_agent": "generate_final_response", "final_response": response}

        context_parts = [
            f"User question: {state['user_question']}",
            f"Approved findings:\n{findings.model_dump_json()}",
            f"Query results summary:\n{state.get('synthesis_context') or 'No step executed successfully.'}",
        ]
        if state.get("human_approved") is not None:
            context_parts.append(f"Human approval: {'approved' if state['human_approved'] else 'not required'}")

        conversation_context = state.get("conversation_context")
        if conversation_context and conversation_context.previous_question:
            context_parts.append(
                "Earlier in this conversation:\n"
                f"Previous question: {conversation_context.previous_question}\n"
                f"Previous answer: {conversation_context.previous_answer}"
            )

        messages = [
            SystemMessage(content=MULTI_AGENT_FINAL_RESPONSE_PROMPT),
            HumanMessage(content="\n\n".join(context_parts)),
        ]

        try:
            response: AnalysisResponse = structured_model.invoke(messages)
        except Exception:
            logger.exception("Multi-agent final response generation failed for dataset %s", dataset_id)
            raise

        response = response.model_copy(
            update={"sql_used": [query.sql for query in queries], "chart": state.get("chart")}
        )

        new_context = ConversationContext(
            previous_question=state["user_question"],
            previous_answer=response.answer,
            relevant_findings=findings.key_findings,
        )

        logger.info(
            "Multi-agent final response generated (dataset_id=%s, confidence=%.2f)", dataset_id, response.confidence
        )

        return {"current_agent": "generate_final_response", "final_response": response, "conversation_context": new_context}

    return multi_agent_final_response_node
