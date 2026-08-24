"""
API-level tests for the Phase 10 deep-analysis endpoints: creating an
Analysis with mode='deep' (job-based, via the Celery-eager worker -- same
infrastructure as the existing Phase 9 v1 tests, see test_v1_api.py's
module docstring for why SQLite/fakeredis/eager-Celery stand in for
Postgres/Redis/a real worker here), then reading back insights/report/
visualizations, plus GET /api/v1/datasets/{id}/profile.

The worker's real (OpenAI-backed) deep-analysis graph construction is
monkeypatched to use FakeStructuredModel throughout -- no real OpenAI
call happens anywhere in this file.
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.deep_analysis import (
    DeepAnalysisPlan,
    DeepAnalysisStep,
    DeepReviewResult,
    HypothesisList,
    InsightNarrative,
    ReportNarrative,
)
from tests.conftest import FakeStructuredModel

pytestmark = pytest.mark.usefixtures("sqlite_db")


def asyncio_run(coro):
    return asyncio.run(coro)

_PLAN = DeepAnalysisPlan(
    objective="Investigate sales",
    steps=[DeepAnalysisStep(step_type="trend", column="revenue", reason="track revenue over time")],
)
_INSIGHT_NARRATIVE = InsightNarrative(title="Revenue trend", finding="Revenue rose over the period.", implication="Worth monitoring.")
_REVIEW_APPROVED = DeepReviewResult(approved=True, confidence=0.9)
_REPORT = ReportNarrative(executive_summary="Revenue trended upward over the observed period.", recommendations=[])


def _patch_worker_deep_graph(monkeypatch):
    import app.workers.analysis_worker as worker

    real_build = worker.build_deep_analysis_graph

    def fake_build(*, checkpointer):
        return real_build(
            checkpointer=checkpointer,
            planning_model=FakeStructuredModel(_PLAN),
            hypothesis_model=FakeStructuredModel(HypothesisList(hypotheses=[])),
            insight_model=FakeStructuredModel(_INSIGHT_NARRATIVE),
            reviewer_model=FakeStructuredModel(_REVIEW_APPROVED),
            report_model=FakeStructuredModel(_REPORT),
        )

    monkeypatch.setattr(worker, "build_deep_analysis_graph", fake_build)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def registered_user(client: TestClient):
    email = f"{uuid.uuid4().hex}@example.com"
    response = client.post("/api/v1/auth/register", json={"email": email, "password": "supersecret123"})
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    return {"email": email, "token": token, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture
def dataset(client: TestClient, registered_user):
    with open("tests/fixtures/golden_sales.csv", "rb") as fh:
        response = client.post("/api/v1/datasets", files={"file": ("golden_sales.csv", fh, "text/csv")}, headers=registered_user["headers"])
    assert response.status_code == 201, response.text
    return response.json()


def test_create_deep_analysis_runs_to_completion(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    _patch_worker_deep_graph(monkeypatch)

    response = client.post(
        "/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "mode": "deep"}, headers=registered_user["headers"]
    )
    assert response.status_code == 202, response.text
    analysis_id = response.json()["analysis_id"]
    assert response.json()["status"] == "completed"

    detail = client.get(f"/api/v1/analysis/{analysis_id}", headers=registered_user["headers"])
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "completed"
    assert body["mode"] == "deep"
    assert body["progress"] == 100
    assert body["answer"]  # the summary, stored as `answer`


def test_failed_analysis_exposes_error_reason_on_detail_endpoint(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    """AnalysisDetailResponse.error was previously only surfaced via the
    cancel/approve responses -- see schemas/analysis_v1.py and
    api/routes/analysis_jobs.py::_to_detail."""

    import app.workers.analysis_worker as worker

    def raising_build(*, checkpointer):
        raise RuntimeError("simulated graph failure")

    monkeypatch.setattr(worker, "build_deep_analysis_graph", raising_build)

    create = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "mode": "deep"}, headers=registered_user["headers"])
    analysis_id = create.json()["analysis_id"]
    assert create.json()["status"] == "failed"

    detail = client.get(f"/api/v1/analysis/{analysis_id}", headers=registered_user["headers"])
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "failed"
    assert body["error"] == "simulated graph failure"


def test_deep_analysis_question_is_optional(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    """Section 44: no specific question needed for an autonomous run."""

    _patch_worker_deep_graph(monkeypatch)

    response = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "mode": "deep"}, headers=registered_user["headers"])
    assert response.status_code == 202, response.text


def test_qa_mode_still_requires_a_question(client: TestClient, registered_user, dataset) -> None:
    response = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "mode": "qa"}, headers=registered_user["headers"])
    assert response.status_code == 422


def test_get_insights_returns_reviewed_insights(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    _patch_worker_deep_graph(monkeypatch)
    create = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "mode": "deep"}, headers=registered_user["headers"])
    analysis_id = create.json()["analysis_id"]

    response = client.get(f"/api/v1/analysis/{analysis_id}/insights", headers=registered_user["headers"])
    assert response.status_code == 200, response.text
    insights = response.json()
    assert len(insights) >= 1
    assert insights[0]["evidence"]  # evidence-first: never empty
    assert "confidence" in insights[0] and "overall" in insights[0]["confidence"]


def test_get_report_returns_all_sections(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    _patch_worker_deep_graph(monkeypatch)
    create = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "mode": "deep"}, headers=registered_user["headers"])
    analysis_id = create.json()["analysis_id"]

    response = client.get(f"/api/v1/analysis/{analysis_id}/report", headers=registered_user["headers"])
    assert response.status_code == 200, response.text
    report = response.json()
    for field in (
        "executive_summary", "dataset_overview", "data_quality", "key_findings",
        "trends", "anomalies", "segment_analysis", "correlations", "recommendations", "limitations",
    ):
        assert field in report


def test_get_visualizations_returns_stable_chart_specs(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    _patch_worker_deep_graph(monkeypatch)
    create = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "mode": "deep"}, headers=registered_user["headers"])
    analysis_id = create.json()["analysis_id"]

    response = client.get(f"/api/v1/analysis/{analysis_id}/visualizations", headers=registered_user["headers"])
    assert response.status_code == 200, response.text
    visualizations = response.json()
    assert visualizations
    for viz in visualizations:
        assert viz["type"] in ("line", "bar", "scatter", "histogram", "box", "heatmap", "table")
        assert "data" in viz


def test_insights_endpoint_rejects_qa_mode_analysis(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    import app.workers.analysis_worker as worker
    from app.schemas.analysis import AnalysisResponse, AnalyticalFindings, GeneratedSQL, ResultValidation, SupervisorDecision
    from app.schemas.review import ReviewResult

    real_build = worker.build_multi_agent_graph

    def fake_build(*, checkpointer):
        return real_build(
            checkpointer=checkpointer,
            supervisor_model=FakeStructuredModel(SupervisorDecision(complexity="simple", requires_planning=False, requires_data_analysis=True, is_answerable=True, reasoning="x")),
            sql_model=FakeStructuredModel(GeneratedSQL(sql="SELECT SUM(revenue) AS total FROM dataset")),
            result_model=FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.9)),
            data_analyst_model=FakeStructuredModel(AnalyticalFindings(answer="x", key_findings=[], metrics=[], anomalies=[], confidence=0.9)),
            reviewer_model=FakeStructuredModel(ReviewResult(approved=True, issues=[], corrections=[], confidence=0.9)),
            final_model=FakeStructuredModel(AnalysisResponse(answer="x", key_findings=[], metrics=[], sql_used=[], confidence=0.9, chart=None, follow_up_suggestions=[])),
        )

    monkeypatch.setattr(worker, "build_multi_agent_graph", fake_build)

    create = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "What is total revenue?", "mode": "qa"}, headers=registered_user["headers"])
    analysis_id = create.json()["analysis_id"]

    response = client.get(f"/api/v1/analysis/{analysis_id}/insights", headers=registered_user["headers"])
    assert response.status_code == 422


def test_dataset_profile_endpoint_returns_compact_profile(client: TestClient, registered_user, dataset) -> None:
    response = client.get(f"/api/v1/datasets/{dataset['dataset_id']}/profile", headers=registered_user["headers"])
    assert response.status_code == 200, response.text
    profile = response.json()
    assert profile["row_count"] == 13
    assert set(profile["numeric_columns"]) == {"revenue", "discount", "transaction_amount"}
    assert profile["date_columns"] == ["date"]


def test_dataset_quality_endpoint_returns_planted_issues(client: TestClient, registered_user, dataset) -> None:
    """golden_sales.csv plants exactly: 1 duplicate row, 1 missing
    discount value, 1 extreme transaction_amount outlier -- see
    test_deep_analysis_data_quality.py for the same assertions against
    the service directly."""

    response = client.get(f"/api/v1/datasets/{dataset['dataset_id']}/quality", headers=registered_user["headers"])
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["duplicate_row_count"] == 1
    issue_types = {issue["issue_type"] for issue in report["issues"]}
    assert "missing_values" in issue_types
    assert "outliers" in issue_types
    assert "duplicate_rows" in issue_types


def test_other_users_dataset_quality_is_hidden(client: TestClient, registered_user, dataset) -> None:
    other_email = f"{uuid.uuid4().hex}@example.com"
    other = client.post("/api/v1/auth/register", json={"email": other_email, "password": "supersecret123"})
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}

    response = client.get(f"/api/v1/datasets/{dataset['dataset_id']}/quality", headers=other_headers)
    assert response.status_code == 404


def test_dataset_profile_endpoint_is_cached(client: TestClient, registered_user, dataset, fake_redis) -> None:
    first = client.get(f"/api/v1/datasets/{dataset['dataset_id']}/profile", headers=registered_user["headers"])
    assert first.status_code == 200

    cache_key = f"dataset_profile:{dataset['dataset_id']}:{dataset['version']}"
    assert asyncio_run(fake_redis.get(cache_key)) is not None

    second = client.get(f"/api/v1/datasets/{dataset['dataset_id']}/profile", headers=registered_user["headers"])
    assert second.status_code == 200
    assert second.json() == first.json()


def test_analysis_history_filters_by_mode(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    """The frontend's /reports list needs to show only deep-analysis
    runs -- see api/routes/analysis_jobs.py's `mode` query param and
    AnalysisRepository.list_for_user's matching filter."""

    _patch_worker_deep_graph(monkeypatch)
    client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "mode": "deep"}, headers=registered_user["headers"])

    import app.workers.analysis_worker as worker
    from app.schemas.analysis import AnalysisResponse, AnalyticalFindings, GeneratedSQL, ResultValidation, SupervisorDecision
    from app.schemas.review import ReviewResult

    real_build = worker.build_multi_agent_graph

    def fake_qa_build(*, checkpointer):
        return real_build(
            checkpointer=checkpointer,
            supervisor_model=FakeStructuredModel(SupervisorDecision(complexity="simple", requires_planning=False, requires_data_analysis=True, is_answerable=True, reasoning="x")),
            sql_model=FakeStructuredModel(GeneratedSQL(sql="SELECT SUM(revenue) AS total FROM dataset")),
            result_model=FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.9)),
            data_analyst_model=FakeStructuredModel(AnalyticalFindings(answer="x", key_findings=[], metrics=[], anomalies=[], confidence=0.9)),
            reviewer_model=FakeStructuredModel(ReviewResult(approved=True, issues=[], corrections=[], confidence=0.9)),
            final_model=FakeStructuredModel(AnalysisResponse(answer="x", key_findings=[], metrics=[], sql_used=[], confidence=0.9, chart=None, follow_up_suggestions=[])),
        )

    monkeypatch.setattr(worker, "build_multi_agent_graph", fake_qa_build)
    client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "What is total revenue?", "mode": "qa"}, headers=registered_user["headers"])

    deep_only = client.get("/api/v1/analysis?mode=deep", headers=registered_user["headers"])
    assert deep_only.status_code == 200
    body = deep_only.json()
    assert body["total"] == 1
    assert body["analyses"][0]["mode"] == "deep"

    qa_only = client.get("/api/v1/analysis?mode=qa", headers=registered_user["headers"])
    assert qa_only.json()["total"] == 1

    both = client.get("/api/v1/analysis", headers=registered_user["headers"])
    assert both.json()["total"] == 2


def test_other_users_dataset_profile_is_hidden(client: TestClient, registered_user, dataset) -> None:
    other_email = f"{uuid.uuid4().hex}@example.com"
    other = client.post("/api/v1/auth/register", json={"email": other_email, "password": "supersecret123"})
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}

    response = client.get(f"/api/v1/datasets/{dataset['dataset_id']}/profile", headers=other_headers)
    assert response.status_code == 404
