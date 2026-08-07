"""DataCo Smart Supply Chain — shipment records for the agentic path and ML layer.

Access note
-----------
This dataset lives on Kaggle and **cannot be fetched anonymously**. Kaggle
requires an authenticated account for every download, so unlike CROSS and
Comtrade there is no unattended path to it. That is a property of the source,
not a limitation of this code.

Two supported routes:

  1. `KAGGLE_USERNAME` + `KAGGLE_KEY` in `.env` (or `~/.kaggle/kaggle.json`),
     then `marsa-ingest dataco --download`.
  2. Manual download of `DataCoSupplyChainDataset.csv` into `data/raw/dataco/`,
     then `marsa-ingest dataco`.

The CSV is Latin-1 encoded, not UTF-8 — a detail that bites everyone who
assumes otherwise and gets a UnicodeDecodeError on the first customer name with
an accent.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from marsa.ingestion.schemas import DataCoOrder, Origin, Provenance, SourceKind
from marsa.logging import get_logger

log = get_logger(__name__)

KAGGLE_DATASET = "shashwatwork/dataco-smart-supply-chain-for-big-data-analysis"
CSV_NAME = "DataCoSupplyChainDataset.csv"

# The published CSV is Latin-1. Read it as UTF-8 and it fails on real rows.
ENCODING = "latin-1"

# Upstream column -> our field. Kept explicit rather than auto-snake-cased so a
# renamed upstream column fails visibly instead of quietly becoming a null.
COLUMN_MAP: dict[str, str] = {
    "Order Id": "order_id",
    "Order Item Id": "order_item_id",
    "order date (DateOrders)": "order_date",
    "shipping date (DateOrders)": "shipping_date",
    "Shipping Mode": "shipping_mode",
    "Days for shipping (real)": "days_for_shipping_real",
    "Days for shipment (scheduled)": "days_for_shipment_scheduled",
    "Late_delivery_risk": "late_delivery_risk",
    "Delivery Status": "delivery_status",
    "Customer Id": "customer_id",
    "Customer Country": "customer_country",
    "Customer City": "customer_city",
    "Customer Segment": "customer_segment",
    "Product Card Id": "product_card_id",
    "Product Name": "product_name",
    "Category Name": "category_name",
    "Department Name": "department_name",
    "Order Country": "order_country",
    "Order City": "order_city",
    "Order Region": "order_region",
    "Market": "market",
    "Order Item Quantity": "order_item_quantity",
    "Sales": "sales",
    "Order Profit Per Order": "order_profit_per_order",
}

REQUIRED_COLUMNS = ("Order Id", "Late_delivery_risk", "Days for shipping (real)")


class DataCoLoadError(RuntimeError):
    pass


def download_via_kaggle(dest_dir: Path) -> Path:
    """Download and unzip the dataset using Kaggle credentials.

    Imported lazily: `kaggle` authenticates at import time and raises if no
    credentials exist, which would break every other ingestor's import.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
    except ImportError as exc:
        raise DataCoLoadError(
            "The `kaggle` package is not installed. Run `pip install kaggle`, "
            "or download the CSV manually into data/raw/dataco/."
        ) from exc

    try:
        api = KaggleApi()
        api.authenticate()
        api.dataset_download_files(KAGGLE_DATASET, path=str(dest_dir), unzip=True)
    except Exception as exc:  # noqa: BLE001 — surface the real cause to the user
        raise DataCoLoadError(
            f"Kaggle download failed: {exc}. Set KAGGLE_USERNAME and KAGGLE_KEY "
            "in .env, or download the CSV manually into data/raw/dataco/."
        ) from exc

    csv_path = find_csv(dest_dir)
    if csv_path is None:
        raise DataCoLoadError(f"Download finished but {CSV_NAME} was not found in {dest_dir}")
    return csv_path


def find_csv(search_dir: Path) -> Path | None:
    """Locate the dataset CSV, tolerating Kaggle's occasional renames."""
    if not search_dir.exists():
        return None
    exact = search_dir / CSV_NAME
    if exact.exists():
        return exact
    candidates = sorted(search_dir.rglob("*.csv"), key=lambda p: p.stat().st_size, reverse=True)
    return candidates[0] if candidates else None


def load_dataframe(csv_path: Path, *, nrows: int | None = None) -> pd.DataFrame:
    frame = pd.read_csv(csv_path, encoding=ENCODING, nrows=nrows, low_memory=False)

    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise DataCoLoadError(
            f"{csv_path.name} is missing required columns {missing}. "
            f"Columns present: {list(frame.columns)[:15]}…"
        )
    return frame


def iter_orders(
    csv_path: Path,
    *,
    nrows: int | None = None,
) -> Iterator[DataCoOrder]:
    """Stream the CSV as validated `DataCoOrder` records."""
    frame = load_dataframe(csv_path, nrows=nrows)
    present = {src: dst for src, dst in COLUMN_MAP.items() if src in frame.columns}

    dropped = set(COLUMN_MAP) - set(present)
    if dropped:
        log.warning("dataco columns absent from CSV", extra={"columns": sorted(dropped)})

    frame = frame[list(present)].rename(columns=present)

    for date_col in ("order_date", "shipping_date"):
        if date_col in frame.columns:
            frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")

    provenance = Provenance(
        source=SourceKind.DATACO,
        origin=Origin.LIVE,
        url=f"https://www.kaggle.com/datasets/{KAGGLE_DATASET}",
        retrieved_at=datetime.now(UTC),
        note=f"loaded from {csv_path.name}",
    )

    for record in frame.to_dict(orient="records"):
        cleaned: dict[str, Any] = {
            k: (None if pd.isna(v) else v) for k, v in record.items()
        }
        try:
            yield DataCoOrder(**cleaned, provenance=provenance)
        except Exception as exc:  # noqa: BLE001 — skip the row, keep the run
            log.warning(
                "skipping unparseable DataCo row",
                extra={"order_id": cleaned.get("order_id"), "error": str(exc)},
            )
