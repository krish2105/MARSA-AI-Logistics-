"""Routing accuracy — did the classifier send each query to the right path?

The spec asks for this number *and* for any systematic misclassification
pattern to be reported honestly, naming over-routing to the agentic path as a
plausible and interesting finding. So this does not stop at an accuracy
percentage: it computes the full confusion matrix, per-class precision and
recall, accuracy split by difficulty, and an explicit directional-bias check.

A single accuracy figure over a set containing both trivial and genuinely
ambiguous queries is the least informative number available. The breakdowns are
the point.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from marsa.eval.dataset import LABELLED_QUERIES, Difficulty, LabelledQuery
from marsa.router.classifier import QueryClass

CLASSES = [QueryClass.SIMPLE_FACTUAL, QueryClass.MULTI_HOP, QueryClass.RELATIONSHIP]

#: Relative computational cost of each path, for the bias-direction analysis.
#: Routing a simple query to the agentic path wastes budget; routing a complex
#: query to the fast path returns a thin answer. Both are errors, but they are
#: not the same error, and the spec cares which way the classifier leans.
PATH_EXPENSE = {
    QueryClass.SIMPLE_FACTUAL: 1,
    QueryClass.RELATIONSHIP: 2,
    QueryClass.MULTI_HOP: 3,
}


@dataclass
class Prediction:
    query: str
    expected: QueryClass
    predicted: QueryClass
    confidence: float
    difficulty: Difficulty
    rationale: str
    latency_ms: float = 0.0

    @property
    def correct(self) -> bool:
        return self.expected is self.predicted

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "expected": self.expected.value,
            "predicted": self.predicted.value,
            "correct": self.correct,
            "confidence": round(self.confidence, 4),
            "difficulty": self.difficulty.value,
            "latencyMs": round(self.latency_ms, 3),
        }


@dataclass
class RoutingReport:
    predictions: list[Prediction] = field(default_factory=list)
    classifier_method: str = "unknown"
    classifier_is_llm: bool = False

    # ── headline ───────────────────────────────────────────────────────────
    @property
    def accuracy(self) -> float:
        if not self.predictions:
            return 0.0
        return sum(p.correct for p in self.predictions) / len(self.predictions)

    @property
    def baseline_accuracy(self) -> float:
        """Always predicting the largest class. The floor any classifier must clear."""
        if not self.predictions:
            return 0.0
        counts = Counter(p.expected for p in self.predictions)
        return max(counts.values()) / len(self.predictions)

    # ── breakdowns ─────────────────────────────────────────────────────────
    def confusion(self) -> dict[str, dict[str, int]]:
        matrix = {a.value: dict.fromkeys((b.value for b in CLASSES), 0) for a in CLASSES}
        for p in self.predictions:
            matrix[p.expected.value][p.predicted.value] += 1
        return matrix

    def per_class(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for target in CLASSES:
            tp = sum(1 for p in self.predictions if p.expected is target and p.correct)
            fp = sum(
                1 for p in self.predictions if p.predicted is target and p.expected is not target
            )
            fn = sum(
                1 for p in self.predictions if p.expected is target and p.predicted is not target
            )
            precision = tp / (tp + fp) if (tp + fp) else 0.0
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
            out[target.value] = {
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
                "support": tp + fn,
            }
        return out

    def by_difficulty(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for level in Difficulty:
            subset = [p for p in self.predictions if p.difficulty is level]
            if not subset:
                continue
            out[level.value] = {
                "accuracy": round(sum(p.correct for p in subset) / len(subset), 4),
                "count": len(subset),
            }
        return out

    def confidence_calibration(self) -> dict[str, float]:
        """Is the classifier more confident when it is right?

        A classifier whose confidence carries no signal makes the Route Badge's
        confidence figure decorative — the number is shown to users as if it
        means something, so it should.
        """
        correct = [p.confidence for p in self.predictions if p.correct]
        wrong = [p.confidence for p in self.predictions if not p.correct]
        mean = lambda xs: sum(xs) / len(xs) if xs else 0.0  # noqa: E731
        return {
            "meanConfidenceCorrect": round(mean(correct), 4),
            "meanConfidenceWrong": round(mean(wrong), 4),
            "separation": round(mean(correct) - mean(wrong), 4),
        }

    def bias(self) -> dict[str, Any]:
        """Which direction does the classifier lean when it is wrong?

        The spec explicitly flags over-routing to the agentic path as a
        plausible finding worth reporting, so the direction of error is
        measured rather than left to inspection.
        """
        errors = [p for p in self.predictions if not p.correct]
        if not errors:
            return {"errors": 0, "note": "No misroutes."}

        over = sum(1 for p in errors if PATH_EXPENSE[p.predicted] > PATH_EXPENSE[p.expected])
        under = len(errors) - over
        destinations = Counter(p.predicted.value for p in errors)

        if over > under * 1.5:
            note = (
                f"Leans expensive: {over} of {len(errors)} misroutes went to a costlier "
                "path than needed. That wastes the budget the router exists to conserve."
            )
        elif under > over * 1.5:
            note = (
                f"Leans cheap: {under} of {len(errors)} misroutes went to a cheaper path "
                "than needed, which returns thin answers to questions that deserved more."
            )
        else:
            note = f"No strong directional bias ({over} expensive, {under} cheap)."

        return {
            "errors": len(errors),
            "toMoreExpensive": over,
            "toCheaper": under,
            "destinations": dict(destinations),
            "note": note,
        }

    def worst_cases(self, limit: int = 8) -> list[dict[str, Any]]:
        """Confident mistakes first — those are the ones that mislead a user."""
        errors = sorted(
            (p for p in self.predictions if not p.correct),
            key=lambda p: -p.confidence,
        )
        return [
            {**p.as_dict(), "labelRationale": p.rationale} for p in errors[:limit]
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "classifier": {"method": self.classifier_method, "isLlm": self.classifier_is_llm},
            "n": len(self.predictions),
            "accuracy": round(self.accuracy, 4),
            "baselineAccuracy": round(self.baseline_accuracy, 4),
            "liftOverBaseline": round(self.accuracy - self.baseline_accuracy, 4),
            "confusion": self.confusion(),
            "perClass": self.per_class(),
            "byDifficulty": self.by_difficulty(),
            "confidence": self.confidence_calibration(),
            "bias": self.bias(),
            "worstCases": self.worst_cases(),
        }


def evaluate_routing(
    classifier, queries: list[LabelledQuery] | None = None
) -> RoutingReport:
    """Classify every labelled query and score the result."""
    import time

    items = queries if queries is not None else LABELLED_QUERIES
    report = RoutingReport(
        classifier_method=getattr(classifier, "method", "unknown"),
        classifier_is_llm=bool(getattr(classifier, "is_llm", False)),
    )

    for item in items:
        started = time.perf_counter()
        result = classifier.classify(item.query)
        elapsed = (time.perf_counter() - started) * 1000

        report.predictions.append(
            Prediction(
                query=item.query,
                expected=item.expected,
                predicted=result.query_class,
                confidence=result.confidence,
                difficulty=item.difficulty,
                rationale=item.rationale,
                latency_ms=elapsed,
            )
        )

    return report
