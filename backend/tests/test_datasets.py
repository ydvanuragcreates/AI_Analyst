"""
Tests for the dataset upload / inspection / retrieval / deletion API.

Uses the real DatasetService against the configured local storage
directory (no mocking of pandas or the filesystem) — the `upload_fixture`
fixture from conftest.py deletes whatever it creates, so the suite is
self-cleaning.
"""

from fastapi.testclient import TestClient


def test_upload_valid_csv_returns_metadata(upload_fixture) -> None:
    response = upload_fixture("sales.csv")

    assert response.status_code == 201
    body = response.json()
    assert body["file_type"] == "csv"
    assert body["filename"] == "sales.csv"
    assert body["row_count"] == 6
    assert body["column_count"] == 3


def test_upload_valid_xlsx_returns_metadata(upload_fixture) -> None:
    response = upload_fixture("sales.xlsx")

    assert response.status_code == 201
    body = response.json()
    assert body["file_type"] == "xlsx"
    assert body["row_count"] == 6
    assert body["column_count"] == 3


def test_upload_valid_json_returns_metadata(upload_fixture) -> None:
    response = upload_fixture("sales.json")

    assert response.status_code == 201
    body = response.json()
    assert body["file_type"] == "json"
    assert body["row_count"] == 6
    assert body["column_count"] == 3


def test_upload_rejects_unsupported_extension(upload_fixture) -> None:
    response = upload_fixture("notes.txt")

    assert response.status_code == 400
    assert "Unsupported file extension" in response.json()["detail"]


def test_upload_rejects_empty_file(upload_fixture) -> None:
    response = upload_fixture("empty.csv")

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_upload_rejects_invalid_json_structure(upload_fixture) -> None:
    response = upload_fixture("invalid_structure.json")

    assert response.status_code == 400
    assert "Unsupported JSON structure" in response.json()["detail"]


def test_upload_detects_column_types(upload_fixture) -> None:
    metadata = upload_fixture("sales.csv").json()

    types_by_name = {column["name"]: column["data_type"] for column in metadata["columns"]}
    assert types_by_name["date"] == "datetime"
    assert types_by_name["region"] == "string"
    assert types_by_name["sales"] == "float"


def test_upload_detects_boolean_column_type(upload_fixture) -> None:
    metadata = upload_fixture("products.json").json()

    types_by_name = {column["name"]: column["data_type"] for column in metadata["columns"]}
    assert types_by_name["in_stock"] == "boolean"
    assert types_by_name["price"] == "float"
    assert types_by_name["product"] == "string"


def test_upload_detects_missing_values(upload_fixture) -> None:
    metadata = upload_fixture("sales.csv").json()

    assert metadata["missing_values"] == {"date": 0, "region": 0, "sales": 1}
    columns_by_name = {column["name"]: column for column in metadata["columns"]}
    assert columns_by_name["sales"]["nullable"] is True
    assert columns_by_name["region"]["nullable"] is False


def test_upload_returns_bounded_preview(upload_fixture) -> None:
    metadata = upload_fixture("sales.csv").json()

    # 6 rows uploaded, DATASET_PREVIEW_ROWS defaults to 5.
    assert len(metadata["preview"]) == 5
    assert metadata["preview"][0]["region"] == "Delhi"


def test_get_dataset_by_id(client: TestClient, upload_fixture) -> None:
    uploaded = upload_fixture("sales.csv").json()

    response = client.get(f"/api/datasets/{uploaded['dataset_id']}")

    assert response.status_code == 200
    assert response.json()["dataset_id"] == uploaded["dataset_id"]


def test_get_unknown_dataset_returns_404(client: TestClient) -> None:
    response = client.get("/api/datasets/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


def test_get_dataset_with_malformed_id_returns_404(client: TestClient) -> None:
    response = client.get("/api/datasets/not-a-uuid")

    assert response.status_code == 404


def test_list_datasets_includes_uploaded_dataset(client: TestClient, upload_fixture) -> None:
    uploaded = upload_fixture("sales.csv").json()

    response = client.get("/api/datasets")

    assert response.status_code == 200
    body = response.json()
    dataset_ids = {dataset["dataset_id"] for dataset in body["datasets"]}
    assert uploaded["dataset_id"] in dataset_ids
    assert body["count"] == len(body["datasets"])


def test_delete_dataset_removes_it(client: TestClient, upload_fixture) -> None:
    dataset_id = upload_fixture("sales.csv").json()["dataset_id"]

    delete_response = client.delete(f"/api/datasets/{dataset_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"dataset_id": dataset_id, "deleted": True}

    get_response = client.get(f"/api/datasets/{dataset_id}")
    assert get_response.status_code == 404


def test_delete_unknown_dataset_returns_404(client: TestClient) -> None:
    response = client.delete("/api/datasets/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
