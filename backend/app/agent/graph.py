"""
LangGraph graph definitions and compilation.

Phase 1's graph (unchanged):

    START -> process_question -> END

Phase 4 added a second, tool-calling graph. Phase 5 extends it with a
structured-output stage that runs once the tool-calling loop is done:

    START -> agent -> tools_condition -> tools -> agent -> ... -> final_response -> END
                          |                                            ^
                          '-> (no tool call) ------------------------->'

`START` and `END` are LangGraph's special sentinel nodes marking entry and
exit points of the graph. Compiling a graph produces a runnable object
with `.invoke()` / `.ainvoke()`, which is what the API layer calls.

Two implementations of the tool-calling graph are kept side by side, on
purpose, per the learning goals for this phase:

- build_dataset_agent_graph(): built from LangGraph's own standard
  building blocks (StateGraph + ToolNode + tools_condition) rather than
  hand-rolling tool dispatch/routing. This is "the appropriate LangGraph
  mechanism" for tool execution, and it's what api/routes/analysis.py
  actually calls. The Phase 5 structured-output step is a fourth node
  here, "final_response" (see build_final_analysis_node in nodes.py).
- build_dataset_react_agent(): the same tools and system prompt, built
  instead with LangGraph's create_react_agent() prebuilt, which bundles
  the model-bind-tools-loop wiring above into one call -- and, as of
  Phase 5, its own built-in `response_format` for exactly the same
  structured-output step, done automatically. Kept as a documented,
  tested, side-by-side comparison -- see the project write-up for why
  create_agent() (a newer, differently-packaged API) isn't used here.

Phase 6 added a THIRD graph, not an agent at all, and Phase 7 extends it
from one SQL query to a plan of 1-5 steps:

    START -> analyze_question -> inspect_schema -> create_analysis_plan
           -> generate_step_sql -> validate_sql -[valid]-> execute_step_sql
                                       |                        |
                                  [invalid,                 [success,
                                   retries left]            retries left/
                                       |                     exhausted]
                                       v                        v
                              generate_step_sql       validate_step_result
                                       ^                    |         |
                                       |               [sufficient  [insufficient,
                                       |                + more       retries left]
                                       |                steps]           |
                                       '----------------'                v
                                       |                          (exhausted OR
                                       |                           no more steps)
                                       '------------------------------->|
                                                                         v
                                                                    synthesize
                                                                         |
                                                                         v
                                                                  build_chart_data
                                                                         |
                                                                         v
                                                               generate_final_response
                                                                         |
                                                                        END

A sufficient result with more plan steps remaining, and an insufficient
result with per-step retries remaining, both loop back to
generate_step_sql (see route_after_validate_step_result in nodes.py) --
they just arrive with different state already set up (next step vs.
same step retried). A simple question naturally produces a 1-step plan,
so it flows through this exact same graph with zero loop-backs -- there
is no separate "simple" graph path, per the design note in nodes.py.

build_analysis_workflow_graph() is what api/routes/analysis.py actually
calls now -- see the project write-up's "agent vs workflow" explanation
for why a fixed sequence with narrow, checked retry points is a better
fit for "answer analytical questions reliably" than an open-ended
tool-calling loop, even though both architectures remain in this
codebase (build_dataset_agent_graph is still fully tested on its own).

Phase 7 also adds a checkpointer (see build_analysis_workflow_graph),
which is what makes conversational follow-up questions possible: the
graph is compiled with an InMemorySaver, and api/routes/analysis.py
invokes it with a `thread_id` in the run config. See ConversationContext
in schemas/analysis.py and generate_final_response_node in nodes.py for
exactly what gets carried forward between turns (a small curated
summary, not the full raw state of the previous turn).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent, tools_condition
from langgraph.prebuilt.chat_agent_executor import AgentState as ReactAgentState

from app.agent.data_analyst import build_data_analyst_node
from app.agent.nodes import (
    AGENT_TOOLS,
    build_agent_node,
    build_analyze_question_node,
    build_chart_data_node,
    build_create_analysis_plan_node,
    build_final_analysis_node,
    build_generate_final_response_node,
    build_generate_step_sql_node,
    build_multi_agent_chart_node,
    build_multi_agent_final_response_node,
    build_tools_node,
    build_trivial_plan_node,
    build_validate_step_result_node,
    execute_step_sql_node,
    get_model_with_tools,
    get_structured_model,
    get_structured_model_for,
    inspect_schema_node,
    process_question,
    route_after_execute_sql,
    route_after_schema,
    route_after_supervisor,
    route_after_validate_step_result,
    route_after_validate_sql,
    synthesize_node,
    validate_sql_node,
)
from app.agent.prompts import ANALYST_SYSTEM_PROMPT
from app.agent.reviewer import build_human_approval_node, build_reviewer_node, route_after_human_approval, route_after_reviewer
from app.agent.state import AgentState, AnalystState, MultiAgentState, WorkflowState
from app.agent.subgraphs.sql_workflow import build_sql_analyst_subgraph
from app.agent.supervisor import build_supervisor_node
from app.core.llm import get_chat_model
from app.schemas.analysis import (
    AnalysisPlan,
    AnalysisResponse,
    AnalyticalFindings,
    GeneratedSQL,
    QuestionAnalysis,
    ResultValidation,
    SupervisorDecision,
)
from app.schemas.review import ReviewResult


def build_graph() -> CompiledStateGraph:
    """Construct and compile the Phase 1 agent graph."""

    graph = StateGraph(AnalystState)
    graph.add_node("process_question", process_question)
    graph.add_edge(START, "process_question")
    graph.add_edge("process_question", END)
    return graph.compile()


@lru_cache
def get_agent_graph() -> CompiledStateGraph:
    """Return the cached, compiled Phase 1 agent graph (compiled once per process)."""

    return build_graph()


# ---------------------------------------------------------------------- #
# Phase 4: tool-calling graph, built from LangGraph's standard primitives
# ---------------------------------------------------------------------- #


def build_dataset_agent_graph(
    model_with_tools: BaseChatModel | None = None,
    structured_model: Runnable | None = None,
) -> CompiledStateGraph:
    """Construct and compile the tool-calling + structured-output analyst graph.

    `model_with_tools` and `structured_model` are optional so tests can
    pass fakes for either or both (see tests/test_agent_graph.py) instead
    of the real OpenAI-backed ones; production code (get_dataset_agent_graph)
    leaves both unset and gets the real, cached models -- two separately
    configured runnables built from the *same* get_chat_model(), not two
    different models.

    Routing: `tools_condition` is a LangGraph-provided function that
    inspects the agent node's latest AIMessage -- if it has `tool_calls`,
    route to "tools"; otherwise, instead of routing straight to END like
    Phase 4 did, it's remapped here to "final_response". `ToolNode`
    (wrapped by build_tools_node -- see nodes.py) is the LangGraph-provided
    node that executes whichever tool(s) the model requested (matching
    each tool_call's name/args to one of AGENT_TOOLS, including resolving
    InjectedState arguments from graph state) and appends a ToolMessage
    per call. None of this dispatch/routing is hand-rolled -- it's "the
    appropriate LangGraph mechanism" instead of manually re-parsing
    tool_calls and re-implementing routing.
    """

    tool_model = model_with_tools if model_with_tools is not None else get_model_with_tools()
    final_model = structured_model if structured_model is not None else get_structured_model()

    graph = StateGraph(AgentState)
    graph.add_node("agent", build_agent_node(tool_model))
    graph.add_node("tools", build_tools_node())
    graph.add_node("final_response", build_final_analysis_node(final_model))

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: "final_response"})
    graph.add_edge("tools", "agent")
    graph.add_edge("final_response", END)

    return graph.compile()


@lru_cache
def get_dataset_agent_graph() -> CompiledStateGraph:
    """Return the cached, compiled tool-calling agent graph."""

    return build_dataset_agent_graph()


# ---------------------------------------------------------------------- #
# Phase 4: the same agent, built with create_react_agent() instead
# ---------------------------------------------------------------------- #


class ReactAnalystState(ReactAgentState):
    """create_react_agent's own state (messages + its internal step-limit
    bookkeeping) extended with dataset_id, so InjectedState("dataset_id")
    resolves the same way it does in build_dataset_agent_graph.

    `structured_response` is required by create_react_agent whenever
    `response_format` is passed -- see build_dataset_react_agent below.
    """

    dataset_id: str
    structured_response: AnalysisResponse


def build_dataset_react_agent(model: BaseChatModel | None = None) -> CompiledStateGraph:
    """The create_react_agent() equivalent of build_dataset_agent_graph().

    Unlike build_dataset_agent_graph, this is given the *unbound* model --
    create_react_agent calls bind_tools(tools) internally itself, as part
    of what it sets up for you. See the "CREATE_AGENT" explanation in the
    project write-up for exactly what else it does under the hood.

    `response_format=AnalysisResponse` is create_react_agent's own
    built-in equivalent of build_final_analysis_node: once the tool loop
    ends, it makes one further model.with_structured_output(AnalysisResponse)
    call for you and returns the result under result["structured_response"].
    Note this prebuilt path does NOT get the deterministic sql_used
    override that build_dataset_agent_graph performs -- that's a
    project-specific correctness rule, not something the generic
    prebuilt knows about, which is one concrete reason api/routes/analysis.py
    calls build_dataset_agent_graph rather than this variant.
    """

    chat_model = model if model is not None else get_chat_model()

    return create_react_agent(
        chat_model,
        AGENT_TOOLS,
        state_schema=ReactAnalystState,
        prompt=ANALYST_SYSTEM_PROMPT,
        response_format=AnalysisResponse,
    )


@lru_cache
def get_dataset_react_agent() -> CompiledStateGraph:
    """Return the cached, compiled create_react_agent() variant."""

    return build_dataset_react_agent()


# ---------------------------------------------------------------------- #
# Phase 6: deterministic SQL workflow (not an agent -- see module docstring)
# ---------------------------------------------------------------------- #


def build_analysis_workflow_graph(
    question_model: Runnable | None = None,
    planning_model: Runnable | None = None,
    sql_model: Runnable | None = None,
    result_model: Runnable | None = None,
    final_model: Runnable | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Construct and compile the deterministic multi-step analysis workflow graph.

    Five separate structured-output configurations of the same
    get_chat_model() -- one per stage that needs the model at all
    (analyze_question, create_analysis_plan, generate_step_sql,
    validate_step_result, generate_final_response). inspect_schema,
    validate_sql, execute_step_sql, synthesize, and build_chart_data make
    no model call whatsoever; they're plain Python calling existing
    services/utilities or just formatting already-real data. Each
    `*_model` parameter is optional so tests can substitute a fake for
    any subset of stages (see tests/test_workflow_graph.py) -- production
    code (get_analysis_workflow_graph) leaves all five unset and gets the
    real, cached, per-schema models.

    `checkpointer` defaults to a fresh InMemorySaver() per call, so tests
    get an isolated one automatically; production code
    (get_analysis_workflow_graph) is itself @lru_cache'd, so the SAME
    compiled graph -- and therefore the SAME checkpointer instance -- is
    reused across requests for the life of the process. That sharing is
    what makes thread_id-based conversation continuity actually work
    across separate HTTP calls; a fresh checkpointer per request would
    remember nothing.

    Conditional edges (route_after_validate_sql/execute_sql/
    validate_step_result, all in nodes.py) are what implement the retry
    loop -- see their docstrings and state.py's WorkflowState docstring
    for exactly how retry_count/retry_reason make it provably finite
    (bounded by Settings.max_sql_retries per step, Settings.max_analysis_steps
    overall).
    """

    question_model = question_model if question_model is not None else get_structured_model_for(QuestionAnalysis)
    planning_model = planning_model if planning_model is not None else get_structured_model_for(AnalysisPlan)
    sql_model = sql_model if sql_model is not None else get_structured_model_for(GeneratedSQL)
    result_model = result_model if result_model is not None else get_structured_model_for(ResultValidation)
    final_model = final_model if final_model is not None else get_structured_model_for(AnalysisResponse)
    checkpointer = checkpointer if checkpointer is not None else InMemorySaver()

    graph = StateGraph(WorkflowState)
    graph.add_node("analyze_question", build_analyze_question_node(question_model))
    graph.add_node("inspect_schema", inspect_schema_node)
    graph.add_node("create_analysis_plan", build_create_analysis_plan_node(planning_model))
    graph.add_node("generate_step_sql", build_generate_step_sql_node(sql_model))
    graph.add_node("validate_sql", validate_sql_node)
    graph.add_node("execute_step_sql", execute_step_sql_node)
    graph.add_node("validate_step_result", build_validate_step_result_node(result_model))
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("build_chart_data", build_chart_data_node)
    graph.add_node("generate_final_response", build_generate_final_response_node(final_model))

    graph.add_edge(START, "analyze_question")
    graph.add_edge("analyze_question", "inspect_schema")
    graph.add_edge("inspect_schema", "create_analysis_plan")
    graph.add_edge("create_analysis_plan", "generate_step_sql")
    graph.add_edge("generate_step_sql", "validate_sql")

    graph.add_conditional_edges(
        "validate_sql",
        route_after_validate_sql,
        {"execute_step_sql": "execute_step_sql", "generate_step_sql": "generate_step_sql", "synthesize": "synthesize"},
    )
    graph.add_conditional_edges(
        "execute_step_sql",
        route_after_execute_sql,
        {
            "validate_step_result": "validate_step_result",
            "generate_step_sql": "generate_step_sql",
            "synthesize": "synthesize",
        },
    )
    graph.add_conditional_edges(
        "validate_step_result",
        route_after_validate_step_result,
        {"generate_step_sql": "generate_step_sql", "synthesize": "synthesize"},
    )

    graph.add_edge("synthesize", "build_chart_data")
    graph.add_edge("build_chart_data", "generate_final_response")
    graph.add_edge("generate_final_response", END)

    return graph.compile(checkpointer=checkpointer)


