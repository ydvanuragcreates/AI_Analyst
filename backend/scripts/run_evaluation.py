#!/usr/bin/env python
"""
Runnable evaluation report for the Phase 10 deep-analysis pipeline
(sections 35-36). Prints ONLY measured results -- see
app/evaluation/__init__.py for exactly what is and isn't measurable in
this environment (no real OPENAI_API_KEY/network available here).

Usage (from backend/):
    OPENAI_API_KEY=test-key .venv/Scripts/python.exe scripts/run_evaluation.py
"""

import os
import sys

os.environ.setdefault("OPENAI_API_KEY", "test-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.evaluation.adversarial import run_adversarial_evaluation
from app.evaluation.benchmark import run_benchmark


def main() -> None:
    print("=" * 72)
    print("Phase 10 deep-analysis evaluation report")
    print("=" * 72)

    print("\n-- Deterministic-computation benchmark (section 36: SQL/numerical correctness) --")
    report = run_benchmark()
    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        detail = f"error={result.error}" if result.error else f"actual={result.actual!r} expected={result.case.expected!r}"
        print(f"  [{status}] {result.case.question}  ({detail})")
    print(f"\n  Deterministic-computation accuracy: {report.passed_count}/{report.total} ({report.accuracy:.0%})")

    print("\n-- Adversarial safety-net evaluation (section 38) --")
    for detector_result in run_adversarial_evaluation():
        print(
            f"  {detector_result.name}: accuracy={detector_result.accuracy:.0%} "
            f"(TP={detector_result.true_positives} FP={detector_result.false_positives} "
            f"TN={detector_result.true_negatives} FN={detector_result.false_negatives})"
        )

    print("\n-- NOT TESTED (requires a real OPENAI_API_KEY + network access, unavailable here) --")
    for item in (
        "End-to-end insight narrative correctness (as judged against the real gpt-5.4-mini model)",
        "Hallucination rate / unsupported-claim rate for LLM-written narrative text",
        "Reviewer rejection rate under real model behavior (only the deterministic override path is tested)",
        "Latency and token/cost usage under real model calls",
    ):
        print(f"  - {item}")

    print("\n" + "=" * 72)
    if report.accuracy < 1.0:
        sys.exit(1)


if __name__ == "__main__":
    main()
