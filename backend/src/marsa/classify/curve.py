"""The abstention curve — the measurement Phase J is accepted on.

ROADMAP §6 asks for "accuracy on answered queries vs abstention rate", and is
explicit that "the useful result is the shape of that trade-off, not a single
number". On thirteen scoreable queries a single number carries a confidence
interval wider than most differences worth detecting, so the curve is the
result and the shipped threshold is a point on it.

Ground truth comes from `marsa.eval.relevance`, which decides relevance from
CBP's own code assignments rather than from anything this system produced:

* a query with a target provision is **answered correctly** when the suggested
  subheading falls under that provision;
* a query the corpus cannot answer is handled correctly when the system
  **abstains** — answering it at all is a failure regardless of what it says;
* a query with no defensible target is excluded, not scored generously.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from marsa.classify.abstain import DEFAULT_THRESHOLD, decide
from marsa.classify.citations import Suggestion
from marsa.eval.relevance import FAST_PATH_RELEVANCE
from marsa.indexing.hybrid import RetrievedRuling

#: Sampled finely enough to show the shape, coarsely enough to stay readable.
THRESHOLDS: tuple[float, ...] = tuple(round(0.05 * i, 2) for i in range(21))


@dataclass
class CurvePoint:
    threshold: float
    answered: int = 0
    correct: int = 0
    abstained: int = 0
    #: Queries the corpus cannot support that were answered anyway. The hard
    #: gate requires this to be zero.
    unsupported_answered: int = 0
    unsupported_total: int = 0

    @property
    def total(self) -> int:
        return self.answered + self.abstained

    @property
    def abstention_rate(self) -> float:
        return self.abstained / self.total if self.total else 0.0

    @property
    def accuracy_on_answered(self) -> float:
        return self.correct / self.answered if self.answered else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "abstentionRate": round(self.abstention_rate, 4),
            "accuracyOnAnswered": round(self.accuracy_on_answered, 4),
            "answered": self.answered,
            "correct": self.correct,
            "abstained": self.abstained,
            "unsupportedAnswered": self.unsupported_answered,
            "unsupportedTotal": self.unsupported_total,
        }


@dataclass
class AbstentionReport:
    points: list[CurvePoint] = field(default_factory=list)
    shipped_threshold: float = DEFAULT_THRESHOLD
    #: Queries that ran, by label kind.
    scored: int = 0
    unanswerable: int = 0
    excluded: int = 0

    def at(self, threshold: float) -> CurvePoint | None:
        for point in self.points:
            if abs(point.threshold - threshold) < 1e-9:
                return point
        return None

    @property
    def gate_passes(self) -> bool:
        """No confident answer on a query the corpus cannot support."""
        point = self.at(self.shipped_threshold)
        return point is not None and point.unsupported_answered == 0

    def as_dict(self) -> dict[str, Any]:
        shipped = self.at(self.shipped_threshold)
        return {
            "shippedThreshold": self.shipped_threshold,
            "gatePasses": self.gate_passes,
            "atShippedThreshold": shipped.as_dict() if shipped else None,
            "curve": [p.as_dict() for p in self.points],
            "dataset": {
                "scored": self.scored,
                "unanswerable": self.unanswerable,
                "excluded": self.excluded,
            },
        }


def is_correct(subheading: str, target: tuple[str, ...]) -> bool:
    """Whether a suggested subheading falls under the query's target provision.

    Prefix comparison, so a chapter-level target is satisfied by any subheading
    in that chapter — matching the granularity the question asked about.
    """
    return any(subheading.startswith(prefix) for prefix in target)


def measure(
    retrieve: Callable[[str], list[RetrievedRuling]],
    *,
    thresholds: tuple[float, ...] = THRESHOLDS,
    shipped: float = DEFAULT_THRESHOLD,
) -> AbstentionReport:
    """Sweep the threshold over the labelled fast-path queries.

    `retrieve` is injected rather than constructed here so the sweep runs
    retrieval **once** per query instead of once per query per threshold — the
    retrieved set does not depend on the threshold, only the decision does.
    """
    report = AbstentionReport(shipped_threshold=shipped)

    prepared: list[tuple[str, list[RetrievedRuling], tuple[str, ...], bool]] = []
    for query, label in FAST_PATH_RELEVANCE.items():
        if not label.is_scored and not label.unanswerable:
            report.excluded += 1
            continue
        try:
            rulings = retrieve(query)
        except Exception:  # noqa: BLE001 — a dead path is not a label problem
            report.excluded += 1
            continue
        prepared.append((query, rulings, label.target, label.unanswerable))
        if label.unanswerable:
            report.unanswerable += 1
        else:
            report.scored += 1

    for threshold in thresholds:
        point = CurvePoint(threshold=threshold)
        for query, rulings, target, unanswerable in prepared:
            outcome = decide(query, rulings, threshold=threshold)
            answered = isinstance(outcome, Suggestion)

            if unanswerable:
                point.unsupported_total += 1
                if answered:
                    point.unsupported_answered += 1
                    point.answered += 1
                    # Never correct: there is nothing in the corpus to be right
                    # about, so any confident answer here is a fabrication.
                else:
                    point.abstained += 1
                continue

            if answered:
                point.answered += 1
                if is_correct(outcome.subheading, target):
                    point.correct += 1
            else:
                point.abstained += 1

        report.points.append(point)

    return report
