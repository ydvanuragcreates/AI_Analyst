"""
The SQL Analyst agent, implemented as a LangGraph subgraph.

This is the literal "reuse the existing Phase 6 SQL workflow, do not
duplicate SQL validation logic" instruction: every node and routing
function below is imported directly from agent/nodes.py, unchanged --
build_generate_step_sql_node, validate_sql_node, execute_step_sql_node,
build_validate_step_result_node, and their three route_after_* functions
are the exact same Phase 6/7 code, not reimplementations. This module
only supplies NEW wiring: a small, self-contained graph over
SQLAnalystState (see agent/state.py) that loops through however many
steps `analysis_plan` has, generating/validating/executing/retrying SQL
per step, and exits when either every step succeeds or one exhausts its
retry budget.

Compiled as a plain StateGraph -- NOT with its own checkpointer. When
added as a node in the parent multi-agent graph (see agent/graph.py),
LangGraph automatically shares every field SQLAnalystState declares with
the parent's MultiAgentState (verified empirically: overlapping keys
flow in and back out; fields only one side declares pass through
untouched), and the PARENT's checkpointer covers the whole run -- a
subgraph checkpointer here would be redundant and is the wrong pattern.
"""

from functools import lru_cache

from langchain_core.runnables import Runnable
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.nodes import (
    build_generate_step_sql_node,
    build_validate_step_result_node,
    execute_step_sql_node,
    get_structured_model_for,
    route_after_execute_sql,
    route_after_validate_sql,
    route_after_validate_step_result,
    validate_sql_node,
)
from app.agent.state import SQLAnalystState
from app.schemas.analysis import GeneratedSQL, ResultValidation


def build_sql_analyst_subgraph(
    sql_model: Runnable | None = None,
    result_model: Runnable | None = None,
) -> CompiledStateGraph:
    """Construct and compile the SQL Analyst subgraph.

    `sql_model`/`result_model` are optional so tests can inject fakes
    (see tests/test_sql_analyst_subgraph.py), matching the same pattern
    used throughout agent/graph.py.

    Routing note: route_after_validate_sql/route_after_execute_sql/
    route_after_validate_step_result all return the *logical* string
    "synthesize" as their "give up, move on" signal (that's what they're
    called for in the Phase 6/7 parent workflow). Reused here unchanged,
    their path_map just sends that same signal to END instead -- this
    subgraph has no synthesize node of its own; synthesis is the Data
    Analyst's job, outside this subgraph entirely.
    """

    sql_model = sql_model if sql_model is not None else get_structured_model_for(GeneratedSQL)
    result_model = result_model if result_model is not None else get_structured_model_for(ResultValidation)

    graph = StateGraph(SQLAnalystState)
    graph.add_node("generate_step_sql", build_generate_step_sql_node(sql_model))
    graph.add_node("validate_sql", validate_sql_node)
    graph.add_node("execute_step_sql", execute_step_sql_node)
    graph.add_node("validate_step_result", build_validate_step_result_node(result_model))

    graph.add_edge(START, "generate_step_sql")
    graph.add_edge("generate_step_sql", "validate_sql")

    graph.add_conditional_edges(
        "validate_sql",
        route_after_validate_sql,
        {"execute_step_sql": "execute_step_sql", "generate_step_sql": "generate_step_sql", "synthesize": END},
    )
    graph.add_conditional_edges(
        "execute_step_sql",
        route_after_execute_sql,
        {"validate_step_result": "validate_step_result", "generate_step_sql": "generate_step_sql", "synthesize": END},
    )
    graph.add_conditional_edges(
        "validate_step_result",
        route_after_validate_step_result,
        {"generate_step_sql": "generate_step_sql", "synthesize": END},
    )

    return graph.compile()


@lru_cache
def get_sql_analyst_subgraph() -> CompiledStateGraph:
    """Return the cached, compiled SQL Analyst subgraph (real, cached models)."""

    return build_sql_analyst_subgraph()
