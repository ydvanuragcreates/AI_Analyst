"""
LangGraph state definitions.

The state is the typed data structure that flows through every node in a
graph. Each node receives the current state and returns a partial update
to it. Keeping these plain TypedDicts (rather than classes with behavior)
is intentional: LangGraph merges node return values into this shape
between steps, so it should stay a simple, serializable data contract.

Four states live here, for four different graphs:

- AnalystState: the original Phase 1 demo graph (single-turn question ->
  response, no tools). Left exactly as it was -- /api/test-agent still
  uses it unchanged.
- AgentState: the Phase 4 tool-calling graph. It's a *separate* TypedDict
  rather than a bigger AnalystState, because the two graphs are
  structurally different (one is a single Q&A exchange, the other is a
  multi-turn message history with tool calls) -- growing AnalystState to
  cover both would make either graph's actual data contract unclear.
- WorkflowState: the deterministic SQL workflow graph, introduced in
  Phase 6 (single SQL query) and extended in Phase 7 (a plan of 1-5
  steps, each its own SQL query, plus conversation context and
  visualization). Same reasoning as AgentState -- it has no message
  history at all (it's a sequence of typed, single-purpose fields, not a
  conversation transcript), so it gets its own state rather than bolting
  workflow fields onto AgentState. Extended in place rather than forked
  into a new state/graph: Phase 6 -> 7 is a straight-line evolution of
  the same deterministic-workflow paradigm (still no agent, still
  explicit nodes/edges), unlike the Phase 4-vs-6 agent/workflow split,
  which was worth keeping side by side specifically *because* it
  contrasted two different paradigms.
- MultiAgentState (+ SQLAnalystState): the Phase 8 multi-agent graph.
  Named MultiAgentState rather than the spec's literal "AgentState" --
  that name is already taken by Phase 4's tool-calling graph state above,
  in this same module, and reusing it would silently shadow a completely
  different state shape. SQLAnalystState is the narrow slice of fields
  the SQL Analyst *subgraph* actually needs; see its own docstring below
  for why it's a near-identical shape to WorkflowState's SQL-execution
  fields (it's what lets Phase 7's node functions be reused verbatim).
"""

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from app.schemas.analysis import (
    AnalysisPlan,
    AnalysisResponse,
    AnalyticalFindings,
    ConversationContext,
    DatasetSchema,
    ExecutedQuery,
    QuestionAnalysis,
    ResultValidation,
    SupervisorDecision,
)
from app.schemas.review import ReviewResult
from app.schemas.visualization import ChartData


class AnalystState(TypedDict):
    """State passed between nodes in the Phase 1 demo graph."""

    user_question: str
    response: str


