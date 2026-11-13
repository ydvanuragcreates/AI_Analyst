"""Datasets survive an ephemeral-disk wipe: the original bytes are kept in
Postgres on upload, and scripts/restore_datasets.py rebuilds the local
file, DuckDB table and metadata.json from them."""

import asyncio
import shutil

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.scripts.restore_datasets import restore_all
from app.services.dataset_service import get_dataset_service
from app.services.duckdb_service import get_duckdb_service

pytestmark = pytest.mark.usefixtures("sqlite_db")


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app)
    yield test_client
    # Dataset storage is shared across the whole test session; don't leave
    # restored copies behind for other tests' "list datasets" assertions.
    for item in test_client.get("/api/v1/datasets").json()["datasets"]:
        test_client.delete(f"/api/v1/datasets/{item['dataset_id']}")


def _upload(client: TestClient) -> dict:
    with open("tests/fixtures/sales.csv", "rb") as fh:
        response = client.post("/api/v1/datasets", files={"file": ("sales.csv", fh, "text/csv")})
    assert response.status_code == 201, response.text
    return response.json()


def _wipe_local_copy(dataset_id: str) -> None:
    """What a container restart on an ephemeral disk looks like."""
    service = get_dataset_service()
    shutil.rmtree(service._dataset_dir(dataset_id))
    get_duckdb_service().unregister_dataset(dataset_id)


def test_dataset_is_restored_after_disk_wipe(client: TestClient) -> None:
    uploaded = _upload(client)
    dataset_id = uploaded["dataset_id"]
    _wipe_local_copy(dataset_id)

    assert client.get("/api/v1/datasets").json()["count"] == 0  # gone locally

    counts = asyncio.run(restore_all(get_dataset_service()))
    assert counts["restored"] == 1 and counts["failed"] == 0

    listed = client.get("/api/v1/datasets").json()
    assert listed["count"] == 1
    restored = listed["datasets"][0]["metadata"]
    assert restored["filename"] == "sales.csv"
    assert restored["row_count"] == uploaded["metadata"]["row_count"]
    # Queryable again, not just listed.
    assert get_duckdb_service().get_schema(dataset_id).columns


def test_restore_skips_datasets_already_present(client: TestClient) -> None:
    _upload(client)
    counts = asyncio.run(restore_all(get_dataset_service()))
    assert counts == {"restored": 0, "already_present": 1, "failed": 0, "no_stored_file": 0}


def test_delete_removes_stored_file(client: TestClient) -> None:
    dataset_id = _upload(client)["dataset_id"]
    assert client.delete(f"/api/v1/datasets/{dataset_id}").status_code == 204
    counts = asyncio.run(restore_all(get_dataset_service()))
    assert counts == {"restored": 0, "already_present": 0, "failed": 0, "no_stored_file": 0}
