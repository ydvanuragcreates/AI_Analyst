"""
Pydantic schemas for the analytical query API (Phase 3), the
tool-calling / structured-output agent API (Phase 4/5), the
deterministic SQL workflow (Phase 6), multi-step analysis + conversation
+ visualization (Phase 7), and the multi-agent + human-in-the-loop
architecture (Phase 8).

These are the typed contracts DuckDBService/the agent graph return
directly and FastAPI serializes — no arbitrary dicts and no raw DuckDB
or LangChain objects ever reach a route or a client.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.review import ReviewResult
from app.schemas.visualization import ChartData, ChartType


class QueryRequest(BaseModel):
    """Request body for POST /api/analysis/query."""

    dataset_id: str
    query: str = Field(
        ...,
        min_length=1,
        description=(
            "A single read-only SELECT/WITH statement. Refer to the dataset "
            "as table name 'dataset', e.g. \"SELECT * FROM dataset LIMIT 10\" "
            "-- the real internal table name is never exposed or accepted."
        ),
    )


class QueryResult(BaseModel):
    """Response body for POST /api/analysis/query."""

    dataset_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    execution_time_ms: float
    truncated: bool = Field(
        default=False,
        description="True if the query matched more rows than MAX_QUERY_ROWS and the result was capped",
    )


class SchemaColumn(BaseModel):
    """A single column's name and DuckDB type."""

    name: str
    type: str
    sample_values: list[str] | None = Field(
        default=None,
        description=(
            "The full set of distinct values, only populated for low-cardinality text "
            "columns (see DuckDBService._distinct_values) -- lets the SQL-generation "
            "prompt disambiguate near-duplicate categorical columns (e.g. two different "
            "'state' columns using different labels for the same place) without ever "
            "exposing row-level data. None for anything high-cardinality (free text, "
            "IDs, PII) or non-text."
        ),
    )


class DatasetSchema(BaseModel):
    """Response body for GET /api/analysis/{dataset_id}/schema."""

    dataset_id: str
    table_name: str
    columns: list[SchemaColumn]


class SampleDataResponse(BaseModel):
    """Response body for GET /api/analysis/{dataset_id}/sample."""

    dataset_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int


class AgentQueryRequest(BaseModel):
    """Request body for POST /api/analysis."""

    dataset_id: str
    question: str = Field(..., min_length=1, description="A natural-language question about the dataset")
    debug: bool = Field(
        default=False,
        description="If true, the response includes a 'debug' field with safe workflow execution metadata",
    )
    thread_id: str | None = Field(
        default=None,
        description=(
            "Conversation thread id, from a previous response's `thread_id`, to ask a follow-up question "
            "with context from that earlier turn. Omit to start a new conversation."
        ),
    )


class Metric(BaseModel):
    """A single named number backing the analytical answer."""

    name: str = Field(description="Name of the metric")
    value: str = Field(description="Value of the metric")
    description: str | None = Field(default=None, description="Explanation of the metric")


class QuestionAnalysis(BaseModel):
    """Structured output of the `analyze_question` node.

    This runs *before* the dataset schema is known -- it's triage on the
    question's own wording (plus prior-turn conversation context, if
    any), not a grounded plan. `required_columns` are therefore the
    user's own terms ("revenue"), not confirmed real column names;
    AnalysisPlan (below) is what grounds this against the real schema.
    """

    intent: str = Field(
        description="The kind of analysis this question requires, e.g. aggregation, ranking, "
        "comparison, trend, lookup, or unanswerable"
    )
    required_columns: list[str] = Field(
        description="Column-like concepts the question refers to, in the user's own words"
    )
    operation: str | None = Field(
        default=None, description="Aggregation implied by the question, if any, e.g. SUM, AVG, COUNT, MIN, MAX"
    )
    group_by: str | None = Field(default=None, description="What the result should be grouped by, if anything")
    sort: str | None = Field(default=None, description="ASC or DESC, if the question implies an ordering")
    limit: int | None = Field(default=None, description="Row limit implied by the question, e.g. 1 for 'highest'")
    is_answerable: bool = Field(
        description="Whether this looks like a question about the dataset's data at all"
    )
    reasoning: str = Field(description="One brief sentence explaining the intent/answerability judgment")
    requires_multiple_steps: bool = Field(
        description="Whether answering this fully needs more than one SQL query run in sequence "
        "(e.g. comparing two periods, deriving growth) as opposed to a single aggregation"
    )
    suggested_chart_type: ChartType | None = Field(
        default=None,
        description="A chart type that would help visualize the answer, or null if a chart wouldn't add "
        "anything (e.g. a single total). Only a *type* -- never chart values; those come from the "
        "actual query result, deterministically, never from you.",
    )


