"""World Bank — LPI 2.0 and the Container Port Performance Index (graph path).

Two different products, both open:

* **Logistics Performance Index 2.0** (2025 redesign). The redesign matters:
  LPI 2.0 is derived from shipment and vessel *tracking* data rather than the
  perception survey the old LPI used, which is why dwell times are real
  measured values here and can be trusted as graph edge weights.
* **Container Port Performance Index** — ranks real ports, including Jebel Ali
  (UNLOCODE AEJEA), the anchor node of the supply graph.

Access
------
LPI indicators are served by the World Bank's indicator API, which is open and
needs no key. CPPI is published as a report annex (Excel/PDF) rather than
through an API, so it is loaded from a local table — there is no honest way to
call it "an API pull".
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marsa.ingestion.fetcher import Fetcher, UpstreamShapeError
from marsa.ingestion.schemas import (
    CountryLogistics,
    Origin,
    PortPerformance,
    Provenance,
    SourceKind,
)
from marsa.logging import get_logger

log = get_logger(__name__)

INDICATOR_API = "https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}"

# World Bank LPI indicator codes.
LPI_INDICATORS: dict[str, str] = {
    "LP.LPI.OVRL.XQ": "lpi_score",
    "LP.LPI.CUST.XQ": "customs_score",
    "LP.LPI.INFR.XQ": "infrastructure_score",
    "LP.LPI.ITRN.XQ": "international_shipments_score",
    "LP.LPI.LOGS.XQ": "logistics_competence_score",
    "LP.LPI.TRAC.XQ": "tracking_tracing_score",
    "LP.LPI.TIME.XQ": "timeliness_score",
}

# Trade partners of the Dubai re-export corridor the graph path models.
DEFAULT_COUNTRIES: tuple[str, ...] = (
    "ARE", "CHN", "IND", "USA", "DEU", "JPN", "KOR",
    "SGP", "GBR", "SAU", "OMN", "QAT", "KWT", "BHR",
    "NLD", "BEL", "TUR", "EGY", "ZAF", "BRA",
)


class WorldBankClient:
    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_indicator(
        self,
        indicator: str,
        *,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
        date_range: str = "2018:2025",
        per_page: int = 2000,
    ) -> list[dict[str, Any]]:
        """One indicator across many countries.

        The World Bank API returns `[metadata, rows]` — a two-element array, not
        an object. Treating it as a dict is the classic mistake here.
        """
        url = INDICATOR_API.format(countries=";".join(countries), indicator=indicator)
        payload = self.fetcher.get_json(
            url, params={"format": "json", "date": date_range, "per_page": per_page}
        )

        if not isinstance(payload, list) or len(payload) < 2:
            detail = payload[0] if isinstance(payload, list) and payload else payload
            raise UpstreamShapeError(
                f"World Bank indicator {indicator} returned an unexpected shape: {detail!r}"
            )
        rows = payload[1]
        return list(rows) if isinstance(rows, list) else []

    def harvest_lpi(
        self,
        *,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
        date_range: str = "2018:2025",
    ) -> Iterator[CountryLogistics]:
        """Fetch every LPI indicator and pivot to one record per country-year."""
        merged: dict[tuple[str, int], dict[str, Any]] = {}

        for indicator, field_name in LPI_INDICATORS.items():
            try:
                rows = self.fetch_indicator(indicator, countries=countries, date_range=date_range)
            except UpstreamShapeError as exc:
                log.error("lpi indicator failed", extra={"indicator": indicator, "error": str(exc)})
                continue

            for row in rows:
                value = row.get("value")
                if value is None:
                    continue
                country = row.get("countryiso3code") or (row.get("country") or {}).get("id")
                if not country:
                    continue
                try:
                    year = int(row.get("date"))
                except (TypeError, ValueError):
                    continue

                entry = merged.setdefault(
                    (country, year),
                    {
                        "country_iso3": country,
                        "country_name": (row.get("country") or {}).get("value", country),
                        "year": year,
                    },
                )
                entry[field_name] = float(value)

        provenance = Provenance(
            source=SourceKind.WORLDBANK,
            origin=Origin.LIVE,
            url="https://api.worldbank.org/v2/",
            retrieved_at=datetime.now(UTC),
            note="Logistics Performance Index via World Bank indicator API",
        )

        for entry in merged.values():
            yield CountryLogistics(**entry, provenance=provenance)


def load_cppi_csv(path: Path, *, year: int = 2024) -> Iterator[PortPerformance]:
    """Load a Container Port Performance Index table from a local CSV.

    CPPI ships as a report annex, not an API. Expected headers (case-insensitive):
      port, unlocode, country_iso3, rank, score, teu, avg_vessel_hours
    """
    if not path.exists():
        raise FileNotFoundError(
            f"CPPI table not found at {path}. Download the Container Port "
            "Performance Index annex from openknowledge.worldbank.org and save "
            "it there as CSV."
        )

    provenance = Provenance(
        source=SourceKind.WORLDBANK,
        origin=Origin.LIVE,
        url="https://openknowledge.worldbank.org/",
        retrieved_at=datetime.now(UTC),
        note=f"Container Port Performance Index, loaded from {path.name}",
    )

    with path.open(encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            name = row.get("port") or row.get("port_name")
            if not name:
                continue
            yield PortPerformance(
                port_name=name,
                unlocode=row.get("unlocode") or None,
                country_iso3=row.get("country_iso3") or row.get("country") or "",
                year=int(row.get("year") or year),
                cppi_rank=_as_int(row.get("rank")),
                cppi_score=_as_float(row.get("score")),
                annual_teu=_as_float(row.get("teu")),
                avg_vessel_hours=_as_float(row.get("avg_vessel_hours")),
                provenance=provenance,
            )


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