@lru_cache
def get_analysis_workflow_graph() -> CompiledStateGraph:
    """Return the cached, compiled multi-step analysis workflow graph.

    This is what api/routes/analysis.py's POST /api/analysis actually
    invokes -- build_dataset_agent_graph (Phase 4/5) remains fully
    functional and tested, but is no longer the endpoint's production
    path (see the module docstring's "agent vs workflow" note). Cached
    via @lru_cache specifically so its checkpointer persists across
    requests -- see build_analysis_workflow_graph's docstring.
    """

    return build_analysis_workflow_graph()


# ---------------------------------------------------------------------- #
# Phase 8: multi-agent architecture + human-in-the-loop approval
#
#     START -> supervisor -[not answerable]-------------------------.
#                  |                                                 |
#           [answerable]                                             |
#                  v                                                 |
#            inspect_schema                                         |
#                  |                                                 |
#        [requires_planning]  [else]                                 |
#              v         v                                           |
#           planner   trivial_plan                                   |
#                  \      /                                          |
#                   v    v                                           |
#              sql_analyst (subgraph: generate/validate/execute/     |
#                            retry SQL -- see subgraphs/sql_workflow) |
#                  |                                                  |
#             synthesize -> build_chart_data                         |
#                  |                                                  |
#                  v                                                  |
#             data_analyst  <-----------------------.                |
#                  |                                 |                |
#                  v                                 |                |
#              reviewer                              |                |
#            /    |     \                            |                |
#   [rejected, [approved  [rejected, retries          |                |
#    retries   or low     exhausted OR low            |                |
#    left]     conf./high conf./expensive]            |                |
#      |       cost -> OK]        |                   |                |
#      '----------------.         v                   |                |
#                        |    human_approval           |                |
#                        |     (interrupt)             |                |
#                        |      /        \             |                |
#                        |  [approved] [rejected] -----'                |
#                        v      |                                       |
#                 generate_final_response <---------------------------- '
#                        |
#                       END
#
# The Planner (build_create_analysis_plan_node) and SQL Analyst
# (build_sql_analyst_subgraph) are Phase 6/7 code reused unchanged --
# see their own docstrings. synthesize/build_multi_agent_chart_node are
# non-LLM, same as the workflow graph. The five NEW pieces of reasoning
# are supervisor, trivial_plan (deterministic, not really "reasoning"),
# data_analyst, reviewer, and human_approval.
# ---------------------------------------------------------------------- #


