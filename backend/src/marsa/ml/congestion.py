"""Port congestion scoring from LPI 2.0 and the Container Port Performance Index.

Deliberately rule-based, not learned. There is no congestion *label* to train
against — CPPI rank is an output of the same measurements, so regressing on it
would be circular. What exists is a small number of measured indicators with a
known direction, and combining those transparently is both more honest and more
auditable than fitting an unsupervised score nobody can interrogate.

Three signals, each normalised to [0, 1] where 1 is worse:

* **Average vessel hours in port** (CPPI) — the most direct congestion measure
  there is: how long a ship actually waits.
* **Import dwell days** (LPI 2.0) — customs and inland clearance friction.
  LPI 2.0's 2025 redesign derives this from tracking data rather than a
  perception survey, which is what makes it usable as a quantity.
* **CPPI rank** — the World Bank's own composite, as a cross-check on the two
  raw measures.

Weights favour vessel hours because it measures the port itself; dwell days are
a country-level attribute and so partly reflect the customs regime rather than
the berth.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from marsa.ingestion.schemas import CountryLogistics, PortPerformance

WEIGHT_VESSEL_HOURS = 0.50
WEIGHT_DWELL_DAYS = 0.30
WEIGHT_CPPI_RANK = 0.20

#: Anchors for normalisation, from the observed range of real port data.
#: Fixed rather than computed from the batch so a score means the same thing
#: across runs — a min-max over whatever ports happen to be loaded would make
#: yesterday's "high" today's "medium" with no change in the world.
VESSEL_HOURS_FLOOR, VESSEL_HOURS_CEIL = 12.0, 70.0
DWELL_DAYS_FLOOR, DWELL_DAYS_CEIL = 0.5, 12.0
CPPI_RANK_FLOOR, CPPI_RANK_CEIL = 1.0, 370.0


class CongestionTier(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    ELEVATED = "elevated"
    HIGH = "high"


#: Lower bound of each tier on the [0, 1] score.
TIER_THRESHOLDS: list[tuple[float, CongestionTier]] = [
    (0.70, CongestionTier.HIGH),
    (0.50, CongestionTier.ELEVATED),
    (0.30, CongestionTier.MODERATE),
    (0.00, CongestionTier.LOW),
]


@dataclass
class PortCongestion:
    unlocode: str
    port_name: str
    country_iso3: str
    score: float
    tier: CongestionTier
    #: Per-signal contributions, so a score can always be explained.
    components: dict[str, float | None]

    def as_dict(self) -> dict[str, Any]:
        return {
            "unlocode": self.unlocode,
            "portName": self.port_name,
            "countryIso3": self.country_iso3,
            "score": round(self.score, 4),
            "tier": self.tier.value,
            "components": {
                k: (round(v, 4) if v is not None else None)
                for k, v in self.components.items()
            },
        }


def _normalise(value: float | None, floor: float, ceil: float) -> float | None:
    """Scale into [0, 1], clamped. None propagates rather than defaulting to 0."""
    if value is None:
        return None
    if ceil <= floor:
        return 0.0
    return max(0.0, min(1.0, (float(value) - floor) / (ceil - floor)))


def tier_for(score: float) -> CongestionTier:
    for threshold, tier in TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return CongestionTier.LOW


def score_port(
    port: PortPerformance, country: CountryLogistics | None = None
) -> PortCongestion:
    """Combine the available signals, renormalising over whichever are present.

    Missing signals are dropped and the weights rescaled, rather than imputed as
    zero. Treating an absent measurement as "no congestion" would systematically
    flatter ports with poor reporting — exactly the ports most likely to have a
    problem.
    """
    components: dict[str, float | None] = {
        "vessel_hours": _normalise(
            port.avg_vessel_hours, VESSEL_HOURS_FLOOR, VESSEL_HOURS_CEIL
        ),
        "import_dwell_days": _normalise(
            country.import_dwell_days if country else None,
            DWELL_DAYS_FLOOR,
            DWELL_DAYS_CEIL,
        ),
        "cppi_rank": _normalise(
            float(port.cppi_rank) if port.cppi_rank is not None else None,
            CPPI_RANK_FLOOR,
            CPPI_RANK_CEIL,
        ),
    }
    weights = {
        "vessel_hours": WEIGHT_VESSEL_HOURS,
        "import_dwell_days": WEIGHT_DWELL_DAYS,
        "cppi_rank": WEIGHT_CPPI_RANK,
    }

    available = {k: v for k, v in components.items() if v is not None}
    total_weight = sum(weights[k] for k in available) or 1.0
    score = sum(v * weights[k] for k, v in available.items()) / total_weight

    return PortCongestion(
        unlocode=port.unlocode or port.port_name,
        port_name=port.port_name,
        country_iso3=port.country_iso3,
        score=score,
        tier=tier_for(score),
        components=components,
    )


def score_ports(
    ports: Iterable[PortPerformance], countries: Iterable[CountryLogistics]
) -> list[PortCongestion]:
    """Score every port, joining each to its country's latest LPI record."""
    latest: dict[str, CountryLogistics] = {}
    for record in countries:
        current = latest.get(record.country_iso3)
        if current is None or record.year > current.year:
            latest[record.country_iso3] = record

    scored = [score_port(port, latest.get(port.country_iso3)) for port in ports]
    scored.sort(key=lambda p: -p.score)
    return scored
