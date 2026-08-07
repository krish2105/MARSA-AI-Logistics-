"""Training and comparison harness for late-delivery risk."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from marsa.logging import get_logger
from marsa.ml.dataset import Split, split_is_chronological, temporal_split
from marsa.ml.features import (
    DEFAULT_SPEC,
    LEAKY_SPEC,
    FeatureSpec,
    build_matrix,
    orders_to_frame,
)
from marsa.ml.models import (
    MODEL_BUILDERS,
    ModelResult,
    build_logistic,
    calibration_points,
    evaluate,
    extract_importance,
)

log = get_logger(__name__)


@dataclass
class ComparisonReport:
    results: list[ModelResult] = field(default_factory=list)
    split_sizes: dict[str, int] = field(default_factory=dict)
    split_boundaries: dict[str, str] = field(default_factory=dict)
    base_rates: dict[str, float] = field(default_factory=dict)
    n_features: int = 0
    origin: str = "unknown"
    leakage_demo: ModelResult | None = None

    @property
    def best(self) -> ModelResult | None:
        honest = [r for r in self.results if not r.leaky]
        return max(honest, key=lambda r: r.metrics["pr_auc"]) if honest else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "models": [r.as_dict() for r in self.results],
            "best": self.best.name if self.best else None,
            "splitSizes": self.split_sizes,
            "splitBoundaries": self.split_boundaries,
            "baseRates": self.base_rates,
            "nFeatures": self.n_features,
            "origin": self.origin,
            "leakageDemo": self.leakage_demo.as_dict() if self.leakage_demo else None,
        }


def _fit_and_score(
    name: str,
    model: Any,
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    spec: FeatureSpec,
    *,
    leaky: bool = False,
) -> ModelResult:
    started = time.perf_counter()
    model.fit(X_train, y_train)
    elapsed = time.perf_counter() - started

    probabilities = model.predict_proba(X_test)[:, 1]

    return ModelResult(
        name=name,
        metrics=evaluate(y_test, probabilities),
        calibration=calibration_points(y_test, probabilities),
        feature_importance=extract_importance(model, spec),
        train_seconds=elapsed,
        leaky=leaky,
    )


def train_and_compare(
    orders: list,
    *,
    origin: str = "unknown",
    include_leakage_demo: bool = True,
    train_frac: float = 0.70,
    valid_frac: float = 0.15,
) -> tuple[ComparisonReport, dict[str, Any]]:
    """Train all three candidates on a temporal split and score them on test.

    The validation slice is kept separate from test even though no hyperparameter
    search runs here — so that adding one later cannot silently start tuning
    against the reported numbers.
    """
    frame = orders_to_frame(orders)
    if frame.empty:
        raise ValueError("no usable orders (need both a label and an order date)")

    split = temporal_split(frame, train_frac=train_frac, valid_frac=valid_frac)
    if not split_is_chronological(split):
        raise RuntimeError("temporal split is not chronological — refusing to train")

    X_train, y_train = build_matrix(split.train, DEFAULT_SPEC)
    X_test, y_test = build_matrix(split.test, DEFAULT_SPEC)

    # Boosted trees take the positive/negative ratio explicitly; the linear
    # model uses class_weight="balanced" for the same purpose.
    positives = int(y_train.sum())
    negatives = int(len(y_train) - positives)
    scale_pos_weight = (negatives / positives) if positives else 1.0

    report = ComparisonReport(
        split_sizes=split.sizes,
        split_boundaries=split.boundaries,
        base_rates=split.base_rates(),
        n_features=len(DEFAULT_SPEC.all),
        origin=origin,
    )

    fitted: dict[str, Any] = {}

    for name, builder in MODEL_BUILDERS.items():
        model = (
            builder(DEFAULT_SPEC)
            if name == "logistic_regression"
            else builder(DEFAULT_SPEC, scale_pos_weight=scale_pos_weight)
        )
        result = _fit_and_score(
            name, model, X_train, y_train, X_test, y_test, DEFAULT_SPEC
        )
        report.results.append(result)
        fitted[name] = model
        log.info(
            "model trained",
            extra={
                "model": name,
                "pr_auc": round(result.metrics["pr_auc"], 4),
                "roc_auc": round(result.metrics["roc_auc"], 4),
                "seconds": round(result.train_seconds, 2),
            },
        )

    if include_leakage_demo:
        report.leakage_demo = _run_leakage_demo(split)

    return report, fitted


def _run_leakage_demo(split: Split) -> ModelResult:
    """Deliberately train on the leaky columns, to quantify the trap.

    This is not a model anyone would ship. It exists so the gap between the
    honest score and the flattering one is a measured number in the report
    rather than a claim in a README — and so that a reader who has seen 0.99
    AUC published on this dataset can see exactly where it comes from.
    """
    X_train, y_train = build_matrix(split.train, LEAKY_SPEC, allow_leakage=True)
    X_test, y_test = build_matrix(split.test, LEAKY_SPEC, allow_leakage=True)

    result = _fit_and_score(
        "logistic_regression__LEAKY",
        build_logistic(LEAKY_SPEC),
        X_train, y_train, X_test, y_test, LEAKY_SPEC,
        leaky=True,
    )
    log.warning(
        "leakage demonstration — not a shippable model",
        extra={"roc_auc": round(result.metrics["roc_auc"], 4)},
    )
    return result


def predict_corridor_risk(
    model: Any,
    orders: list,
    *,
    by: tuple[str, ...] = ("order_country", "category_name"),
) -> dict[tuple[str, ...], float]:
    """Mean predicted late-risk per corridor, for the graph to consume.

    This is the Phase C hand-off: the graph's `ships_from` edges currently carry
    an *observed* historical late rate, which says nothing about corridors with
    few shipments. A model-predicted rate generalises across sparse corridors
    because it borrows strength from shipping mode, region and category.
    """
    frame = orders_to_frame(orders)
    if frame.empty:
        return {}

    X, _ = build_matrix(frame, DEFAULT_SPEC)
    frame = frame.assign(predicted_risk=model.predict_proba(X)[:, 1])

    grouped = frame.groupby(list(by), observed=True)["predicted_risk"].mean()
    return {
        (key if isinstance(key, tuple) else (key,)): float(value)
        for key, value in grouped.items()
    }


def summarise(report: ComparisonReport) -> pd.DataFrame:
    """Comparison table, ordered by the headline metric."""
    rows = [
        {
            "model": r.name,
            "pr_auc": r.metrics["pr_auc"],
            "lift_over_baseline": r.metrics["pr_auc_lift"],
            "roc_auc": r.metrics["roc_auc"],
            "brier": r.metrics["brier"],
            "f1": r.metrics["f1"],
            "accuracy": r.metrics["accuracy"],
            "vs_majority": r.metrics["accuracy"] - r.metrics["majority_accuracy"],
            "train_s": r.train_seconds,
        }
        for r in report.results
    ]
    frame = pd.DataFrame(rows)
    return frame.sort_values("pr_auc", ascending=False).reset_index(drop=True)


def spread(report: ComparisonReport) -> float:
    """Difference between best and worst honest PR-AUC.

    The spec asks whether the added complexity earns its keep. If three very
    different learners land within noise of each other, that is the finding.
    """
    scores = [r.metrics["pr_auc"] for r in report.results if not r.leaky]
    return float(np.max(scores) - np.min(scores)) if scores else 0.0