def build_multi_agent_graph(
    supervisor_model: Runnable | None = None,
    planning_model: Runnable | None = None,
    sql_model: Runnable | None = None,
    result_model: Runnable | None = None,
    data_analyst_model: Runnable | None = None,
    reviewer_model: Runnable | None = None,
    final_model: Runnable | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Construct and compile the Phase 8 multi-agent analytical graph.

    Seven separate structured-output configurations of get_chat_model()
    -- one per stage that actually calls a model (supervisor, planner,
    generate_step_sql, validate_step_result, data_analyst, reviewer,
    generate_final_response). Every `*_model` parameter is optional, same
    pattern as build_analysis_workflow_graph, so tests can inject
    FakeStructuredModel for any subset of agents (see
    tests/test_multi_agent_graph.py) while production code
    (get_multi_agent_graph) leaves all unset and gets real, cached,
    per-schema models.

    `checkpointer` defaults to a fresh InMemorySaver() per call for test
    isolation; get_multi_agent_graph is itself @lru_cache'd so production
    reuses one graph -- and therefore one checkpointer -- for the life of
    the process, exactly like build_analysis_workflow_graph. This is what
    makes the human-in-the-loop resume (POST /api/analysis/{thread_id}/approve)
    and multi-turn conversation continuity both work: the SAME checkpointer
    instance must see both the original run and the later resume.

    The SQL Analyst subgraph is compiled separately (build_sql_analyst_subgraph,
    no checkpointer of its own -- see that module's docstring) and added
    here as a single node; LangGraph shares SQLAnalystState's fields with
    this graph's MultiAgentState automatically.
    """

    supervisor_model = supervisor_model if supervisor_model is not None else get_structured_model_for(SupervisorDecision)
    planning_model = planning_model if planning_model is not None else get_structured_model_for(AnalysisPlan)
    sql_model = sql_model if sql_model is not None else get_structured_model_for(GeneratedSQL)
    result_model = result_model if result_model is not None else get_structured_model_for(ResultValidation)
    data_analyst_model = (
        data_analyst_model if data_analyst_model is not None else get_structured_model_for(AnalyticalFindings)
    )
    reviewer_model = reviewer_model if reviewer_model is not None else get_structured_model_for(ReviewResult)
    final_model = final_model if final_model is not None else get_structured_model_for(AnalysisResponse)
    checkpointer = checkpointer if checkpointer is not None else InMemorySaver()

    sql_analyst_subgraph = build_sql_analyst_subgraph(sql_model=sql_model, result_model=result_model)

    graph = StateGraph(MultiAgentState)
    graph.add_node("supervisor", build_supervisor_node(supervisor_model))
    # input=MultiAgentState is required on every node reused directly from
    # nodes.py that's still type-hinted `state: WorkflowState` --
    # LangGraph infers a node's input schema from that hint when `input`
    # is omitted (confirmed by reading langgraph.graph.state.StateGraph.add_node),
    # and WorkflowState doesn't declare supervisor_decision/findings/review/etc,
    # which silently drops those keys from the state a *subsequent*
    # conditional edge reads. Nodes already typed `state: MultiAgentState`
    # (supervisor, trivial_plan, data_analyst, reviewer, human_approval,
    # build_multi_agent_chart_node, generate_final_response) don't need this.
    graph.add_node("inspect_schema", inspect_schema_node, input=MultiAgentState)
    graph.add_node("planner", build_create_analysis_plan_node(planning_model), input=MultiAgentState)
    graph.add_node("trivial_plan", build_trivial_plan_node())
    graph.add_node("sql_analyst", sql_analyst_subgraph)
    graph.add_node("synthesize", synthesize_node, input=MultiAgentState)
    graph.add_node("build_chart_data", build_multi_agent_chart_node)
    graph.add_node("data_analyst", build_data_analyst_node(data_analyst_model))
    graph.add_node("reviewer", build_reviewer_node(reviewer_model))
    graph.add_node("human_approval", build_human_approval_node())
    graph.add_node("generate_final_response", build_multi_agent_final_response_node(final_model))

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {"inspect_schema": "inspect_schema", "generate_final_response": "generate_final_response"},
    )
    graph.add_conditional_edges(
        "inspect_schema", route_after_schema, {"planner": "planner", "trivial_plan": "trivial_plan"}
    )
    graph.add_edge("planner", "sql_analyst")
    graph.add_edge("trivial_plan", "sql_analyst")
    graph.add_edge("sql_analyst", "synthesize")
    graph.add_edge("synthesize", "build_chart_data")
    graph.add_edge("build_chart_data", "data_analyst")
    graph.add_edge("data_analyst", "reviewer")

    graph.add_conditional_edges(
        "reviewer",
        route_after_reviewer,
        {
            "data_analyst": "data_analyst",
            "human_approval": "human_approval",
            "generate_final_response": "generate_final_response",
        },
    )
    graph.add_conditional_edges(
        "human_approval",
        route_after_human_approval,
        {"generate_final_response": "generate_final_response", "planner": "planner"},
    )
    graph.add_edge("generate_final_response", END)

    return graph.compile(checkpointer=checkpointer)


@lru_cache
def get_multi_agent_graph() -> CompiledStateGraph:
    """Return the cached, compiled Phase 8 multi-agent graph.

    Cached via @lru_cache for the same reason as get_analysis_workflow_graph:
    the checkpointer must persist across requests, both for ordinary
    multi-turn conversation and for the human-in-the-loop approve step,
    which resumes the SAME run on a LATER request.
    """

    return build_multi_agent_graph()


# ---------------------------------------------------------------------- #
# Phase 9: a persistent (Postgres-backed) checkpointer, replacing
# InMemorySaver so a thread_id survives an application/worker/container
# restart -- see main.py's lifespan (API process) and
# workers/analysis_worker.py (Celery worker process) for the two places
# this is used. `InMemorySaver` remains the fallback whenever
# Settings.database_url isn't configured -- this is exactly what keeps
# every Phase 1-8 test and the local dev experience unchanged when no
# Postgres is set up: get_multi_agent_graph() (InMemorySaver, @lru_cache)
# is untouched, and is what tests/dependency_overrides still target.
# ---------------------------------------------------------------------- #


def _psycopg_conn_string(database_url: str) -> str:
    """AsyncPostgresSaver wants a plain psycopg conninfo string
    ('postgresql://...'), not SQLAlchemy's driver-qualified
    'postgresql+psycopg://...' -- strip the '+psycopg' part."""

    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@asynccontextmanager
async def checkpointer_context(database_url: str | None) -> AsyncIterator[BaseCheckpointSaver]:
    """Async context manager yielding the checkpointer to use for the
    lifetime of one caller (the FastAPI app's lifespan, or one Celery
    task's `asyncio.run()` scope).

    Postgres-backed when `database_url` points at Postgres: opens an
    AsyncPostgresSaver connection, calls `.setup()` once (creates the
    checkpoint tables if they don't exist yet -- LangGraph's own internal
    migration, separate from and in addition to Alembic's application
    schema), and closes the connection when the caller's `async with`
    block exits.

    Without Postgres (local SQLite mode) it yields ONE process-wide
    InMemorySaver rather than a fresh one per call. A human-approval
    pause and its resume run as two separate jobs; with a fresh saver per
    call the resume found no checkpoint and LangGraph failed with "Must
    write to at least one of [...]". Without Redis, jobs run in the API
    process, so a shared saver lets them find each other's state. It is
    still not durable: a backend restart loses paused analyses (the
    worker reports that clearly instead of the LangGraph error).
    """

    if database_url and database_url.startswith("postgresql"):
        async with AsyncPostgresSaver.from_conn_string(_psycopg_conn_string(database_url)) as checkpointer:
            await checkpointer.setup()
            yield checkpointer
    else:
        yield _process_memory_saver()


@lru_cache
def _process_memory_saver() -> InMemorySaver:
    """The single in-process checkpointer used when Postgres isn't configured."""

    return InMemorySaver()
