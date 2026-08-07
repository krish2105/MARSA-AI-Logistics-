"""Train/validation/test splitting.

The split is **temporal**, not random.

A random split lets the model see orders from June while being tested on orders
from March. Shipping performance drifts — carriers change, lanes congest,
volumes spike seasonally — so a randomly-split model is scored on a world it
has already observed, and its number overstates what it would do in production
by however much that drift matters.

Sorting by order date and cutting at fixed quantiles reproduces the only
question that matters operationally: *given everything up to today, how well
does this predict tomorrow?*
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Split:
    train: pd.DataFrame
    valid: pd.DataFrame
    test: pd.DataFrame

    @property
    def sizes(self) -> dict[str, int]:
        return {"train": len(self.train), "valid": len(self.valid), "test": len(self.test)}

    @property
    def boundaries(self) -> dict[str, str]:
        def span(frame: pd.DataFrame) -> str:
            if frame.empty:
                return "—"
            return (
                f"{frame['order_date'].min().date()} → {frame['order_date'].max().date()}"
            )

        return {"train": span(self.train), "valid": span(self.valid), "test": span(self.test)}

    def base_rates(self, target: str = "late_delivery_risk") -> dict[str, float]:
        return {
            name: round(float(frame[target].mean()), 4) if len(frame) else 0.0
            for name, frame in (
                ("train", self.train), ("valid", self.valid), ("test", self.test)
            )
        }


def temporal_split(
    frame: pd.DataFrame,
    *,
    train_frac: float = 0.70,
    valid_frac: float = 0.15,
    date_column: str = "order_date",
) -> Split:
    """Split chronologically: earliest → train, latest → test."""
    if frame.empty:
        raise ValueError("cannot split an empty frame")
    if not 0 < train_frac < 1 or not 0 <= valid_frac < 1 or train_frac + valid_frac >= 1:
        raise ValueError(f"invalid fractions: train={train_frac}, valid={valid_frac}")

    ordered = frame.sort_values(date_column).reset_index(drop=True)
    n = len(ordered)
    train_end = int(n * train_frac)
    valid_end = train_end + int(n * valid_frac)

    return Split(
        train=ordered.iloc[:train_end].reset_index(drop=True),
        valid=ordered.iloc[train_end:valid_end].reset_index(drop=True),
        test=ordered.iloc[valid_end:].reset_index(drop=True),
    )


def split_is_chronological(split: Split, date_column: str = "order_date") -> bool:
    """Verify no future data leaked backwards across a boundary."""
    if split.train.empty or split.valid.empty or split.test.empty:
        return True
    return bool(
        split.train[date_column].max() <= split.valid[date_column].min()
        and split.valid[date_column].max() <= split.test[date_column].min()
    )
