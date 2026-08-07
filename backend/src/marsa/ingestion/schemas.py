"""Normalised corpus schemas.

Everything Phase A writes conforms to one of these models. Later phases read
*only* these — never a raw upstream payload — so a change in an upstream
response shape breaks loudly here at validation time rather than silently
corrupting an index three phases downstream.

`Provenance` is attached to every record. The spec commits to reporting exactly
what was pulled and over what window, and that promise is only keepable if the
provenance is in the data rather than in a README someone forgets to update.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, Field, field_validator


class SourceKind(StrEnum):
    CROSS = "cbp_cross_rulings"
    COMTRADE = "un_comtrade"
    DATACO = "dataco_supply_chain"
    WORLDBANK = "worldbank_lpi_cppi"


class Origin(StrEnum):
    """How a record came to exist. Never inferred — always set explicitly."""

    LIVE = "live"  # fetched from the real upstream source
    SYNTHETIC = "synthetic"  # generated locally; NOT real data


class Provenance(BaseModel):
    source: SourceKind
    origin: Origin
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    url: str | None = None
    note: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Fast path — CBP CROSS rulings
# ─────────────────────────────────────────────────────────────────────────────


class CrossRuling(BaseModel):
    """One binding CBP classification ruling.

    The fast path chunks these parent-child: the full ruling is the parent, the
    holding/reasoning paragraph is the child chunk that gets embedded.
    """

    ruling_number: str = Field(description="e.g. NY N302241, HQ H289765")
    collection: str = Field(description="HQ or NY")
    ruling_date: date | None = None
    subject: str
    body: str = Field(description="Full ruling text")
    hts_codes: list[str] = Field(default_factory=list)
    related_rulings: list[str] = Field(
        default_factory=list, description="Rulings cited by this one"
    )
    category: str | None = None
    url: str | None = None
    provenance: Provenance

    @field_validator("hts_codes", "related_rulings", mode="before")
    @classmethod
    def _dedupe(cls, v: Any) -> Any:
        if isinstance(v, list):
            return list(dict.fromkeys(x for x in v if x))
        return v

    @property
    def doc_id(self) -> str:
        return f"cross::{self.ruling_number.replace(' ', '')}"


# ─────────────────────────────────────────────────────────────────────────────
# Agentic path — UN Comtrade bilateral flows
# ─────────────────────────────────────────────────────────────────────────────


class ComtradeFlow(BaseModel):
    """One reporter/partner/commodity/period trade observation.

    Field names mirror the UN Comtrade v1 response so the mapping stays
    inspectable; only the value types are normalised.
    """

    period: str
    ref_year: int
    reporter_code: int
    reporter_iso: str | None = None
    reporter_desc: str | None = None
    partner_code: int
    partner_iso: str | None = None
    partner_desc: str | None = None
    flow_code: str = Field(description="M=import, X=export, RM/RX=re-import/export")
    flow_desc: str | None = None
    cmd_code: str = Field(description="HS commodity code")
    cmd_desc: str | None = None
    primary_value: float | None = Field(default=None, description="Trade value, USD")
    net_weight_kg: float | None = None
    qty: float | None = None
    qty_unit: str | None = None
    provenance: Provenance

    @property
    def doc_id(self) -> str:
        return (
            f"comtrade::{self.period}::{self.reporter_code}"
            f"::{self.partner_code}::{self.cmd_code}::{self.flow_code}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Agentic path + ML — DataCo shipment records
# ─────────────────────────────────────────────────────────────────────────────


class DataCoOrder(BaseModel):
    """One order line from the DataCo Smart Supply Chain dataset.

    `late_delivery_risk` is the pre-labelled target the Phase D classifier
    trains on. It reflects one company's historical operations — the model
    demonstrates the technique, not a generalisable prediction.
    """

    order_id: int
    order_item_id: int | None = None
    order_date: datetime | None = None
    shipping_date: datetime | None = None
    shipping_mode: str | None = None
    days_for_shipping_real: float | None = None
    days_for_shipment_scheduled: float | None = None
    late_delivery_risk: int | None = Field(default=None, ge=0, le=1)
    delivery_status: str | None = None

    customer_id: int | None = None
    customer_country: str | None = None
    customer_city: str | None = None
    customer_segment: str | None = None

    product_card_id: int | None = None
    product_name: str | None = None
    category_name: str | None = None
    department_name: str | None = None

    order_country: str | None = None
    order_city: str | None = None
    order_region: str | None = None
    market: str | None = None

    order_item_quantity: int | None = None
    sales: float | None = None
    order_profit_per_order: float | None = None

    provenance: Provenance

    @property
    def doc_id(self) -> str:
        return f"dataco::{self.order_id}::{self.order_item_id or 0}"

    @property
    def shipping_slack_days(self) -> float | None:
        """Scheduled minus actual. Negative means the shipment ran late."""
        if self.days_for_shipment_scheduled is None or self.days_for_shipping_real is None:
            return None
        return self.days_for_shipment_scheduled - self.days_for_shipping_real


# ─────────────────────────────────────────────────────────────────────────────
# Graph path — World Bank logistics performance
# ─────────────────────────────────────────────────────────────────────────────


class CountryLogistics(BaseModel):
    """World Bank Logistics Performance Index 2.0 record for one country.

    LPI 2.0 (2025 redesign) is derived from shipment and vessel tracking rather
    than perception surveys, which is why dwell times appear here as real
    measured values.
    """

    country_iso3: str
    country_name: str
    year: int
    lpi_score: float | None = None
    lpi_rank: int | None = None
    customs_score: float | None = None
    infrastructure_score: float | None = None
    international_shipments_score: float | None = None
    logistics_competence_score: float | None = None
    tracking_tracing_score: float | None = None
    timeliness_score: float | None = None
    import_dwell_days: float | None = None
    export_dwell_days: float | None = None
    provenance: Provenance

    @property
    def doc_id(self) -> str:
        return f"lpi::{self.country_iso3}::{self.year}"


class PortPerformance(BaseModel):
    """Container Port Performance Index entry for one port."""

    port_name: str
    unlocode: str | None = Field(default=None, description="e.g. AEJEA for Jebel Ali")
    country_iso3: str
    year: int
    cppi_rank: int | None = None
    cppi_score: float | None = None
    annual_teu: float | None = None
    avg_vessel_hours: float | None = None
    provenance: Provenance

    @property
    def doc_id(self) -> str:
        return f"port::{self.unlocode or self.port_name}::{self.year}"


# ─────────────────────────────────────────────────────────────────────────────
# Provenance manifest
# ─────────────────────────────────────────────────────────────────────────────


class SourceManifest(BaseModel):
    """What was pulled, from where, when, and how much of it.

    Written next to every corpus. This is the artefact the spec's "document the
    actual query volume you pulled and over what time window" commitment is
    settled with.
    """

    source: SourceKind
    origin: Origin
    record_count: int
    output_path: str
    started_at: datetime
    completed_at: datetime
    request_count: int = 0
    cache_hits: int = 0
    parameters: dict[str, Any] = Field(default_factory=dict)
    content_sha256: str | None = None
    subset_rationale: str | None = Field(
        default=None,
        description="Why this subset and not the full corpus — required for honest reporting",
    )
    warnings: list[str] = Field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.completed_at - self.started_at).total_seconds()


# ─────────────────────────────────────────────────────────────────────────────
# JSONL helpers
# ─────────────────────────────────────────────────────────────────────────────

M = TypeVar("M", bound=BaseModel)


def write_jsonl(records: Iterable[BaseModel], path: Path) -> tuple[int, str]:
    """Write records as JSONL. Returns (count, sha256 of the file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            line = record.model_dump_json() + "\n"
            fh.write(line)
            digest.update(line.encode("utf-8"))
            count += 1
    return count, digest.hexdigest()


def read_jsonl(path: Path, model: type[M]) -> Iterator[M]:
    """Stream a JSONL corpus back as validated models."""
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield model.model_validate(json.loads(line))
