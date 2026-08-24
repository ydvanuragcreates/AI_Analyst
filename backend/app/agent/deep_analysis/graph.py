"""
Phase 10: the autonomous deep-analysis graph.

    START
      |
      v
    dataset_context  (schema, budget/trace/timer init -- deterministic)
      |
      v
    data_profile     (section 1 -- deterministic)
      |
      v
    data_quality_check  (section 2 -- deterministic)
      |
      v
    planner          (section 3/4 -- LLM, grounded + dataset-aware filtered)
      |
      v
    hypothesis_generator  (section 5 -- LLM, grounded + filtered)
      |
      v
    analysis_executor     (sections 6-10 -- deterministic dispatcher over
      |                     trend/anomaly/correlation/segmentation +
      |                     hypothesis testing)
      |
      +--[budget exceeded]------------------------------.
      |                                                  |
      v                                                  |
    insight_engine   (sections 11-14 -- LLM narrative,   |
      |                deterministic evidence+confidence) |
      v                                                  |
    evidence_validator (structural check -- deterministic)|
      |                                                  |
      v                                                  |
    reviewer  (sections 15/16 -- LLM + deterministic      |
      |        contradiction/causal-language override)    |
      |                                                   |
      +--[FAIL, retries left]--> insight_engine           |
      |                                                   |
      +--[PASS, or retries exhausted]--------------------+
                                                           |
                                                           v
                                                  report_generator
                                                  (sections 20-22 -- LLM
                                                   narrative, deterministic
                                                   structure/limitations)
                                                           |
                                                           v
                                                  chart_recommendation
                                                  (sections 23/24 -- deterministic)
                                                           |
                                                           v
                                                        finalize
                                                           |
                                                          END

This is a NEW, separate graph -- it does not touch or replace
build_multi_agent_graph (Phase 8) or any earlier graph in agent/graph.py.
It reuses the same LangGraph primitives (StateGraph, conditional edges,
a checkpointer) and the same `get_structured_model_for(schema)` factory
every other graph in this project already uses (agent/nodes.py) --
extension, not reinvention.
"""

from functools import lru_cache

from langchain_core.runnables import Runnable
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.deep_analysis.nodes import (
    analysis_executor_node,
    build_deep_reviewer_node,
    build_hypothesis_generator_node,
    build_insight_engine_node,
    build_planner_node,
    build_report_generator_node,
    chart_recommendation_node,
    data_profile_node,
    data_quality_node,
    dataset_context_node,
    evidence_validator_node,
    finalize_node,
    route_after_deep_reviewer,
    route_after_executor,
)
from app.agent.deep_analysis.state import DeepAnalysisState
from app.agent.nodes import get_structured_model_for
from app.schemas.deep_analysis import DeepAnalysisPlan, DeepReviewResult, HypothesisList, InsightNarrative, ReportNarrative


def build_deep_analysis_graph(
    planning_model: Runnable | None = None,
    hypothesis_model: Runnable | None = None,
    insight_model: Runnable | None = None,
    reviewer_model: Runnable | None = None,
    report_model: Runnable | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Construct and compile the Phase 10 deep-analysis graph.

    Five separate structured-output configurations of get_chat_model(),
    one per stage that calls a model at all -- planner, hypothesis
    generator, insight engine (narrative only), reviewer, report
    generator. Every `*_model` parameter is optional so tests can inject
    FakeStructuredModel for any subset (same pattern as
    build_multi_agent_graph); production code
    (get_deep_analysis_graph) leaves all unset and gets real, cached,
    per-schema models via the SAME get_structured_model_for factory
    Phase 6-8 already use -- not a second, duplicate model factory.

    `checkpointer` defaults to a fresh InMemorySaver() per call for test
    isolation, exactly like every other graph builder in this project;
    production code is cached below so the checkpointer persists across
    requests for the process's lifetime.
    """

    planning_model = planning_model if planning_model is not None else get_structured_model_for(DeepAnalysisPlan)
    hypothesis_model = hypothesis_model if hypothesis_model is not None else get_structured_model_for(HypothesisList)
    insight_model = insight_model if insight_model is not None else get_structured_model_for(InsightNarrative)
    reviewer_model = reviewer_model if reviewer_model is not None else get_structured_model_for(DeepReviewResult)
    report_model = report_model if report_model is not None else get_structured_model_for(ReportNarrative)
    checkpointer = checkpointer if checkpointer is not None else InMemorySaver()

    graph = StateGraph(DeepAnalysisState)
    graph.add_node("dataset_context", dataset_context_node)
    graph.add_node("data_profile", data_profile_node)
    graph.add_node("data_quality_check", data_quality_node)
    graph.add_node("planner", build_planner_node(planning_model))
    graph.add_node("hypothesis_generator", build_hypothesis_generator_node(hypothesis_model))
    graph.add_node("analysis_executor", analysis_executor_node)
    graph.add_node("insight_engine", build_insight_engine_node(insight_model))
    graph.add_node("evidence_validator", evidence_validator_node)
    graph.add_node("reviewer", build_deep_reviewer_node(reviewer_model))
    graph.add_node("report_generator", build_report_generator_node(report_model))
    graph.add_node("chart_recommendation", chart_recommendation_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "dataset_context")
    graph.add_edge("dataset_context", "data_profile")
    graph.add_edge("data_profile", "data_quality_check")
    graph.add_edge("data_quality_check", "planner")
    graph.add_edge("planner", "hypothesis_generator")
    graph.add_edge("hypothesis_generator", "analysis_executor")

    graph.add_conditional_edges(
        "analysis_executor", route_after_executor, {"insight_engine": "insight_engine", "report_generator": "report_generator"}
    )
    graph.add_edge("insight_engine", "evidence_validator")
    graph.add_edge("evidence_validator", "reviewer")
    graph.add_conditional_edges(
        "reviewer", route_after_deep_reviewer, {"insight_engine": "insight_engine", "report_generator": "report_generator"}
    )

    graph.add_edge("report_generator", "chart_recommendation")
    graph.add_edge("chart_recommendation", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)


@lru_cache
def get_deep_analysis_graph() -> CompiledStateGraph:
    """Return the cached, compiled Phase 10 deep-analysis graph -- same
    @lru_cache pattern as get_multi_agent_graph/get_analysis_workflow_graph,
    for the same reason: the checkpointer must persist across requests."""

    return build_deep_analysis_graph()
