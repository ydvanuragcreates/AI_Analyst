"""
Tests for the tool-calling + structured-output graph
(app/agent/graph.py::build_dataset_agent_graph) using fake models for
both stages -- no real OpenAI calls.

The tool-calling model's responses are scripted with
FakeMessagesListChatModel (as in Phase 4); the structured-output stage
uses FakeStructuredModel (see conftest.py), since with_structured_output()
isn't implemented on LangChain's fake chat models. These tests verify
LangGraph's routing (agent -> tools -> agent -> final_response -> END)
and real tool execution against real DuckDB -- not what a live LLM
decides to do, which is inherently non-deterministic and belongs to
manual testing (see tests/manual_tool_call.py, run with a real API key).
"""

import uuid

import pandas as pd
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.graph import build_dataset_agent_graph, build_dataset_react_agent
from app.schemas.analysis import AnalysisResponse
from app.services.duckdb_service import get_duckdb_service
from tests.conftest import FakeStructuredModel


@pytest.fixture
def registered_dataset():
    service = get_duckdb_service()
    dataset_id = str(uuid.uuid4())
    dataframe = pd.DataFrame(
        {"region": ["Delhi", "Mumbai", "Delhi"], "revenue": [100.0, 200.0, 50.0]}
    )
    service.register_dataset(dataset_id, dataframe)
    yield dataset_id
    service.unregister_dataset(dataset_id)


def _tool_call(name: str, args: dict, call_id: str) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _fake_final_response(**overrides) -> AnalysisResponse:
    defaults = {
        "answer": "Mumbai has the highest revenue.",
        "key_findings": ["Mumbai's total revenue is the highest of all regions."],
        "metrics": [],
        "sql_used": ["THIS SHOULD BE OVERWRITTEN"],  # proves the graph, not the model, controls this field
        "confidence": 0.9,
    }
    defaults.update(overrides)
    return AnalysisResponse(**defaults)


def test_agent_executes_tool_then_produces_structured_final_analysis(registered_dataset) -> None:
    scripted = [
        AIMessage(
            content="",
            tool_calls=[
                _tool_call(
                    "execute_sql",
                    {
                        "query": "SELECT region, SUM(revenue) AS total FROM dataset "
                        "GROUP BY region ORDER BY total DESC LIMIT 1"
                    },
                    "call_1",
                )
            ],
        ),
        AIMessage(content=""),  # no tool_calls -> routes to final_response, not straight to END
    ]
    tool_model = FakeMessagesListChatModel(responses=scripted)
    structured_model = FakeStructuredModel(_fake_final_response())
    graph = build_dataset_agent_graph(model_with_tools=tool_model, structured_model=structured_model)

    result = graph.invoke(
        {"messages": [HumanMessage(content="Which region has highest revenue?")], "dataset_id": registered_dataset}
    )

    # Tool-calling mechanics (unchanged from Phase 4): the ToolMessage
    # carries the real query result.
    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "Mumbai" in tool_message.content

    # Phase 5: a validated AnalysisResponse, not raw text, is the result.
    final = result["final_analysis"]
    assert isinstance(final, AnalysisResponse)
    assert final.answer == "Mumbai has the highest revenue."


def test_sql_used_is_overridden_with_actual_executed_query(registered_dataset) -> None:
    """The model's own sql_used guess must never survive -- only the real
    executed_sql captured from the tool result is allowed through."""

    real_query = "SELECT region, SUM(revenue) AS total FROM dataset GROUP BY region"
    scripted = [
        AIMessage(content="", tool_calls=[_tool_call("execute_sql", {"query": real_query}, "call_1")]),
        AIMessage(content=""),
    ]
    tool_model = FakeMessagesListChatModel(responses=scripted)
    structured_model = FakeStructuredModel(_fake_final_response(sql_used=["SELECT * FROM made_up_table"]))
    graph = build_dataset_agent_graph(model_with_tools=tool_model, structured_model=structured_model)

    result = graph.invoke({"messages": [HumanMessage(content="hi")], "dataset_id": registered_dataset})

    assert result["final_analysis"].sql_used == [real_query]
    assert result["executed_sql"] == real_query


def test_sql_used_is_empty_when_no_query_was_executed(registered_dataset) -> None:
    """If the agent answers without calling execute_sql, sql_used must be
    an empty list -- even if the model's structured reply hallucinates one."""

    tool_model = FakeMessagesListChatModel(responses=[AIMessage(content="Hello, how can I help?")])
    structured_model = FakeStructuredModel(_fake_final_response(sql_used=["SELECT 1"], confidence=0.5))
    graph = build_dataset_agent_graph(model_with_tools=tool_model, structured_model=structured_model)

    result = graph.invoke({"messages": [HumanMessage(content="hi")], "dataset_id": registered_dataset})

    assert result["final_analysis"].sql_used == []
    assert result.get("executed_sql") is None


