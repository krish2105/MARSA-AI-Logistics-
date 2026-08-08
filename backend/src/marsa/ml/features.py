"""Feature engineering for late-delivery risk — and the leakage guard.

The leakage problem, stated plainly
------------------------------------
DataCo's `Late_delivery_risk` label is *defined* as::

    late_delivery_risk = 1 if days_for_shipping_real > days_for_shipment_scheduled

Verified on this corpus: that expression reproduces the label in 5,000 of 5,000
rows. `Delivery Status` is likewise a 1:1 re-encoding ("Late delivery" ⇔ 1).

So a model handed `days_for_shipping_real` or `delivery_status` is not
predicting anything — it is reading the answer and restating it. It will report
~1.00 AUC, look spectacular, and be worthless: at the moment a shipment is
booked, nobody knows how many days it will actually take. That is the only
moment a prediction has any value.

This is the single most common error in published work on this dataset, which
is precisely why the blocklist below is enforced in code, asserted by a test,
and *demonstrated* — `marsa-ml train --with-leakage` deliberately reproduces the
inflated score so the gap between the honest number and the flattering one is
visible rather than argued about.

What survives is the set of things genuinely known when the order is placed:
the service level promised, where it is going, what it is, and how big it is.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from marsa.ingestion.schemas import DataCoOrder

TARGET = "late_delivery_risk"

#: Columns that encode the target. Never features.
LEAKY_COLUMNS: dict[str, str] = {
    "days_for_shipping_real": (
        "The label is literally `real > scheduled`. Including this reproduces "
        "the target exactly and is unknown at order time."
    ),
    "delivery_status": (
        "A 1:1 re-encoding of the label ('Late delivery' ⇔ 1). Same defect, "
        "different column name."
    ),
    "shipping_date": (
        "Equals order_date + days_for_shipping_real, so the actual transit time "
        "is recoverable by subtraction."
    ),
    "shipping_slack_days": (
        "Defined as scheduled − real, i.e. the signed version of the label."
    ),
    TARGET: "The target itself.",
}

#: Known when the order is booked, which is the only moment a prediction helps.
CATEGORICAL_FEATURES = [
    "shipping_mode",
    "market",
    "order_region",
    "order_country",
    "category_name",
    "department_name",
    "customer_segment",
    "customer_country",
]

NUMERIC_FEATURES = [
    # The *promised* window, not the achieved one. A tight promise is a real
    # risk factor and is fixed at booking, so it is legitimately available.
    "days_for_shipment_scheduled",
    "order_item_quantity",
    "sales",
    "order_month",
    "order_dayofweek",
    "order_quarter",
]

FEATURE_COLUMNS = CATEGORICAL_FEATURES + NUMERIC_FEATURES


class LeakageError(RuntimeError):
    """Raised when a leaky column reaches the feature matrix."""


@dataclass(frozen=True)
class FeatureSpec:
    categorical: list[str]
    numeric: list[str]

    @property
    def all(self) -> list[str]:
        return [*self.categorical, *self.numeric]


DEFAULT_SPEC = FeatureSpec(categorical=CATEGORICAL_FEATURES, numeric=NUMERIC_FEATURES)

#: Used only by the leakage demonstration. Never by a reported model.
LEAKY_SPEC = FeatureSpec(
    categorical=[*CATEGORICAL_FEATURES, "delivery_status"],
    numeric=[*NUMERIC_FEATURES, "days_for_shipping_real"],
)


def orders_to_frame(orders: Iterable[DataCoOrder]) -> pd.DataFrame:
    """Flatten orders into a DataFrame, deriving order-time calendar features."""
    records: list[dict[str, Any]] = []
    for order in orders:
        if order.late_delivery_risk is None or order.order_date is None:
            # No label or no timestamp: cannot train on it and cannot place it
            # in a temporal split.
            continue
        records.append(
            {
                "order_id": order.order_id,
                "order_date": order.order_date,
                "shipping_mode": order.shipping_mode,
                "market": order.market,
                "order_region": order.order_region,
                "order_country": order.order_country,
                "category_name": order.category_name,
                "department_name": order.department_name,
                "customer_segment": order.customer_segment,
                "customer_country": order.customer_country,
                "days_for_shipment_scheduled": order.days_for_shipment_scheduled,
                "order_item_quantity": order.order_item_quantity,
                "sales": order.sales,
                "order_month": order.order_date.month,
                "order_dayofweek": order.order_date.weekday(),
                "order_quarter": (order.order_date.month - 1) // 3 + 1,
                # Carried through for the leakage demonstration only.
                "days_for_shipping_real": order.days_for_shipping_real,
                "delivery_status": order.delivery_status,
                TARGET: int(order.late_delivery_risk),
            }
        )

    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return frame
    return frame.sort_values("order_date").reset_index(drop=True)


def assert_no_leakage(columns: Iterable[str], *, allow: set[str] | None = None) -> None:
    """Fail loudly if a leaky column reached the feature set.

    Called on every training run. A silent leak produces a model that looks
    excellent and is useless, which is far more expensive than a crash.
    """
    allow = allow or set()
    offenders = [c for c in columns if c in LEAKY_COLUMNS and c not in allow]
    if offenders:
        detail = "; ".join(f"{c}: {LEAKY_COLUMNS[c]}" for c in offenders)
        raise LeakageError(
            f"Leaky column(s) in the feature matrix: {offenders}. {detail}"
        )


def fit_category_dtypes(
    frame: pd.DataFrame, spec: FeatureSpec = DEFAULT_SPEC
) -> dict[str, pd.CategoricalDtype]:
    """Derive one categorical dtype per column, over the WHOLE dataset.

    This must be computed before the temporal split and shared by every slice.
    Calling `.astype("category")` on each slice separately looks equivalent and
    is not: pandas builds the category list from the values that slice happens
    to contain, and assigns integer codes by sorted position. Two slices with
    different value sets therefore produce different code mappings for the same
    string.

    A category that trades only in the early period is enough to shift every
    later code by one, so a model trained with `France == 1` is asked to
    predict with `France == 0` — which meant a different country during
    training. The model does not error; it returns confident nonsense. XGBoost
    3.x catches the subset of this it can see (a level present at predict time
    and absent at fit time) and raises; the reverse direction is silent.

    Sharing one dtype makes the codes stable and lets a category legitimately
    have zero rows in a slice, which under a chronological split is normal
    rather than exceptional.
    """
    dtypes: dict[str, pd.CategoricalDtype] = {}
    for column in spec.categorical:
        if column not in frame.columns:
            continue
        # sorted + unique so the mapping is deterministic across runs, and
        # dropna because NaN is represented as code -1, not as a level.
        levels = pd.Series(frame[column].dropna().unique()).sort_values()
        dtypes[column] = pd.CategoricalDtype(categories=levels, ordered=False)
    return dtypes


def build_matrix(
    frame: pd.DataFrame,
    spec: FeatureSpec = DEFAULT_SPEC,
    *,
    allow_leakage: bool = False,
    dtypes: dict[str, pd.CategoricalDtype] | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Return (X, y) for a given feature spec.

    Pass `dtypes` from `fit_category_dtypes` over the full dataset whenever
    more than one frame is built — see that function for why. Omitting it is
    only correct when a single frame is the entire universe.
    """
    if frame.empty:
        raise ValueError("no rows to build features from")

    allow = set(LEAKY_COLUMNS) - {TARGET} if allow_leakage else None
    assert_no_leakage(spec.all, allow=allow)

    missing = [c for c in spec.all if c not in frame.columns]
    if missing:
        raise ValueError(f"frame is missing feature columns: {missing}")

    X = frame[spec.all].copy()

    # Tree learners take categoricals natively; the linear model one-hots them
    # in its own pipeline. Declaring the dtype once keeps both honest about
    # which columns are categorical.
    for column in spec.categorical:
        X[column] = X[column].astype(
            dtypes[column] if dtypes and column in dtypes else "category"
        )
    for column in spec.numeric:
        X[column] = pd.to_numeric(X[column], errors="coerce")

    return X, frame[TARGET].astype(int)
