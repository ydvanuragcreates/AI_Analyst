"""
LangChain tool for executing validated, read-only SQL against a dataset.

This is the most important tool in the project: it is the ONLY path from
"the model wrote some SQL" to "that SQL actually ran." It is a thin
wrapper around DuckDBService.execute_query, which is what enforces the
Phase 3 safety pipeline (validate_query -> render_for_execution ->
row-capped, timeout-bounded execution). Nothing here re-implements or
bypasses that pipeline -- there is exactly one place SQL gets validated
in this project, and this tool goes through it every time:

    LLM-generated SQL -> DuckDBService.execute_query -> sql_utils.validate_query -> DuckDB

never straight to DuckDB.

The returned dict includes the literal `query` text alongside the result.
That's not for the model's benefit -- it's how the Phase 5 "tools" node
(agent/nodes.py::build_tools_node) captures the *actual* executed SQL
into graph state, so the final structured-output stage never has to ask
the model to recall or retype it (see AgentState.executed_sql).
"""

import logging
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.core.config import get_settings
from app.services.duckdb_service import get_duckdb_service
from app.services.exceptions import DatasetServiceError

logger = logging.getLogger(__name__)


@tool
def execute_sql(
    dataset_id: Annotated[str, InjectedState("dataset_id")],
    query: str,
) -> dict[str, Any]:
    """Execute a read-only analytical SQL query against the current dataset.

    The query must be SELECT/WITH based and must refer to the dataset as
    table name 'dataset' (e.g. "SELECT * FROM dataset LIMIT 10") -- the
    real table name is internal and not something you need to know.
    INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, and other write or
    filesystem-access operations are rejected before execution. Results
    are capped to the application's configured row limit.
    """

    settings = get_settings()

    try:
        result = get_duckdb_service().execute_query(
            dataset_id=dataset_id,
            query=query,
            max_rows=settings.max_query_rows,
            timeout_seconds=settings.max_query_execution_seconds,
        )
    except DatasetServiceError as exc:
        # Includes QueryValidationError (unsafe/invalid SQL),
        # QueryExecutionError, QueryTimeoutError, DatasetNotFoundError --
        # all already carry a clean, safe message (see services/exceptions.py
        # and utils/sql_utils.py), so it's returned to the model as-is.
        # This is what lets the model potentially recover: e.g. see
        # "unknown table" and retry with 'dataset' instead.
        return {"error": str(exc)}
    except Exception:
        logger.exception("Unexpected error in execute_sql for dataset %s", dataset_id)
        return {"error": "An unexpected error occurred while executing the query."}

    return {"query": query, **result.model_dump()}
