"""
SQL safety validation for user-supplied analytical queries.

Parses the query with sqlglot rather than substring/keyword matching
(easy to bypass via comments, string literals, or alternate syntax — e.g.
checking `"drop" in query` would also flag a column literally named
"dropdown") and enforces three rules:

1. Exactly one statement, and its root must be a SELECT (optionally with
   a WITH clause). This alone rejects every DDL/DML/session statement --
   DROP, INSERT, CREATE, ALTER, TRUNCATE, ATTACH, DETACH, COPY, PRAGMA,
   SET, ... -- since sqlglot parses each of those to its own distinct,
   non-Select node type (verified empirically against DuckDB's dialect).

2. No function call may be one sqlglot doesn't recognize as a standard,
   cross-dialect SQL function ("Anonymous", in sqlglot's terms). Every
   DuckDB filesystem/extension function (read_csv, read_parquet, glob,
   pragma_table_info, ...) falls into that bucket, while every ordinary
   analytical function (SUM, COUNT, CAST, ROUND, EXTRACT, ...) does not --
   so this is a whitelist by construction rather than a name blocklist
   that would need updating every time DuckDB adds a new function.

3. The only real table a query may reference is the fixed alias
   `dataset` (CTEs the query defines itself are also fine). Callers never
   see or type the actual internal table name -- see render_for_execution
   below, which swaps `dataset` for the real name via an AST rewrite
   *after* validation passes. This is what makes cross-dataset table
   access impossible: the query text has no way to name any other
   dataset's real table, because it can only ever say "dataset".

Scope note: only a single SELECT/WITH statement is supported. UNION and
other set operations aren't validated here and are rejected, which keeps
the three rules above simple to state and verify.
"""

import sqlglot
from sqlglot import exp

from app.services.exceptions import QueryValidationError

# The table name every query must use to refer to "the dataset this
# request is scoped to" (see QueryRequest.dataset_id). Never the real
# DuckDB table name -- callers don't know it and can't reference it.
DATASET_TABLE_ALIAS = "dataset"


def validate_query(query: str) -> exp.Expression:
    """Parse and validate a user-supplied SQL string.

    Returns the parsed, validated AST (pass it to render_for_execution to
    get executable SQL), or raises QueryValidationError.
    """

    stripped = normalize_identifier_quotes(query.strip().rstrip(";").strip())
    if not stripped:
        raise QueryValidationError("Query must not be empty")

    try:
        statements = [s for s in sqlglot.parse(stripped, read="duckdb") if s is not None]
    except sqlglot.errors.SqlglotError as exc:
        raise QueryValidationError(f"Invalid SQL: {exc}") from exc

    if len(statements) != 1:
        raise QueryValidationError("Only a single SQL statement is allowed")

    statement = statements[0]

    if not isinstance(statement, exp.Select):
        raise QueryValidationError(
            f"Only SELECT/WITH queries are allowed (got a '{type(statement).__name__}' statement)"
        )

    for func in statement.find_all(exp.Func):
        if isinstance(func, exp.Anonymous):
            func_name = func.this if isinstance(func.this, str) else str(func.this)
            raise QueryValidationError(f"Function '{func_name}' is not allowed")

    _validate_table_references(statement)

    return statement


def render_for_execution(statement: exp.Expression, real_table_name: str) -> str:
    """Rewrite the validated `dataset` table reference to the real internal
    table name and render the result back to SQL text, ready to execute.

    Must only be called with an AST that already passed validate_query --
    it trusts that every `dataset` table reference here is legitimate.
    """

    def _rename(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Table) and node.name.lower() == DATASET_TABLE_ALIAS:
            node.set("this", exp.to_identifier(real_table_name, quoted=True))
        return node

    rewritten = statement.transform(_rename)
    return rewritten.sql(dialect="duckdb")


def normalize_identifier_quotes(query: str) -> str:
    """Rewrite MySQL-style `backtick` identifiers as DuckDB "double-quoted" ones.

    LLMs (small local ones especially) often write `State / City` even
    when shown "State / City". DuckDB and sqlglot's DuckDB dialect reject
    backticks outright, so this can never change the meaning of a query
    that would otherwise run -- it only rescues ones that would fail to
    parse. Text inside '...' string literals and "..." identifiers is
    copied unchanged; the result still goes through every validation rule.
    """

    if "`" not in query:
        return query

    out: list[str] = []
    i, n = 0, len(query)
    while i < n:
        ch = query[i]
        if ch in ("'", '"'):
            # Copy a quoted run verbatim; a doubled quote is an escape.
            j = i + 1
            while j < n:
                if query[j] == ch:
                    if j + 1 < n and query[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            out.append(query[i : j + 1])
            i = j + 1
        elif ch == "`":
            end = query.find("`", i + 1)
            if end == -1:  # unbalanced -- leave it for the parser to reject
                out.append(query[i:])
                break
            out.append(quote_identifier(query[i + 1 : end]))
            i = end + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def quote_identifier(identifier: str) -> str:
    """Double-quote a column name for safe interpolation into
    generated (not user-supplied) SQL, escaping any embedded double
    quotes. Used by the Phase 10 deterministic analysis services
    (profiling/data-quality/statistics) when they build aggregate queries
    dynamically from real column names read off DuckDB's own DESCRIBE
    output -- never from user input."""

    return '"' + identifier.replace('"', '""') + '"'


def _validate_table_references(statement: exp.Select) -> None:
    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}

    if DATASET_TABLE_ALIAS in cte_names:
        raise QueryValidationError(
            f"'{DATASET_TABLE_ALIAS}' is a reserved name and cannot be used as a CTE alias"
        )

    allowed = cte_names | {DATASET_TABLE_ALIAS}

    for table in statement.find_all(exp.Table):
        table_name = table.name.lower()
        if not table_name or table_name not in allowed:
            suffix = f" ('{table.name}')" if table.name else ""
            raise QueryValidationError(
                f"Query references an unknown table{suffix}. "
                f"Use '{DATASET_TABLE_ALIAS}' to refer to the requested dataset."
            )
