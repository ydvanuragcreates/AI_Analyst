"""
Tests for POST /api/analysis, backed by the Phase 8 multi-agent graph
(build_multi_agent_graph), plus GET /api/analysis/{thread_id}/status and
POST /api/analysis/{thread_id}/approve (api/routes/approvals.py).

Phase 6/7's deterministic workflow graph is still fully tested on its own
in test_workflow_graph.py / test_workflow_nodes.py -- it's no longer what
this HTTP endpoint runs (see agent/graph.py's module docstring), the same
way build_dataset_agent_graph stayed tested after Phase 6 stopped using it
for POST /api/analysis.

The compiled multi-agent graph is swapped out via FastAPI's
dependency_overrides for one built with fake models for all seven
structured-output stages (build_multi_agent_graph accepts
supervisor_model=, planning_model=, sql_model=, result_model=,
data_analyst_model=, reviewer_model=, final_model=), so these run with no
real OpenAI calls.
"""

import pytest
from fastapi.testclient import TestClient

from app.agent.graph import build_multi_agent_graph, get_multi_agent_graph
from app.main import app
from app.schemas.analysis import (
    AnalysisPlan,
    AnalysisResponse,
    AnalysisStep,
    AnalyticalFindings,
    GeneratedSQL,
    Metric,
    ResultValidation,
    SupervisorDecision,
)
from app.schemas.review import ReviewResult
from tests.conftest import FakeStructuredModel

REGION_SALES_QUERY = "SELECT region, SUM(sales) AS total FROM dataset GROUP BY region ORDER BY total DESC LIMIT 1"


def _supervisor_decision(**overrides) -> SupervisorDecision:
    defaults = dict(
        complexity="simple",
        requires_planning=False,
        requires_data_analysis=True,
        is_answerable=True,
        reasoning="Asks which region has the most sales.",
    )
    defaults.update(overrides)
    return SupervisorDecision(**defaults)


def _plan() -> AnalysisPlan:
    return AnalysisPlan(
        objective="Find the region with the highest total sales.",
        steps=[AnalysisStep(step_id=1, objective="Sum sales by region.", required_columns=["region", "sales"])],
    )


def _findings(**overrides) -> AnalyticalFindings:
    defaults = dict(
        answer="Mumbai had the highest sales.",
        key_findings=["Mumbai's sales figure is the highest in the dataset."],
        metrics=[Metric(name="Top sales", value="15000", description="Highest single sales value")],
        anomalies=[],
        confidence=0.9,
    )
    defaults.update(overrides)
    return AnalyticalFindings(**defaults)


def _review(**overrides) -> ReviewResult:
    defaults = dict(approved=True, issues=[], corrections=[], confidence=0.9)
    defaults.update(overrides)
    return ReviewResult(**defaults)


def _final_response(**overrides) -> AnalysisResponse:
    defaults = dict(
        answer="Mumbai had the highest sales.",
        key_findings=["Mumbai's sales figure is the highest in the dataset."],
        metrics=[],
        sql_used=["THIS SHOULD BE OVERWRITTEN"],
        confidence=0.92,
        chart=None,
        follow_up_suggestions=[],
    )
    defaults.update(overrides)
    return AnalysisResponse(**defaults)


def _override_graph(
    *,
    supervisor_model=None,
    planning_model=None,
    sql_model=None,
    result_model=None,
    data_analyst_model=None,
    reviewer_model=None,
    final_model=None,
) -> None:
    # Built ONCE, not inside the lambda -- see build_analysis_workflow_graph's
    # equivalent comment in the Phase 6/7 test suite this file replaces:
    # a lambda that calls build_multi_agent_graph() itself would hand each
    # request a brand new InMemorySaver, breaking cross-request thread_id
    # continuity (which the approval flow depends on just as much as
    # conversational follow-ups did).
    graph = build_multi_agent_graph(
        supervisor_model=supervisor_model or FakeStructuredModel(_supervisor_decision()),
        planning_model=planning_model or FakeStructuredModel(_plan()),
        sql_model=sql_model or FakeStructuredModel(GeneratedSQL(sql=REGION_SALES_QUERY)),
        result_model=result_model or FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.95)),
        data_analyst_model=data_analyst_model or FakeStructuredModel(_findings()),
        reviewer_model=reviewer_model or FakeStructuredModel(_review()),
        final_model=final_model or FakeStructuredModel(_final_response()),
    )
    app.dependency_overrides[get_multi_agent_graph] = lambda: graph


