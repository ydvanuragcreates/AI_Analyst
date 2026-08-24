"""
Section 38: adversarial evaluation of the deterministic safety nets --
contradiction detection and causal-language detection (agent/deep_analysis/
nodes.py::_detect_contradictions / _detect_causal_language). These are
the mechanisms that make "Prove that discounts cause sales" or "Revenue
increased" (when it actually decreased) get caught rather than shipped.

A small, hand-labeled set of realistic insight texts -- true positives
(should be flagged) and true negatives (should NOT be flagged, so a
too-aggressive detector that flags everything doesn't silently "pass").
Both detectors are pure functions with no LLM/DB dependency, so this runs
instantly and is exactly repeatable.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.deep_analysis.nodes import _detect_causal_language, _detect_contradictions
from app.schemas.deep_analysis import ConfidenceBreakdown, Insight


def _insight(source_step_type: str, evidence: dict, finding: str, implication: str = "") -> Insight:
    return Insight(
        title=finding[:40],
        finding=finding,
        evidence=evidence,
        implication=implication or finding,
        confidence=ConfidenceBreakdown(data_completeness=1.0, sample_size_adequacy=1.0, statistical_strength=1.0, consistency=1.0, overall=0.8),
        source_step_type=source_step_type,  # type: ignore[arg-type]
    )


@dataclass
class LabeledCase:
    description: str
    insight: Insight
    should_flag: bool


CAUSAL_LANGUAGE_CASES: list[LabeledCase] = [
    LabeledCase("direct causal claim", _insight("correlation", {"coefficient": 0.8}, "The discount causes higher sales."), True),
    LabeledCase("'leads to' causal claim", _insight("correlation", {"coefficient": 0.7}, "Higher discounts lead to more purchases."), True),
    LabeledCase("'drives' causal claim on a correlation", _insight("correlation", {"coefficient": 0.6}, "This discount drives revenue growth."), True),
    LabeledCase("'because of' causal claim", _insight("correlation", {"coefficient": 0.75}, "Sales rose because of the discount."), True),
    LabeledCase("legitimate association language", _insight("correlation", {"coefficient": 0.8}, "Discount is strongly associated with revenue."), False),
    LabeledCase("legitimate correlation language", _insight("correlation", {"coefficient": 0.8}, "Discount and revenue are positively correlated."), False),
    LabeledCase("'drives' on a segmentation claim (not correlation)", _insight("segmentation", {"segments": []}, "North drives most of total revenue."), False),
    LabeledCase("causal word inside a trend insight (out of scope for this check)", _insight("trend", {"direction": "increasing"}, "Revenue growth is driving expansion plans."), False),
]

CONTRADICTION_CASES: list[LabeledCase] = [
    LabeledCase("claims increase on decreasing evidence", _insight("trend", {"direction": "decreasing"}, "Revenue increased significantly this period."), True),
    LabeledCase("claims decrease on increasing evidence", _insight("trend", {"direction": "increasing"}, "Revenue declined sharply."), True),
    LabeledCase("consistent increase claim", _insight("trend", {"direction": "increasing"}, "Revenue grew steadily over the period."), False),
    LabeledCase("consistent decrease claim", _insight("trend", {"direction": "decreasing"}, "Revenue fell over the period."), False),
    LabeledCase("flat trend, no directional claim", _insight("trend", {"direction": "flat"}, "Revenue stayed roughly constant."), False),
    LabeledCase("non-trend insight is out of scope for this check", _insight("correlation", {"direction": "decreasing"}, "Revenue increased."), False),
]


@dataclass
class DetectorEvalResult:
    name: str
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int

    @property
    def total(self) -> int:
        return self.true_positives + self.false_positives + self.true_negatives + self.false_negatives

    @property
    def accuracy(self) -> float:
        correct = self.true_positives + self.true_negatives
        return correct / self.total if self.total else 0.0


def _evaluate(name: str, cases: list[LabeledCase], detect: callable) -> DetectorEvalResult:
    tp = fp = tn = fn = 0
    for case in cases:
        flagged = bool(detect([case.insight]))
        if case.should_flag and flagged:
            tp += 1
        elif case.should_flag and not flagged:
            fn += 1
        elif not case.should_flag and flagged:
            fp += 1
        else:
            tn += 1
    return DetectorEvalResult(name=name, true_positives=tp, false_positives=fp, true_negatives=tn, false_negatives=fn)


def run_adversarial_evaluation() -> list[DetectorEvalResult]:
    return [
        _evaluate("causal_language_detection", CAUSAL_LANGUAGE_CASES, _detect_causal_language),
        _evaluate("contradiction_detection", CONTRADICTION_CASES, _detect_contradictions),
    ]