class AgentState(TypedDict):
    """State passed between nodes in the Phase 4/5 tool-calling agent graph.

    `messages` uses the `add_messages` reducer: instead of each node's
    return value *replacing* `state["messages"]`, LangGraph *appends* it.
    That's what lets the agent/tools loop accumulate a full transcript
    (HumanMessage -> AIMessage with tool_calls -> ToolMessage -> final
    AIMessage) across multiple round trips instead of each step erasing
    the last one.

    `dataset_id` is set once, from the API request, when the graph is
    invoked (see api/routes/analysis.py) and is then available to every
    node and every tool for the whole run via InjectedState -- see
    agent/tools/dataset_tools.py and analysis_tools.py.

    `executed_sql` and `final_analysis` are Phase 5 additions. Both use
    LangGraph's default "last write wins" merge (no reducer needed): a
    plain value that gets *set*, not accumulated, unlike `messages`.

    - executed_sql: the exact text of the most recent successful
      execute_sql call, captured by the "tools" node directly from the
      tool's own result (not something the final-analysis model is asked
      to remember or reconstruct). None if no query has run yet.
    - final_analysis: the validated AnalysisResponse produced by the
      structured-output stage. None until the graph reaches that node.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    dataset_id: str
    executed_sql: str | None
    final_analysis: AnalysisResponse | None


class WorkflowState(TypedDict):
    """State passed between nodes in the deterministic analysis workflow graph.

    Nothing here uses a LangGraph reducer -- every field is plain "last
    write wins." Fields that need to *accumulate within one run* (like
    `executed_queries` across multiple steps) do so via explicit
    read-modify-write in the node itself (e.g.
    `[*state.get("executed_queries", []), new_entry]`), not an
    `Annotated[..., reducer]` channel. This is deliberate: it means a
    fresh top-level `.invoke()` on the same checkpointed thread_id (a new
    conversation turn) can reset any field simply by passing a new value
    for it -- confirmed empirically that reducer-free fields do NOT merge
    with a prior turn's persisted value the way an `add_messages`-style
    field would. See agent/graph.py for how thread_id/checkpointing use
    this to keep only `conversation_context` deliberately carried forward
    between turns.

    - dataset_id / user_question: set at invocation (from the API
      request; user_question is overwritten fresh each conversation
      turn), read by every node that needs them.
    - conversation_context: the *curated* summary of the previous turn
      (previous question/answer/findings) -- see ConversationContext.
      None on a brand new conversation. This is the one thing worth
      carrying forward; the full raw plan/queries/retries of a previous
      turn are not (see CONVERSATION SAFETY in the project write-up).
    - question_analysis / dataset_schema / analysis_plan /
      step_result_validation: the structured output each corresponding
      node produces. None until that node has run.
    - analysis_plan: 1-5 AnalysisStep objects (1 for a simple question).
      current_step_index tracks which step is currently being worked on;
      completed_steps records the step_ids that finished successfully.
    - generated_sql / validated_sql: the *current step's* SQL text,
      pre/post Phase 3 validation -- reset to None whenever a new step
      begins (see build_generate_step_sql_node). validated_sql is the
      ONLY thing execute_step_sql is ever allowed to run.
    - executed_queries: the growing history of every step's actual
      executed SQL + real result, in order. This -- never the model's
      memory -- is the only source for AnalysisResponse.sql_used and for
      chart data.
    - retry_reason: why the most recent attempt (SQL validation,
      execution, or step-result sufficiency) failed, consumed by the
      next generate_step_sql/create_analysis_plan call so it doesn't
      repeat the same mistake. Cleared at the start of each new
      generate_step_sql call; re-set only if that new attempt also
      fails.
    - retry_count: attempts made *on the current step*, reset to 0 when
      advancing to a new step -- each step gets its own fresh
      MAX_SQL_RETRIES budget. This is what the conditional edges check
      to decide whether another attempt on the current step is allowed,
      which is what makes the retry loop provably finite (see
      agent/nodes.py's route_after_* functions).
    - synthesis_context: a plain-text, controlled summary of
      executed_queries (grouped by step, with each step's objective) --
      built deterministically, not via an LLM call, and used as grounding
      for generate_final_response. This is the "controlled representation"
      that replaces blindly dumping every raw result into the final prompt.
    - chart: built deterministically from an actual executed query's rows
      (see build_chart_data_node) -- the model never invents chart data,
      only (earlier, via QuestionAnalysis.suggested_chart_type) the type.
    - final_response: the validated AnalysisResponse produced by the
      workflow's own final node, whether the workflow succeeded or ran
      out of retries/steps (in the latter case, an honest "couldn't
      answer" response, still grounded in whatever was actually learned).
    """

    dataset_id: str
    user_question: str
    conversation_context: ConversationContext | None

    question_analysis: QuestionAnalysis | None
    dataset_schema: DatasetSchema | None

    analysis_plan: AnalysisPlan | None
    current_step_index: int
    completed_steps: list[int]

    generated_sql: str | None
    validated_sql: str | None
    executed_queries: list[ExecutedQuery]
    step_result_validation: ResultValidation | None

    retry_reason: str | None
    retry_count: int

    synthesis_context: str | None
    chart: ChartData | None

    final_response: AnalysisResponse | None


class SQLAnalystState(TypedDict):
    """State for the Phase 8 SQL Analyst subgraph (agent/subgraphs/sql_workflow.py).

    Deliberately matches the exact field names/types WorkflowState uses
    for its SQL-generation/validation/execution/retry portion -- that's
    what lets the subgraph reuse Phase 7's node functions
    (build_generate_step_sql_node, validate_sql_node, execute_step_sql_node,
    build_validate_step_result_node) and routing functions completely
    unchanged, satisfying "reuse the existing Phase 6 SQL workflow, do not
    duplicate SQL validation logic" literally rather than just in spirit.

    When this subgraph is added as a node in the parent MultiAgentState
    graph, LangGraph automatically shares every field declared here with
    the parent (confirmed empirically): the subgraph reads its slice of
    the parent's state in, and writes its slice back out, with fields the
    parent doesn't declare (there are none here -- see MultiAgentState)
    or the subgraph doesn't declare simply passing through untouched.
    """

    dataset_id: str
    user_question: str
    dataset_schema: DatasetSchema | None

    analysis_plan: AnalysisPlan | None
    current_step_index: int
    completed_steps: list[int]

    generated_sql: str | None
    validated_sql: str | None
    executed_queries: list[ExecutedQuery]
    step_result_validation: ResultValidation | None

    retry_reason: str | None
    retry_count: int


class MultiAgentState(TypedDict):
    """State shared across the Phase 8 Supervisor/Planner/SQL Analyst/Data
    Analyst/Reviewer graph. A strict superset of SQLAnalystState's field
    names (see above) plus each other agent's own contract.

    - current_agent: set by every node when it runs (supervisor, planner,
      sql_analyst, data_analyst, reviewer, human_approval) -- purely for
      observability/debug ("which agent produced this snapshot"), not
      used for routing logic.
    - supervisor_decision: the Supervisor's routing judgment. Read by the
      conditional edge after it, and kept in state afterward only for
      debug visibility.
    - analysis_plan / current_step_index / completed_steps / generated_sql
      / validated_sql / executed_queries / step_result_validation /
      retry_reason / retry_count: the SQL Analyst subgraph's own fields
      (see SQLAnalystState) -- present here so the subgraph can be added
      as a single node in this graph at all.
    - findings: the Data Analyst's structured interpretation of
      executed_queries. Re-generated on each correction cycle if the
      Reviewer rejects it.
    - review / review_retry_count: the Reviewer's judgment and how many
      Data-Analyst-then-Reviewer correction cycles have run so far --
      deliberately a SEPARATE counter from retry_count above (that one is
      the SQL Analyst subgraph's own per-step SQL-generation retry
      budget; conflating the two would let one loop's attempts silently
      consume the other's budget).
    - human_approval_required / human_approval_reason / human_approved /
      human_feedback: the human-in-the-loop gate. human_approved is None
      until a human actually responds (see api/routes/approvals.py);
      human_feedback carries a rejection's guidance into the next
      Planner call rather than being discarded (see agent/graph.py).
    - synthesis_context / chart: same meaning as in WorkflowState -- a
      deterministically-built controlled summary and deterministically-
      built chart, neither ever invented by a model.
    - final_response: set only once, by the last node before END.
    """

    dataset_id: str
    user_question: str
    conversation_context: ConversationContext | None

    current_agent: str | None
    supervisor_decision: SupervisorDecision | None
    dataset_schema: DatasetSchema | None

    analysis_plan: AnalysisPlan | None
    current_step_index: int
    completed_steps: list[int]

    generated_sql: str | None
    validated_sql: str | None
    executed_queries: list[ExecutedQuery]
    step_result_validation: ResultValidation | None

    retry_reason: str | None
    retry_count: int

    findings: AnalyticalFindings | None
    review: ReviewResult | None
    review_retry_count: int

    human_approval_required: bool
    human_approval_reason: str | None
    human_approved: bool | None
    human_feedback: str | None

    synthesis_context: str | None
    chart: ChartData | None

    final_response: AnalysisResponse | None
