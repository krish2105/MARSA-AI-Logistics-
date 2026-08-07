"""The three candidate models, and how they are scored.

Logistic regression is not a strawman here. On tabular data with a handful of
low-cardinality categoricals and a strong main effect (shipping mode), a
regularised linear model is frequently competitive with gradient boosting — and
it is interpretable, trains in milliseconds, and calibrates well out of the box.
Reporting that honestly, when it happens, is more useful than assuming the
boosted model must win.

Metrics are chosen for what the graph path actually does with the output.

* **PR-AUC** over ROC-AUC as the headline. The classes are imbalanced and the
  positive class is the one anyone cares about; ROC-AUC is flattered by the
  large negative class. The no-skill PR-AUC baseline is the base rate itself,
  so it is always reported alongside — a PR-AUC of 0.70 means nothing until you
  know whether the base rate was 0.10 or 0.65.
* **Brier score and calibration.** Phase C consumes these as *probabilities*
  that get multiplied into edge conductance. A model that ranks perfectly but
  outputs 0.9 when it means 0.6 will silently distort every exposure figure
  downstream. Discrimination alone is not enough when the number is used as a
  quantity rather than a sort key.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    log_loss,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from marsa.ml.features import FeatureSpec

RANDOM_STATE = 42


@dataclass
class ModelResult:
    name: str
    metrics: dict[str, float]
    #: (mean_predicted, observed_fraction) pairs for the calibration curve.
    calibration: list[tuple[float, float]] = field(default_factory=list)
    feature_importance: dict[str, float] = field(default_factory=dict)
    train_seconds: float = 0.0
    leaky: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
            "calibration": [[round(a, 4), round(b, 4)] for a, b in self.calibration],
            "featureImportance": {
                k: round(v, 4) for k, v in sorted(
                    self.feature_importance.items(), key=lambda kv: -abs(kv[1])
                )[:12]
            },
            "trainSeconds": round(self.train_seconds, 3),
            "leaky": self.leaky,
        }


def build_logistic(spec: FeatureSpec) -> Pipeline:
    """One-hot + scale + L2 logistic regression.

    `handle_unknown="ignore"` matters under a temporal split: a category that
    only appears in the test period would otherwise crash at inference, which
    is exactly the situation a chronological split is designed to surface.
    """
    return Pipeline(
        [
            (
                "prep",
                ColumnTransformer(
                    [
                        (
                            "cat",
                            OneHotEncoder(handle_unknown="ignore", min_frequency=5),
                            spec.categorical,
                        ),
                        ("num", StandardScaler(), spec.numeric),
                    ],
                    remainder="drop",
                ),
            ),
            (
                "clf",
                LogisticRegression(
                    max_iter=2000,
                    C=1.0,
                    class_weight="balanced",
                    random_state=RANDOM_STATE,
                ),
            ),
        ]
    )


def build_xgboost(spec: FeatureSpec, *, scale_pos_weight: float = 1.0):
    from xgboost import XGBClassifier

    return XGBClassifier(
        n_estimators=400,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=1.0,
        scale_pos_weight=scale_pos_weight,
        # Native categorical handling — avoids inventing an ordinal ranking
        # over unordered values like country names.
        enable_categorical=True,
        tree_method="hist",
        eval_metric="aucpr",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )


def build_lightgbm(spec: FeatureSpec, *, scale_pos_weight: float = 1.0):
    from lightgbm import LGBMClassifier

    return LGBMClassifier(
        n_estimators=400,
        num_leaves=31,
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=1.0,
        scale_pos_weight=scale_pos_weight,
        random_state=RANDOM_STATE,
        n_jobs=-1,
        verbose=-1,
    )


MODEL_BUILDERS: dict[str, Callable[..., Any]] = {
    "logistic_regression": build_logistic,
    "xgboost": build_xgboost,
    "lightgbm": build_lightgbm,
}


def evaluate(
    y_true: np.ndarray | pd.Series,
    y_prob: np.ndarray,
    *,
    threshold: float = 0.5,
) -> dict[str, float]:
    """Discrimination, calibration and threshold metrics in one place."""
    y_true = np.asarray(y_true).astype(int)
    y_pred = (y_prob >= threshold).astype(int)
    base_rate = float(y_true.mean())

    metrics = {
        "roc_auc": float(roc_auc_score(y_true, y_prob)),
        "pr_auc": float(average_precision_score(y_true, y_prob)),
        # The no-skill PR-AUC. Without it the PR-AUC above is uninterpretable.
        "pr_auc_baseline": base_rate,
        "brier": float(brier_score_loss(y_true, y_prob)),
        "log_loss": float(log_loss(y_true, y_prob, labels=[0, 1])),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "accuracy": float((y_pred == y_true).mean()),
        "base_rate": base_rate,
        # Accuracy of always predicting the majority class — the only honest
        # floor for the accuracy figure above.
        "majority_accuracy": float(max(base_rate, 1 - base_rate)),
    }
    metrics["pr_auc_lift"] = metrics["pr_auc"] - base_rate
    return metrics


def calibration_points(
    y_true: np.ndarray | pd.Series, y_prob: np.ndarray, *, bins: int = 10
) -> list[tuple[float, float]]:
    """Reliability curve as (predicted, observed) pairs."""
    try:
        observed, predicted = calibration_curve(
            np.asarray(y_true).astype(int), y_prob, n_bins=bins, strategy="quantile"
        )
    except ValueError:
        # Too few distinct probabilities to bin — degenerate but not fatal.
        return []
    return [(float(p), float(o)) for p, o in zip(predicted, observed, strict=True)]


def extract_importance(model: Any, spec: FeatureSpec) -> dict[str, float]:
    """Best-effort feature importance, whatever the model exposes."""
    # Tree models: native importances aligned to the input columns.
    if hasattr(model, "feature_importances_"):
        values = np.asarray(model.feature_importances_, dtype=float)
        names = spec.all
        if len(values) == len(names):
            total = values.sum() or 1.0
            return {n: float(v / total) for n, v in zip(names, values, strict=True)}

    # Linear pipeline: coefficients against the expanded one-hot names.
    if isinstance(model, Pipeline) and hasattr(model.named_steps.get("clf"), "coef_"):
        try:
            names = model.named_steps["prep"].get_feature_names_out()
            coefs = model.named_steps["clf"].coef_[0]
            return {str(n): float(c) for n, c in zip(names, coefs, strict=True)}
        except Exception:  # noqa: BLE001 — importance is diagnostic, never fatal
            return {}

    return {}