def test_agent_supports_multiple_sequential_tool_calls(registered_dataset) -> None:
    scripted = [
        AIMessage(content="", tool_calls=[_tool_call("get_dataset_schema", {}, "call_1")]),
        AIMessage(
            content="",
            tool_calls=[_tool_call("execute_sql", {"query": "SELECT * FROM dataset LIMIT 1"}, "call_2")],
        ),
        AIMessage(content=""),
    ]
    tool_model = FakeMessagesListChatModel(responses=scripted)
    structured_model = FakeStructuredModel(_fake_final_response(answer="Final answer after two tool calls."))
    graph = build_dataset_agent_graph(model_with_tools=tool_model, structured_model=structured_model)

    result = graph.invoke({"messages": [HumanMessage(content="hi")], "dataset_id": registered_dataset})

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 2
    assert result["final_analysis"].answer == "Final answer after two tool calls."
    # The *last* successful execute_sql call is what gets captured.
    assert result["executed_sql"] == "SELECT * FROM dataset LIMIT 1"


def test_dataset_id_from_state_reaches_the_tool(registered_dataset) -> None:
    scripted = [
        AIMessage(
            content="", tool_calls=[_tool_call("execute_sql", {"query": "SELECT count(*) AS n FROM dataset"}, "call_1")]
        ),
        AIMessage(content=""),
    ]
    tool_model = FakeMessagesListChatModel(responses=scripted)
    structured_model = FakeStructuredModel(_fake_final_response())
    graph = build_dataset_agent_graph(model_with_tools=tool_model, structured_model=structured_model)

    result = graph.invoke({"messages": [HumanMessage(content="how many rows?")], "dataset_id": registered_dataset})

    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert '"n": 3' in tool_message.content


def test_agent_survives_a_tool_error_and_still_reaches_final_analysis(registered_dataset) -> None:
    scripted = [
        AIMessage(content="", tool_calls=[_tool_call("execute_sql", {"query": "DROP TABLE dataset"}, "call_1")]),
        AIMessage(content=""),
    ]
    tool_model = FakeMessagesListChatModel(responses=scripted)
    structured_model = FakeStructuredModel(
        _fake_final_response(answer="I couldn't run that query safely.", sql_used=["ignored"])
    )
    graph = build_dataset_agent_graph(model_with_tools=tool_model, structured_model=structured_model)

    result = graph.invoke({"messages": [HumanMessage(content="drop the table")], "dataset_id": registered_dataset})

    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "error" in tool_message.content
    assert result["final_analysis"].answer == "I couldn't run that query safely."
    # The failed execute_sql call never counted as "executed" -- no query
    # succeeded, so sql_used must still end up empty.
    assert result["final_analysis"].sql_used == []


def test_structured_output_failure_propagates_instead_of_being_swallowed(registered_dataset) -> None:
    """If the structured-output stage itself fails (e.g. a genuine
    validation error the model's reply couldn't satisfy), the graph must
    raise -- not silently return a broken/partial AnalysisResponse. The
    FastAPI route's existing generic exception handler is what turns this
    into a clean 500 (see test_analysis_agent_api.py's error-handling
    tests, which reuse the same established Phase 1 pattern)."""

    class FailingStructuredModel:
        def invoke(self, messages):
            raise ValueError("simulated structured-output validation failure")

    tool_model = FakeMessagesListChatModel(responses=[AIMessage(content="Hello!")])
    graph = build_dataset_agent_graph(model_with_tools=tool_model, structured_model=FailingStructuredModel())

    with pytest.raises(ValueError, match="simulated structured-output validation failure"):
        graph.invoke({"messages": [HumanMessage(content="hi")], "dataset_id": registered_dataset})


def test_no_tool_call_routes_directly_to_final_response(registered_dataset) -> None:
    tool_model = FakeMessagesListChatModel(responses=[AIMessage(content="Hello, how can I help?")])
    structured_model = FakeStructuredModel(_fake_final_response(answer="Hi there!"))
    graph = build_dataset_agent_graph(model_with_tools=tool_model, structured_model=structured_model)

    result = graph.invoke({"messages": [HumanMessage(content="hi")], "dataset_id": registered_dataset})

    assert len(result["messages"]) == 2  # human + AI, no tool round trip
    assert isinstance(result["final_analysis"], AnalysisResponse)
    assert result["final_analysis"].answer == "Hi there!"


def test_react_agent_variant_produces_structured_response(registered_dataset) -> None:
    """The create_react_agent() comparison artifact also needs
    response_format=AnalysisResponse wired correctly (Phase 5 addition to
    the same variant kept from Phase 4) -- this just proves it still
    constructs and runs, using a fake model that supports both
    bind_tools() and with_structured_output() (see Phase 4's exploration
    notes: FakeMessagesListChatModel supports neither by default)."""

    class BindableStructuredFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def with_structured_output(self, schema, **kwargs):
            return FakeStructuredModel(
                AnalysisResponse(answer="react-agent final answer", key_findings=["ok"], confidence=0.8)
            )

    model = BindableStructuredFakeModel(responses=[AIMessage(content="plain final answer, no tools needed")])
    agent = build_dataset_react_agent(model=model)

    result = agent.invoke({"messages": [HumanMessage(content="hi")], "dataset_id": registered_dataset})

    assert isinstance(result["structured_response"], AnalysisResponse)
    assert result["structured_response"].answer == "react-agent final answer"
