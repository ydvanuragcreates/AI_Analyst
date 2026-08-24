"""
Phase 9 production-readiness audit -- DuckDB concurrency (the audit's
highest-priority finding).

DuckDB is an embedded, single-writer-process database. Before this fix,
`DuckDBService` held one `duckdb.connect()` open for a process's entire
lifetime; a SECOND OS process (e.g. the `worker` container in
docker-compose.yml, running alongside the `api` container) opening the
same file would crash immediately with "IO Error: Cannot open file ...
already open in another process" -- confirmed empirically during this
project's own earlier testing (two of the *author's own* processes on
this exact machine hit it).

The fix (services/duckdb_service.py): every connection is now short-lived
(opened, used, closed within one operation), guarded by a cross-process
`filelock.FileLock`. This test proves that fix with REAL separate OS
processes (multiprocessing.Process, not threads -- threads share one
process's file handles and would not have reproduced the original bug at
all) hammering the same DuckDB file concurrently.
"""

import multiprocessing
import uuid
from pathlib import Path

import pandas as pd
import pytest


def _register_dataset_in_subprocess(db_path: str, dataset_id: str, region: str, out_queue) -> None:
    """Runs in a separate OS process (spawned, not forked -- must be
    top-level/importable for pickling on Windows). Builds its own
    DuckDBService against the SAME file and registers a small dataset."""

    import os

    os.environ.setdefault("OPENAI_API_KEY", "test-key")

    from app.core.config import Settings
    from app.services.duckdb_service import DuckDBService

    settings = Settings(openai_api_key="test-key", duckdb_path=db_path, duckdb_lock_timeout_seconds=15.0)
    service = DuckDBService(settings)
    dataframe = pd.DataFrame({"region": [region], "revenue": [100.0]})

    try:
        service.register_dataset(dataset_id, dataframe)
        schema = service.get_schema(dataset_id)
        out_queue.put(("ok", dataset_id, [c.name for c in schema.columns]))
    except Exception as exc:  # noqa: BLE001 -- reporting the failure back to the parent, not re-raising across a process boundary
        out_queue.put(("error", dataset_id, repr(exc)))


def test_two_separate_processes_can_register_datasets_concurrently(tmp_path) -> None:
    """Reproduces the exact scenario docker-compose.yml's `api` and
    `worker` containers create: two independent OS processes touching
    the same DuckDB file at (as close to) the same time as this test can
    arrange. Before the fix, this reliably crashed one side with
    "already open in another process"; after it, both succeed."""

    db_path = str(tmp_path / "concurrency_test.duckdb")
    ctx = multiprocessing.get_context("spawn")
    out_queue = ctx.Queue()

    dataset_id_a = str(uuid.uuid4())
    dataset_id_b = str(uuid.uuid4())

    process_a = ctx.Process(target=_register_dataset_in_subprocess, args=(db_path, dataset_id_a, "Delhi", out_queue))
    process_b = ctx.Process(target=_register_dataset_in_subprocess, args=(db_path, dataset_id_b, "Mumbai", out_queue))

    process_a.start()
    process_b.start()
    process_a.join(timeout=60)
    process_b.join(timeout=60)

    assert process_a.exitcode == 0, "subprocess A did not exit cleanly"
    assert process_b.exitcode == 0, "subprocess B did not exit cleanly"

    results = [out_queue.get(timeout=5), out_queue.get(timeout=5)]

    assert len(results) == 2, f"expected 2 results, got {results}"
    for status, dataset_id, detail in results:
        assert status == "ok", f"dataset {dataset_id} failed: {detail}"

    # Real proof this isn't a fluke: both tables genuinely exist in the
    # SAME file afterward, registered by two DIFFERENT processes.
    import duckdb

    conn = duckdb.connect(db_path, read_only=True)
    try:
        tables = {row[0] for row in conn.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    finally:
        conn.close()
    assert f"dataset_{dataset_id_a.replace('-', '')}" in tables
    assert f"dataset_{dataset_id_b.replace('-', '')}" in tables
