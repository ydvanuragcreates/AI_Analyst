"""
Tests for the LangChain tools in app/agent/tools/.

These call the real DuckDBService/DuckDB directly (registering a small
dataset, same pattern as test_duckdb_service.py) -- no OpenAI involved,
since a tool is just a plain Python function until a model decides to
call it. Invocation goes through `tool.invoke(tool_call_dict)` rather
than calling the underlying function directly, so these tests exercise
the exact same path ToolNode uses in the real graph -- including
InjectedState resolution for dataset_id, which we simulate by supplying
it directly in the tool_call's args (exactly what ToolNode does
automatically; see tests/manual_tool_call.py for the same pattern used
in the manual demo).
"""

import json
import uuid

import pandas as pd
import pytest
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.agent.nodes import AGENT_TOOLS
from app.agent.tools.analysis_tools import execute_sql
from app.agent.tools.dataset_tools import get_dataset_schema, get_sample_rows
from app.core.config import get_settings
from app.services.duckdb_service import get_duckdb_service

UNKNOWN_DATASET_ID = "00000000-0000-0000-0000-000000000000"


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


def _invoke(tool: BaseTool, dataset_id: str, **extra_args):
    """Invoke a tool the way ToolNode would: dataset_id supplied as if
    resolved from graph state, alongside whatever args the model chose."""

    call = {
        "name": tool.name,
        "args": {**extra_args, "dataset_id": dataset_id},
        "id": "test-call-id",
        "type": "tool_call",
    }
    return tool.invoke(call)


def _payload(tool_message) -> dict:
    return json.loads(tool_message.content)


# ---------------------------------------------------------------------- #
# 1-2: tool registration and schema
# ---------------------------------------------------------------------- #


def test_all_three_tools_are_registered():
    assert {t.name for t in AGENT_TOOLS} == {"get_dataset_schema", "get_sample_rows", "execute_sql"}


def test_tools_are_langchain_basetool_instances():
    assert all(isinstance(t, BaseTool) for t in AGENT_TOOLS)


def test_tool_descriptions_are_present_and_meaningful():
    for t in AGENT_TOOLS:
        assert t.description and len(t.description) > 15


def test_dataset_id_is_hidden_from_the_openai_schema():
    # This is the actual schema sent to OpenAI (see convert_to_openai_tool);
    # dataset_id must never appear here since it's InjectedState, not
    # something the model should see, request, or get wrong.
    for t in AGENT_TOOLS:
        schema = convert_to_openai_tool(t)
        assert "dataset_id" not in schema["function"]["parameters"].get("properties", {})


def test_execute_sql_schema_exposes_only_query():
    schema = convert_to_openai_tool(execute_sql)
    params = schema["function"]["parameters"]
    assert set(params["properties"]) == {"query"}
    assert params["required"] == ["query"]


def test_get_sample_rows_schema_exposes_only_limit():
    schema = convert_to_openai_tool(get_sample_rows)
    params = schema["function"]["parameters"]
    assert set(params["properties"]) == {"limit"}


def test_get_dataset_schema_schema_exposes_no_parameters():
    schema = convert_to_openai_tool(get_dataset_schema)
    assert schema["function"]["parameters"].get("properties", {}) == {}


# ---------------------------------------------------------------------- #
# 3: get_dataset_schema
# ---------------------------------------------------------------------- #


def test_get_dataset_schema_returns_real_columns(registered_dataset):
    payload = _payload(_invoke(get_dataset_schema, registered_dataset))

    assert payload["dataset_id"] == registered_dataset
    assert {c["name"] for c in payload["columns"]} == {"region", "revenue"}


def test_get_dataset_schema_unknown_dataset_returns_error_dict():
    payload = _payload(_invoke(get_dataset_schema, UNKNOWN_DATASET_ID))

    assert "error" in payload


# ---------------------------------------------------------------------- #
# 4: get_sample_rows
# ---------------------------------------------------------------------- #


def test_get_sample_rows_returns_requested_count(registered_dataset):
    payload = _payload(_invoke(get_sample_rows, registered_dataset, limit=2))

    assert payload["row_count"] == 2
    assert len(payload["rows"]) == 2


def test_get_sample_rows_caps_at_configured_maximum(registered_dataset):
    payload = _payload(_invoke(get_sample_rows, registered_dataset, limit=10_000))

    assert payload["row_count"] <= get_settings().dataset_preview_rows


def test_get_sample_rows_unknown_dataset_returns_error_dict():
    payload = _payload(_invoke(get_sample_rows, UNKNOWN_DATASET_ID, limit=5))

    assert "error" in payload


# ---------------------------------------------------------------------- #
# 5: execute_sql
# ---------------------------------------------------------------------- #


def test_execute_sql_returns_real_query_results(registered_dataset):
    payload = _payload(
        _invoke(
            execute_sql,
            registered_dataset,
            query="SELECT region, SUM(revenue) AS total FROM dataset GROUP BY region ORDER BY total DESC",
        )
    )

    assert payload["rows"][0]["region"] == "Mumbai"
    assert payload["rows"][0]["total"] == 200.0


def test_execute_sql_result_matches_phase3_query_result_shape(registered_dataset):
    payload = _payload(_invoke(execute_sql, registered_dataset, query="SELECT * FROM dataset"))

    assert set(payload) >= {"columns", "rows", "row_count", "execution_time_ms", "truncated"}


def test_execute_sql_result_includes_the_actual_query_text(registered_dataset):
    # Phase 5: this is the ONLY thing the "tools" node trusts for
    # AgentState.executed_sql -- never the model's own memory of the SQL.
    query = "SELECT * FROM dataset LIMIT 1"
    payload = _payload(_invoke(execute_sql, registered_dataset, query=query))

    assert payload["query"] == query


def test_execute_sql_rejects_unsafe_sql_without_raising(registered_dataset):
    payload = _payload(_invoke(execute_sql, registered_dataset, query="DROP TABLE dataset"))

    assert "error" in payload


def test_execute_sql_rejects_invalid_sql_syntax_without_raising(registered_dataset):
    payload = _payload(_invoke(execute_sql, registered_dataset, query="SELECT FROM WHERE ???"))

    assert "error" in payload


def test_execute_sql_execution_time_failure_without_raising(registered_dataset):
    # Distinct from the two cases above: this is syntactically valid SQL
    # that only references the allowed 'dataset' table (passes
    # validate_query), but fails once DuckDB actually tries to bind it,
    # because the column doesn't exist.
    payload = _payload(_invoke(execute_sql, registered_dataset, query="SELECT nonexistent_column FROM dataset"))

    assert "error" in payload


def test_execute_sql_unknown_dataset_returns_error_dict():
    payload = _payload(_invoke(execute_sql, UNKNOWN_DATASET_ID, query="SELECT * FROM dataset"))

    assert "error" in payload


def test_execute_sql_cross_dataset_reference_rejected(registered_dataset):
    # Same isolation guarantee as Phase 3 -- a tool call can't be used to
    # read a different dataset's real table even by name.
    other_service = get_duckdb_service()
    real_table_name = other_service.get_table_name(registered_dataset)

    payload = _payload(_invoke(execute_sql, registered_dataset, query=f'SELECT * FROM "{real_table_name}"'))

    assert "error" in payload
