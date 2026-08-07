"""Model persistence and the model card."""

from __future__ import annotations

import json
import pickle
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marsa.logging import get_logger
from marsa.ml.features import DEFAULT_SPEC, LEAKY_COLUMNS
from marsa.ml.train import ComparisonReport

log = get_logger(__name__)

MODEL_FILE = "late_delivery_model.pkl"
CARD_FILE = "model_card.json"
CONGESTION_FILE = "port_congestion.json"


def ml_dir(data_dir: Path) -> Path:
    return data_dir / "models"


def save_model(model: Any, name: str, *, data_dir: Path) -> Path:
    target = ml_dir(data_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = target / MODEL_FILE
    with path.open("wb") as fh:
        pickle.dump({"name": name, "model": model, "features": DEFAULT_SPEC.all}, fh)
    log.info("model saved", extra={"model": name, "path": str(path)})
    return path


def load_model(data_dir: Path) -> tuple[str, Any]:
    path = ml_dir(data_dir) / MODEL_FILE
    if not path.exists():
        raise FileNotFoundError(f"No model at {path}. Run `marsa-ml train` first.")
    with path.open("rb") as fh:
        payload = pickle.load(fh)  # noqa: S301 — our own artefact
    return payload["name"], payload["model"]


def write_model_card(
    report: ComparisonReport, *, data_dir: Path, congestion: list | None = None
) -> Path:
    """A model card, not just metrics.

    The intended-use and limitations sections are the point: this model is
    trained on one company's historical operations, so it demonstrates the
    technique rather than predicting delivery risk for anyone else. Recording
    that beside the numbers keeps the two from drifting apart.
    """
    target = ml_dir(data_dir)
    target.mkdir(parents=True, exist_ok=True)

    card: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "task": "Binary classification — will this shipment be delivered late?",
        "target": "late_delivery_risk",
        "results": report.as_dict(),
        "features": {
            "used": DEFAULT_SPEC.all,
            "count": len(DEFAULT_SPEC.all),
            "rationale": (
                "Only fields known at the moment the order is booked. A "
                "prediction made after the shipment has arrived has no value."
            ),
        },
        "excluded_for_leakage": LEAKY_COLUMNS,
        "validation": {
            "split": "temporal (chronological), 70/15/15",
            "why": (
                "A random split scores the model on a period it has already "
                "seen. Shipping performance drifts, so the random figure "
                "overstates production behaviour."
            ),
        },
        "intended_use": (
            "Ranking corridors and shipments by relative risk inside this "
            "project's graph path. Not for operational decisions about "
            "individual consignments."
        ),
        "limitations": [
            "DataCo's Late_delivery_risk label reflects one company's historical "
            "operations. The model demonstrates the technique; it is not a "
            "generalisable prediction of delivery risk for other shippers.",
            "No causal claim is made. Shipping mode correlates with lateness "
            "partly because expedited modes are chosen for already-urgent "
            "shipments — the model cannot separate the two.",
            "Probabilities are consumed downstream as quantities, so calibration "
            "(Brier) matters as much as ranking (PR-AUC).",
        ],
    }

    if report.origin == "synthetic":
        card["limitations"].insert(
            0,
            "TRAINED ON SYNTHETIC DATA. Every metric here describes the fixture "
            "generator, not real shipping. No figure from this run is publishable.",
        )

    if congestion is not None:
        card["port_congestion"] = {
            "method": "rule-based composite (vessel hours 0.50, dwell 0.30, CPPI rank 0.20)",
            "why_not_learned": (
                "There is no congestion label to train against, and CPPI rank is "
                "an output of the same measurements — regressing on it would be "
                "circular."
            ),
            "ports": [p.as_dict() if hasattr(p, "as_dict") else p for p in congestion],
        }

    path = target / CARD_FILE
    path.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    return path


def load_model_card(data_dir: Path) -> dict[str, Any] | None:
    path = ml_dir(data_dir) / CARD_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_congestion(scored: list, *, data_dir: Path) -> Path:
    target = ml_dir(data_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = target / CONGESTION_FILE
    path.write_text(
        json.dumps([p.as_dict() for p in scored], indent=2) + "\n", encoding="utf-8"
    )
    return path


def load_congestion(data_dir: Path) -> list[dict[str, Any]]:
    path = ml_dir(data_dir) / CONGESTION_FILE
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