class AnalysisStep(BaseModel):
    """One step of an AnalysisPlan -- what to calculate, not how (no SQL here).

    Deliberately simpler than Phase 6's SQLPlan (no separate aggregations/
    filters/group_by/order_by breakdown): each step already narrows scope
    to one focused objective, so generate_step_sql can go straight from
    `objective` + `required_columns` + schema to SQL without an extra
    layer of structured indirection.
    """

    step_id: int = Field(description="1-based position of this step within the plan")
    objective: str = Field(description="What this specific step needs to calculate or retrieve")
    required_columns: list[str] = Field(
        default_factory=list, description="Real schema columns this step needs"
    )
    depends_on: list[int] = Field(
        default_factory=list, description="step_ids whose results this step builds on, if any"
    )
    completed: bool = Field(default=False, description="Whether this step has successfully executed")


class AnalysisPlan(BaseModel):
    """Structured output of the `create_analysis_plan` node (the Planner agent, Phase 8).

    A simple question ("what is total revenue?") should produce exactly
    one step; only a genuinely multi-part question (comparing periods,
    computing derived metrics from separate aggregations) should produce
    more. Capped at Settings.max_analysis_steps by the node itself if the
    model over-produces -- see build_create_analysis_plan_node.

    `complexity` is a Phase 8 addition: the Supervisor's own assessment
    (simple/moderate/complex) is separate from this -- SupervisorDecision
    decides *whether* to invoke the Planner at all (skipped for simple
    questions, which get a deterministic 1-step plan instead -- see
    agent/nodes.py::build_trivial_plan_node); this field is the Planner's
    own judgment once it *does* run, useful for debug/observability.
    """

    objective: str = Field(description="One sentence describing the overall analysis goal")
    steps: list[AnalysisStep] = Field(
        description="1 step for a simple question; more only when the question genuinely requires it"
    )
    complexity: Literal["simple", "moderate", "complex"] = Field(
        default="simple", description="The Planner's own assessment of how involved this analysis turned out to be"
    )


class SupervisorDecision(BaseModel):
    """Structured output of the Supervisor agent (agent/supervisor.py).

    The Supervisor's ONLY job is routing -- it never performs SQL
    analysis or interpretation itself. This is the routing contract every
    conditional edge after the supervisor node reads.
    """

    complexity: Literal["simple", "moderate", "complex"] = Field(
        description="Overall assessment of how involved this question is to answer"
    )
    requires_planning: bool = Field(
        description="Whether a genuine multi-step AnalysisPlan is needed (Planner agent), as opposed to a "
        "single-query question that gets a trivial 1-step plan without an extra LLM call"
    )
    requires_data_analysis: bool = Field(
        description="Whether the question needs real interpretation (trends, comparisons, anomalies) beyond "
        "restating a single number"
    )
    is_answerable: bool = Field(description="Whether this looks like a question about the dataset's data at all")
    reasoning: str = Field(description="One brief sentence explaining the routing decision")


