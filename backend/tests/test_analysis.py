"""
Tests for the analytical query API (POST /api/analysis/query,
GET /api/analysis/{dataset_id}/schema, GET /api/analysis/{dataset_id}/sample).

Uses the real DuckDBService (no mocking) against datasets uploaded through
the actual upload endpoint, so these tests also exercise the Phase 2 -> 3
integration: upload registers the dataset in DuckDB, and delete removes it.
"""

from fastapi.testclient import TestClient


def _query(client: TestClient, dataset_id: str, sql: str):
    return client.post("/api/analysis/query", json={"dataset_id": dataset_id, "query": sql})


def test_query_returns_rows(client: TestClient, upload_fixture) -> None:
    dataset_id = upload_fixture("sales.csv").json()["dataset_id"]

    response = _query(client, dataset_id, "SELECT region, sales FROM dataset ORDER BY sales DESC LIMIT 3")

    assert response.status_code == 200
    body = response.json()
    assert body["dataset_id"] == dataset_id
    assert set(body["columns"]) == {"region", "sales"}
    assert body["row_count"] == 3
    assert len(body["rows"]) == 3
    assert body["rows"][0]["sales"] == 15000.0
    assert body["truncated"] is False
    assert body["execution_time_ms"] >= 0


def test_query_supports_group_by_aggregation(client: TestClient, upload_fixture) -> None:
    dataset_id = upload_fixture("sales.csv").json()["dataset_id"]

    response = _query(
        client, dataset_id, "SELECT region, SUM(sales) AS total FROM dataset GROUP BY region ORDER BY region"
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body["columns"]) == {"region", "total"}
    assert body["row_count"] == 4  # Delhi, Mumbai, Bangalore, Chennai


def test_query_rejects_disallowed_statement(client: TestClient, upload_fixture) -> None:
    dataset_id = upload_fixture("sales.csv").json()["dataset_id"]

    response = _query(client, dataset_id, "DROP TABLE dataset")

    assert response.status_code == 400


def test_query_rejects_reference_to_real_table_name(client: TestClient, upload_fixture) -> None:
    dataset_id = upload_fixture("sales.csv").json()["dataset_id"]
    real_table_name = client.get(f"/api/analysis/{dataset_id}/schema").json()["table_name"]

    # Even knowing the real internal table name shouldn't work -- only the
    # fixed alias 'dataset' is accepted, by design.
    response = _query(client, dataset_id, f'SELECT * FROM "{real_table_name}"')

    assert response.status_code == 400


def test_query_unknown_dataset_returns_404(client: TestClient) -> None:
    response = _query(client, "00000000-0000-0000-0000-000000000000", "SELECT * FROM dataset")

    assert response.status_code == 404


def test_query_after_dataset_deleted_returns_404(client: TestClient, upload_fixture) -> None:
    dataset_id = upload_fixture("sales.csv").json()["dataset_id"]
    client.delete(f"/api/datasets/{dataset_id}")

    response = _query(client, dataset_id, "SELECT * FROM dataset")

    assert response.status_code == 404


def test_get_schema_returns_columns(client: TestClient, upload_fixture) -> None:
    dataset_id = upload_fixture("sales.csv").json()["dataset_id"]

    response = client.get(f"/api/analysis/{dataset_id}/schema")

    assert response.status_code == 200
    body = response.json()
    assert body["dataset_id"] == dataset_id
    assert body["table_name"].startswith("dataset_")
    column_names = {column["name"] for column in body["columns"]}
    assert column_names == {"date", "region", "sales"}


def test_get_schema_unknown_dataset_returns_404(client: TestClient) -> None:
    response = client.get("/api/analysis/00000000-0000-0000-0000-000000000000/schema")

    assert response.status_code == 404


def test_get_sample_returns_bounded_rows(client: TestClient, upload_fixture) -> None:
    dataset_id = upload_fixture("sales.csv").json()["dataset_id"]

    response = client.get(f"/api/analysis/{dataset_id}/sample")

    assert response.status_code == 200
    body = response.json()
    # sales.csv has 6 rows; DATASET_PREVIEW_ROWS defaults to 5.
    assert body["row_count"] == 5
    assert len(body["rows"]) == 5


def test_get_sample_unknown_dataset_returns_404(client: TestClient) -> None:
    response = client.get("/api/analysis/00000000-0000-0000-0000-000000000000/sample")

    assert response.status_code == 404
