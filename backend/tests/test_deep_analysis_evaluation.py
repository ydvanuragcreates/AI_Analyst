"""
Runs the Phase 10 evaluation framework (app/evaluation/) as part of the
automated test suite, so its pass/fail is checked on every run, not just
when someone remembers to run scripts/run_evaluation.py by hand.
"""

from app.evaluation.adversarial import run_adversarial_evaluation
from app.evaluation.benchmark import run_benchmark


def test_deterministic_benchmark_cases_all_pass() -> None:
    report = run_benchmark()
    failures = [(r.case.question, r.actual, r.case.expected, r.error) for r in report.results if not r.passed]
    assert not failures, f"Benchmark failures: {failures}"
    assert report.accuracy == 1.0


def test_causal_language_detector_meets_accuracy_bar() -> None:
    results = {r.name: r for r in run_adversarial_evaluation()}
    causal = results["causal_language_detection"]
    assert causal.accuracy >= 0.85, f"{causal}"
    assert causal.false_positives == 0, "must never flag legitimate association/segmentation language as causal"


def test_contradiction_detector_meets_accuracy_bar() -> None:
    results = {r.name: r for r in run_adversarial_evaluation()}
    contradiction = results["contradiction_detection"]
    assert contradiction.accuracy >= 0.85, f"{contradiction}"
    assert contradiction.false_positives == 0, "must never flag a consistent trend claim as contradictory"