class SQLAnalysisResult(BaseModel):
    """Summary output of the SQL Analyst subgraph (agent/subgraphs/sql_workflow.py).

    Built deterministically (not by an LLM) from the subgraph's own
    ExecutedQuery history after it finishes -- see
    agent/graph.py::_summarize_sql_analysis. Exists so the Data Analyst
    and Reviewer receive one clean, typed summary of "what SQL actually
    ran and what happened" instead of reaching into raw executed_queries
    state directly.
    """

    executed_queries: list[str] = Field(description="Every SQL query that actually executed, in order")
    results: list[dict[str, Any]] = Field(
        description="One summary dict per executed query: {step_id, row_count, columns}"
    )
    success: bool = Field(description="Whether at least one query executed and its result was judged sufficient")
    errors: list[str] = Field(default_factory=list, description="Failure reasons from steps that never succeeded")


class AnalyticalFindings(BaseModel):
    """Structured output of the Data Analyst agent (agent/data_analyst.py).

    Turns real SQL_Analyst results into a natural-language interpretation
    -- trends, comparisons, anomalies -- grounded ONLY in
    SQLAnalysisResult and the dataset schema, never invented. This is an
    intermediate artifact, reviewed by the Reviewer before becoming the
    final AnalysisResponse (which additionally has sql_used/chart/
    follow_up_suggestions filled in deterministically or by the final
    response stage, not by the Data Analyst).
    """

    answer: str = Field(description="Concise answer to the user's question")
    key_findings: list[str] = Field(description="Important findings directly supported by the query results")
    metrics: list[Metric] = Field(default_factory=list, description="Important metrics supporting the answer")
    anomalies: list[str] = Field(
        default_factory=list, description="Unexpected patterns worth calling out, if any (empty list otherwise)"
    )
    confidence: float = Field(
        description="How strongly the query results support this interpretation (0-1)", ge=0.0, le=1.0
    )


class GeneratedSQL(BaseModel):
    """Structured output of the `generate_step_sql` node.

    A single-field wrapper rather than plain text: forcing this through
    with_structured_output() is what guarantees the `sql` field is just
    the query, never wrapped in markdown fences or prose commentary --
    a real, common failure mode of asking a model for "just the SQL" in
    free text.
    """

    sql: str = Field(description="The generated SQL query text, and nothing else")


class ExecutedQuery(BaseModel):
    """One step's actual, successfully-executed SQL and its real result.

    This -- never the model's memory -- is the only source for
    AnalysisResponse.sql_used and for ChartData.data (see
    build_chart_data_node and build_generate_final_response_node).
    """

    step_id: int
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    execution_time_ms: float


class ResultValidation(BaseModel):
    """Structured output of the `validate_step_result` node.

    A judgment about whether one step's result satisfies *that step's*
    objective -- separate from whether the query executed without error
    (that's execute_step_sql's job). A query can succeed and still not
    produce what the step needed (empty result, or missing information).
    """

    is_sufficient: bool = Field(description="Whether the query result satisfies this step's objective")
    reason: str = Field(description="Brief explanation of the judgment")
    missing_information: list[str] = Field(
        default_factory=list, description="What information is missing, if insufficient"
    )
    confidence: float = Field(
        description="Confidence in this sufficiency judgment (0-1)", ge=0.0, le=1.0
    )


class ConversationContext(BaseModel):
    """A deliberately small, curated summary carried forward between
    conversation turns via the LangGraph checkpointer -- NOT the full raw
    working state (plan, retries, every query result) of the previous
    turn. See agent/graph.py for how thread_id/checkpointing wire this up,
    and agent/nodes.py::build_generate_final_response_node for how this
    gets populated at the end of each turn.
    """

    previous_question: str | None = None
    previous_answer: str | None = None
    relevant_findings: list[str] = Field(default_factory=list)


