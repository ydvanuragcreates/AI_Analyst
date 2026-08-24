"""
Phase 10, sections 35-38: a small, genuinely-runnable evaluation
framework for the deep-analysis pipeline.

Two kinds of thing are evaluated here, deliberately kept separate because
only one of them can actually be measured in this environment:

- benchmark.py: deterministic-computation correctness (the "SQL
  correctness" / "numerical correctness" axis from section 36) --
  profiling/trend/anomaly/correlation/segmentation answers checked
  against hand-computed expected values on the golden datasets
  (tests/fixtures/golden_sales.csv, golden_no_date.csv). No LLM call is
  involved in producing these numbers at all (see services/
  profiling_service.py, statistics_service.py), so this can be measured
  exactly and repeatably, with no API key or network needed.

- adversarial.py: the safety nets that guard against exactly the failure
  modes section 38 calls out (unsupported causal claims, contradictions)
  -- measured against a small labeled set of insight texts, run through
  the real deterministic detectors in agent/deep_analysis/nodes.py.

What is explicitly NOT measured here, and must not be reported as a
number: end-to-end insight quality/correctness, hallucination rate, and
unsupported-claim rate as judged by the actual gpt-5.4-mini model. Those
require a real OPENAI_API_KEY and network access to the OpenAI API, which
this environment does not have configured (no .env file, no real key).
See tests/test_deep_analysis_evaluation.py and the project write-up for
exactly what "TESTED" vs "NOT TESTED" means for this pipeline.
"""
