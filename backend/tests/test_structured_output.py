"""
Isolated, real-model tests for structured output.

Unlike every other test file in this project, these make REAL calls to
OpenAI -- that's the point: they're the thing that actually proves
model.with_structured_output(AnalysisResponse) works end-to-end, which a
fake/mocked model can't demonstrate (with_structured_output() isn't even
implemented on LangChain's fake chat models -- see conftest.FakeStructuredModel
for how every other test avoids needing this).

Skipped automatically unless a real OPENAI_API_KEY is exported before
running pytest (conftest.py's dummy "test-key" default does not count).
Run explicitly with:

    OPENAI_API_KEY=sk-... pytest tests/test_structured_output.py -v
"""

import os

import pytest

from app.agent.nodes import get_structured_model
from app.schemas.analysis import AnalysisResponse

_HAS_REAL_OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "test-key") != "test-key"

pytestmark = pytest.mark.skipif(
    not _HAS_REAL_OPENAI_KEY, reason="Requires a real OPENAI_API_KEY to call the live OpenAI API"
)


def test_structured_output_returns_an_analysis_response_instance() -> None:
    structured_model = get_structured_model()

    response = structured_model.invoke(
        """
        Based on the following analytical result:

        Delhi: 125000
        Mumbai: 110000

        SQL:
        SELECT region, SUM(revenue)
        FROM dataset
        GROUP BY region;

        Provide the final analysis.
        """
    )

    print(response)

    assert isinstance(response, AnalysisResponse)
    assert 0.0 <= response.confidence <= 1.0
    assert response.answer


def test_full_agent_workflow_produces_analysis_response(client, upload_fixture) -> None:
    """User -> FastAPI -> deterministic multi-step workflow graph -> real
    OpenAI calls at every structured-output stage -> real DuckDB -> a
    validated AnalysisResponse.

    Uploads a real dataset through the real upload API (so it's fully
    registered in both Phase 2 metadata and Phase 3 DuckDB, exactly like
    a real user's dataset) and drives the actual, unmocked
    analyze_question -> inspect_schema -> create_analysis_plan ->
    [generate_step_sql -> validate_sql -> execute_step_sql ->
    validate_step_result] -> synthesize -> build_chart_data ->
    generate_final_response sequence through the real FastAPI endpoint
    with a real question. The API contract (POST /api/analysis ->
    AnalysisResponse) only grew additively across phases (sql_used
    became a list, chart/follow_up_suggestions/thread_id were added), so
    this test's core assertions barely needed to change even as the
    endpoint's internal implementation evolved underneath.
    """

    uploaded = upload_fixture("sales.csv").json()
    dataset_id = uploaded["dataset_id"]

    response = client.post(
        "/api/analysis",
        json={"dataset_id": dataset_id, "question": "Which region had the highest total sales?"},
    )

    assert response.status_code == 200
    body = response.json()
    print(body)

    analysis = AnalysisResponse.model_validate(body)
    assert analysis.answer
    assert len(analysis.sql_used) > 0  # at least one real query executed
    assert 0.0 <= analysis.confidence <= 1.0
    assert analysis.thread_id