class WorkflowDebugInfo(BaseModel):
    """Safe, structured execution metadata for AnalysisResponse.debug.

    Shared across the Phase 6/7 workflow graph and the Phase 8 multi-agent
    graph -- whichever one produced a given response, only the fields
    that graph actually populates are non-default; the rest stay at their
    defaults. No chain-of-thought/raw prompts are included anywhere,
    only the structured artifacts each node/agent itself already produces.
    """

    question_analysis: QuestionAnalysis | None = None
    schema_columns: list[str] = Field(default_factory=list)
    analysis_plan: AnalysisPlan | None = None
    current_step_index: int = 0
    generated_sql: str | None = None
    validated_sql: str | None = None
    executed_queries: list[ExecutedQuery] = Field(default_factory=list)
    step_result_validation: ResultValidation | None = None
    retry_count: int = 0

    # Phase 8 additions (multi-agent graph only):
    current_agent: str | None = Field(default=None, description="Which agent produced the state this snapshot reflects")
    supervisor_decision: SupervisorDecision | None = None
    review: ReviewResult | None = None
    review_retry_count: int = Field(default=0, description="Data Analyst <-> Reviewer correction cycles so far")
    human_approval_required: bool = False
    human_approved: bool | None = None


class AnalysisResponse(BaseModel):
    """Response body for POST /api/analysis (Phase 5, extended in Phase 6/7).

    This is what model.with_structured_output(AnalysisResponse) forces
    the model's final reply to conform to -- FastAPI declares it as
    response_model, so an instance of this class (not an arbitrary dict)
    is the only thing that endpoint can ever return.
    """

    answer: str = Field(description="Concise answer to the user's question")
    key_findings: list[str] = Field(description="Important findings supported by the dataset")
    metrics: list[Metric] = Field(
        default_factory=list, description="Important metrics supporting the answer"
    )
    sql_used: list[str] = Field(
        default_factory=list,
        description="Every SQL query actually executed to produce this answer, in the order they ran. "
        "Empty if no query executed successfully.",
    )
    confidence: float = Field(
        description="How strongly the available dataset evidence supports the answer (0-1, not a statistical probability)",
        ge=0.0,
        le=1.0,
    )
    chart: ChartData | None = Field(
        default=None,
        description="Visualization-ready chart data, built deterministically from actual query results -- "
        "null when a chart wouldn't add anything to the answer",
    )
    follow_up_suggestions: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Up to 3 relevant follow-up questions grounded in this dataset's actual schema",
    )
    thread_id: str | None = Field(
        default=None,
        description="This conversation's thread id -- pass it back as `thread_id` in a follow-up request",
    )
    debug: WorkflowDebugInfo | None = Field(
        default=None,
        description="Present only when the request set debug=true; safe workflow execution metadata, never chain-of-thought",
    )


class ApprovalRequiredResponse(BaseModel):
    """Alternate response body for POST /api/analysis (Phase 8) when the
    graph interrupts for human approval instead of completing.

    FastAPI's response_model for that route is a Union of this and
    AnalysisResponse -- see api/routes/analysis.py. Deliberately doesn't
    include any analytical content (no partial answer/findings): the
    whole point is that nothing is returned to the end user until a human
    has signed off.
    """

    status: Literal["approval_required"] = "approval_required"
    reason: str = Field(description="Why human approval is needed, e.g. 'confidence 0.62 is below the 0.70 threshold'")
    thread_id: str = Field(description="Pass this to POST /api/analysis/{thread_id}/approve to resume")


class ApprovalRequest(BaseModel):
    """Request body for POST /api/analysis/{thread_id}/approve."""

    approved: bool
    feedback: str | None = Field(
        default=None,
        description="Required guidance when approved=false, e.g. 'focus on regional revenue' -- fed back into "
        "the Planner for a corrected attempt, not discarded",
    )


class AnalysisStatusResponse(BaseModel):
    """Response body for GET /api/analysis/{thread_id}/status."""

    thread_id: str
    status: Literal["running", "waiting_for_approval", "completed", "failed"]
    reason: str | None = Field(default=None, description="Present when status is 'waiting_for_approval'")