@pytest.fixture
def scripted_client(upload_fixture):
    uploaded = upload_fixture("sales.csv").json()
    dataset_id = uploaded["dataset_id"]
    _override_graph()

    yield TestClient(app), dataset_id

    app.dependency_overrides.pop(get_multi_agent_graph, None)


def test_ask_agent_returns_validated_analysis_response(scripted_client) -> None:
    client, dataset_id = scripted_client

    response = client.post("/api/analysis", json={"dataset_id": dataset_id, "question": "Which region had the highest sales?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Mumbai had the highest sales."
    assert body["confidence"] == 0.92
    assert body["sql_used"] == [REGION_SALES_QUERY]  # actual executed SQL, not the model's guess
    assert body["debug"] is None
    assert body["thread_id"]  # always assigned, even for a fresh conversation


def test_ask_agent_response_matches_analysis_response_schema(scripted_client) -> None:
    client, dataset_id = scripted_client

    response = client.post("/api/analysis", json={"dataset_id": dataset_id, "question": "anything"})

    AnalysisResponse.model_validate(response.json())


def test_ask_agent_debug_mode_includes_safe_execution_metadata(scripted_client) -> None:
    client, dataset_id = scripted_client

    response = client.post(
        "/api/analysis", json={"dataset_id": dataset_id, "question": "Which region had the highest sales?", "debug": True}
    )

    assert response.status_code == 200
    debug = response.json()["debug"]
    assert debug is not None
    assert debug["generated_sql"] == REGION_SALES_QUERY
    assert len(debug["executed_queries"]) == 1
    # Default SupervisorDecision (requires_planning=False) takes the
    # deterministic trivial_plan shortcut, not the Planner -- its
    # objective is the raw question, not planning_model's fake plan.
    assert debug["analysis_plan"]["objective"] == "Which region had the highest sales?"
    assert set(debug["schema_columns"]) == {"date", "region", "sales"}
    # Phase 8 fields.
    assert debug["current_agent"] == "generate_final_response"
    assert debug["supervisor_decision"]["complexity"] == "simple"
    assert debug["review"]["approved"] is True
    assert debug["review_retry_count"] == 0
    assert debug["human_approval_required"] is False
    assert debug["human_approved"] is None


def test_ask_agent_unknown_dataset_returns_404(scripted_client) -> None:
    client, _dataset_id = scripted_client

    response = client.post(
        "/api/analysis", json={"dataset_id": "00000000-0000-0000-0000-000000000000", "question": "anything?"}
    )

    assert response.status_code == 404


def test_ask_agent_empty_question_returns_422(scripted_client) -> None:
    client, dataset_id = scripted_client

    response = client.post("/api/analysis", json={"dataset_id": dataset_id, "question": ""})

    assert response.status_code == 422


def test_ask_agent_not_answerable_skips_sql_entirely(client: TestClient, upload_fixture) -> None:
    """Supervisor.is_answerable=False short-circuits straight to a "can't
    answer" response -- no SQL Analyst/Data Analyst/Reviewer call at all
    (see build_multi_agent_final_response_node's findings-is-None guard)."""

    uploaded = upload_fixture("sales.csv").json()
    dataset_id = uploaded["dataset_id"]

    _override_graph(
        supervisor_model=FakeStructuredModel(
            _supervisor_decision(is_answerable=False, reasoning="This dataset has no weather data.")
        )
    )

    try:
        response = client.post("/api/analysis", json={"dataset_id": dataset_id, "question": "What's the weather like?"})
    finally:
        app.dependency_overrides.pop(get_multi_agent_graph, None)

    assert response.status_code == 200
    body = response.json()
    assert "can't answer" in body["answer"].lower()
    assert body["sql_used"] == []
    assert body["confidence"] == 0.0


def test_ask_agent_conversation_follow_up_uses_prior_turn(client: TestClient, upload_fixture) -> None:
    """Conversational follow-up + thread memory, exercised through the
    real HTTP endpoint (not just the graph directly). The Supervisor is
    what reads conversation_context in the multi-agent graph."""

    uploaded = upload_fixture("sales.csv").json()
    dataset_id = uploaded["dataset_id"]

    seen_messages_by_call: list[list] = []

    class RecordingSupervisorModel:
        def invoke(self, messages):
            seen_messages_by_call.append(list(messages))
            return _supervisor_decision()

    _override_graph(
        supervisor_model=RecordingSupervisorModel(),
        final_model=FakeStructuredModel(_final_response(answer="Mumbai has the highest sales.", key_findings=["Mumbai leads."])),
    )

    try:
        turn1 = client.post("/api/analysis", json={"dataset_id": dataset_id, "question": "Which region has the highest sales?"})
        assert turn1.status_code == 200
        thread_id = turn1.json()["thread_id"]
        assert thread_id

        turn2 = client.post(
            "/api/analysis",
            json={"dataset_id": dataset_id, "question": "What about the second highest?", "thread_id": thread_id},
        )
        assert turn2.status_code == 200
        assert turn2.json()["thread_id"] == thread_id  # same conversation
    finally:
        app.dependency_overrides.pop(get_multi_agent_graph, None)

    # The supervisor on turn 2 must have received turn 1's context.
    turn2_messages = seen_messages_by_call[1]
    joined = " ".join(m.content for m in turn2_messages)
    assert "Which region has the highest sales?" in joined
    assert "Mumbai has the highest sales." in joined


def test_ask_agent_without_thread_id_starts_fresh_conversation(client: TestClient, upload_fixture) -> None:
    uploaded = upload_fixture("sales.csv").json()
    dataset_id = uploaded["dataset_id"]

    seen_messages_by_call: list[list] = []

    class RecordingSupervisorModel:
        def invoke(self, messages):
            seen_messages_by_call.append(list(messages))
            return _supervisor_decision()

    _override_graph(supervisor_model=RecordingSupervisorModel())

    try:
        client.post("/api/analysis", json={"dataset_id": dataset_id, "question": "Which region has the highest sales?"})
        # No thread_id passed -> a brand new conversation, no prior context.
        client.post("/api/analysis", json={"dataset_id": dataset_id, "question": "Which region has the highest sales?"})
    finally:
        app.dependency_overrides.pop(get_multi_agent_graph, None)

    for messages in seen_messages_by_call:
        joined = " ".join(m.content for m in messages)
        assert "Context from the previous turn" not in joined


# ------------------------------------------------------------------ #
# Human-in-the-loop: POST /api/analysis returning approval_required,
# GET .../status, POST .../approve
# ------------------------------------------------------------------ #


def test_ask_agent_low_confidence_returns_approval_required(client: TestClient, upload_fixture) -> None:
    uploaded = upload_fixture("sales.csv").json()
    dataset_id = uploaded["dataset_id"]

    _override_graph(reviewer_model=FakeStructuredModel(_review(confidence=0.5)))

    try:
        response = client.post("/api/analysis", json={"dataset_id": dataset_id, "question": "Which region had the highest sales?"})
    finally:
        app.dependency_overrides.pop(get_multi_agent_graph, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approval_required"
    assert "confidence" in body["reason"].lower()
    assert body["thread_id"]


def test_status_endpoint_reports_waiting_for_approval(client: TestClient, upload_fixture) -> None:
    uploaded = upload_fixture("sales.csv").json()
    dataset_id = uploaded["dataset_id"]

    _override_graph(reviewer_model=FakeStructuredModel(_review(confidence=0.5)))

    try:
        started = client.post("/api/analysis", json={"dataset_id": dataset_id, "question": "Which region had the highest sales?"})
        thread_id = started.json()["thread_id"]

        status_response = client.get(f"/api/analysis/{thread_id}/status")
    finally:
        app.dependency_overrides.pop(get_multi_agent_graph, None)

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == "waiting_for_approval"
    assert body["thread_id"] == thread_id
    assert body["reason"]


def test_status_endpoint_reports_completed(scripted_client) -> None:
    client, dataset_id = scripted_client

    started = client.post("/api/analysis", json={"dataset_id": dataset_id, "question": "Which region had the highest sales?"})
    thread_id = started.json()["thread_id"]

    status_response = client.get(f"/api/analysis/{thread_id}/status")

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == "completed"
    assert body["thread_id"] == thread_id


def test_status_endpoint_unknown_thread_returns_404(scripted_client) -> None:
    client, _dataset_id = scripted_client

    response = client.get("/api/analysis/00000000-0000-0000-0000-000000000000/status")

    assert response.status_code == 404


def test_approve_endpoint_approves_and_returns_final_response(client: TestClient, upload_fixture) -> None:
    uploaded = upload_fixture("sales.csv").json()
    dataset_id = uploaded["dataset_id"]

    _override_graph(
        reviewer_model=FakeStructuredModel(_review(confidence=0.5)),
        final_model=FakeStructuredModel(_final_response(answer="Approved: Mumbai leads.")),
    )

    try:
        started = client.post("/api/analysis", json={"dataset_id": dataset_id, "question": "Which region had the highest sales?"})
        thread_id = started.json()["thread_id"]
        assert started.json()["status"] == "approval_required"

        approved = client.post(f"/api/analysis/{thread_id}/approve", json={"approved": True})
    finally:
        app.dependency_overrides.pop(get_multi_agent_graph, None)

    assert approved.status_code == 200
    body = approved.json()
    assert body["answer"] == "Approved: Mumbai leads."
    assert body["thread_id"] == thread_id

    AnalysisResponse.model_validate(body)


def test_approve_endpoint_rejection_replans_with_feedback(client: TestClient, upload_fixture) -> None:
    """A rejection is not a dead end: it flows Human Feedback -> Planner ->
    SQL Analyst -> Data Analyst -> Reviewer again (route_after_human_approval),
    with the feedback reaching the Planner via `retry_reason`."""

    uploaded = upload_fixture("sales.csv").json()
    dataset_id = uploaded["dataset_id"]

    seen_planner_messages: list[list] = []

    class RecordingPlanningModel:
        def invoke(self, messages):
            seen_planner_messages.append(list(messages))
            return _plan()

    _override_graph(
        planning_model=RecordingPlanningModel(),
        # Low confidence on the FIRST reviewer call triggers approval;
        # high confidence on the SECOND (post-replan) call resolves it.
        reviewer_model=FakeStructuredModel(responses=[_review(confidence=0.5), _review(confidence=0.9)]),
        final_model=FakeStructuredModel(_final_response(answer="Re-verified: Mumbai leads.")),
    )

    try:
        started = client.post("/api/analysis", json={"dataset_id": dataset_id, "question": "Which region had the highest sales?"})
        thread_id = started.json()["thread_id"]

        rejected = client.post(
            f"/api/analysis/{thread_id}/approve",
            json={"approved": False, "feedback": "Double-check against raw rows."},
        )
    finally:
        app.dependency_overrides.pop(get_multi_agent_graph, None)

    assert rejected.status_code == 200
    body = rejected.json()
    assert body["answer"] == "Re-verified: Mumbai leads."

    # The default SupervisorDecision (requires_planning=False) means the
    # FIRST pass uses the deterministic trivial_plan shortcut, not the
    # Planner -- so the Planner's only call here is the replan triggered
    # by the human's rejection, and it must have been given the feedback.
    assert len(seen_planner_messages) == 1
    joined = " ".join(m.content for m in seen_planner_messages[0])
    assert "Double-check against raw rows." in joined


def test_approve_endpoint_unknown_thread_returns_404(scripted_client) -> None:
    client, _dataset_id = scripted_client

    response = client.post("/api/analysis/00000000-0000-0000-0000-000000000000/approve", json={"approved": True})

    assert response.status_code == 404


def test_approve_endpoint_not_waiting_returns_409(scripted_client) -> None:
    client, dataset_id = scripted_client

    started = client.post("/api/analysis", json={"dataset_id": dataset_id, "question": "Which region had the highest sales?"})
    thread_id = started.json()["thread_id"]
    assert started.json().get("status") != "approval_required"  # already completed, nothing to approve

    response = client.post(f"/api/analysis/{thread_id}/approve", json={"approved": True})

    assert response.status_code == 409
