"""
Phase 10: the autonomous deep-analysis pipeline.

Extends the existing LangGraph architecture (Phase 1-9's graphs in
app/agent/graph.py are untouched) with a NEW, separate graph --
build_deep_analysis_graph() below -- for a genuinely different job than
the Q&A graphs: instead of answering one question, it autonomously
profiles a dataset, checks its quality, generates and tests hypotheses,
runs deterministic trend/anomaly/correlation/segmentation analysis, turns
the results into evidence-first insights, reviews them for contradictions
and unsupported causal language, and produces a professional report.

See this package's graph.py for the full node/edge wiring and a diagram
matching the Phase 10 spec's own flow.
"""
