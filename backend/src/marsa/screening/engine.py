"""Phase I — screen a supplier list against entity listings and goods scopes.

**The output is three-valued and none of the three is "clear".**

That is the whole design. A screening tool that reports "no match" reads as a
clearance, and an importer acts on it: the container ships, CBP detains it, and
the burden is then on them to produce "clear and convincing evidence" within
thirty days. The tool did not check the world — it checked a list it holds, of
a version it knows, at a moment it can name. Saying so is the difference
between a useful instrument and a liability.

So:

    HIT              — matched a listing at or above the identity threshold
    POSSIBLE         — matched enough to warrant a human look
    NO_EVIDENCE_FOUND — nothing in *this* list version matched

The last one is deliberately not `CLEAR`, not `PASS`, and not a green tick.

The list moves, too: DHS added 43 entities in a single day in August 2026. So
every result carries the list version and its age, and a stale list downgrades
the finding rather than being mentioned in a footnote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from marsa.logging import get_logger
from marsa.regulatory.schema import Instrument, InstrumentKind, Programme
from marsa.regulatory.store import InstrumentStore
from marsa.screening.matcher import Match, match

log = get_logger(__name__)


class Finding(StrEnum):
    HIT = "hit"
    POSSIBLE = "possible"
    #: Not "clear". The tool checked a list, not the world.
    NO_EVIDENCE_FOUND = "no_evidence_found"


@dataclass
class SupplierResult:
    """One supplier, screened."""

    supplier: str
    finding: Finding
    matches: list[Match] = field(default_factory=list)
    instrument_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def best_score(self) -> float:
        return self.matches[0].score if self.matches else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "supplier": self.supplier,
            "finding": self.finding.value,
            "bestScore": round(self.best_score, 4),
            "matches": [m.as_dict() for m in self.matches[:5]],
            "instrumentIds": self.instrument_ids,
            "notes": self.notes,
        }


@dataclass
class GoodsResult:
    """One HTS code, checked against goods scopes such as CBAM Annex I."""

    hts: str
    in_scope: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def finding(self) -> Finding:
        # A goods scope is a code list, not a fuzzy match — either the code is
        # inside it or it is not. There is no POSSIBLE here, and inventing one
        # would imply a doubt the data does not contain.
        return Finding.HIT if self.in_scope else Finding.NO_EVIDENCE_FOUND

    def as_dict(self) -> dict[str, Any]:
        return {
            "hts": self.hts,
            "finding": self.finding.value,
            "inScope": self.in_scope,
            "notes": self.notes,
        }


@dataclass
class ScreeningReport:
    on: date
    suppliers: list[SupplierResult] = field(default_factory=list)
    goods: list[GoodsResult] = field(default_factory=list)
    list_version: str = ""
    list_age_days: float | None = None
    list_is_stale: bool = True
    entities_checked: int = 0
    any_synthetic: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def hits(self) -> list[SupplierResult]:
        return [s for s in self.suppliers if s.finding is Finding.HIT]

    @property
    def possibles(self) -> list[SupplierResult]:
        return [s for s in self.suppliers if s.finding is Finding.POSSIBLE]

    @property
    def disclaimer(self) -> str:
        """Shown with every result, not buried in documentation."""
        return (
            f"Screened against {self.entities_checked} listed entities as held on "
            f"{self.list_version or 'an unknown date'}. "
            "'No evidence found' means nothing in THIS list version matched — it "
            "is not a clearance, and it is not a statement about the supplier."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "on": self.on.isoformat(),
            "suppliers": [s.as_dict() for s in self.suppliers],
            "goods": [g.as_dict() for g in self.goods],
            "listVersion": self.list_version,
            "listAgeDays": self.list_age_days,
            "listIsStale": self.list_is_stale,
            "entitiesChecked": self.entities_checked,
            "anySynthetic": self.any_synthetic,
            "counts": {
                "hit": len(self.hits),
                "possible": len(self.possibles),
                "noEvidenceFound": len(self.suppliers) - len(self.hits) - len(self.possibles),
            },
            "warnings": self.warnings,
            "disclaimer": self.disclaimer,
        }


def _listed_entities(store: InstrumentStore, on: date) -> list[Instrument]:
    """Entity listings in force on a date.

    Point-in-time matters here as much as it does for a tariff: an entity added
    in August was not listed in March, and screening a March entry against
    today's list would report a detention risk that did not exist.
    """
    return [
        i
        for i in store.all()
        if i.kind is InstrumentKind.ENTITY_LISTING and i.in_force_on(on)
    ]


def screen(
    store: InstrumentStore,
    *,
    suppliers: list[str],
    hts_codes: list[str] | None = None,
    on: date | None = None,
) -> ScreeningReport:
    """Screen suppliers against entity listings, and codes against goods scopes."""
    as_of = on or date.today()
    listings = _listed_entities(store, as_of)
    names = [n for i in listings for n in i.scope.entity_names]
    by_name = {n: i.id for i in listings for n in i.scope.entity_names}

    staleness = store.staleness()
    report = ScreeningReport(
        on=as_of,
        list_version=staleness.get("oldestRetrievedAt") or "",
        list_age_days=staleness.get("ageDays"),
        list_is_stale=bool(staleness.get("isStale", True)),
        entities_checked=len(names),
        any_synthetic=bool(staleness.get("anySynthetic", True)),
    )

    if not names:
        report.warnings.append(
            "No entity listings are loaded, so every supplier below returns "
            "NO_EVIDENCE_FOUND for the trivial reason that nothing was checked. "
            "Run `marsa-reg fixtures` or `marsa-reg ingest`."
        )
    if report.list_is_stale:
        report.warnings.append(
            f"The entity list is {report.list_age_days} days old. DHS added 43 "
            "entities in a single day in August 2026; a result from a stale list "
            "understates risk."
        )
    if report.any_synthetic:
        report.warnings.append(
            "Listings are SYNTHETIC. This exercises the matcher; it screens "
            "against nothing real."
        )

    for supplier in suppliers:
        found = match(supplier, names)
        if found and found[0].is_hit:
            finding = Finding.HIT
        elif found:
            finding = Finding.POSSIBLE
        else:
            finding = Finding.NO_EVIDENCE_FOUND

        result = SupplierResult(
            supplier=supplier,
            finding=finding,
            matches=found,
            instrument_ids=[by_name[m.listed_name] for m in found if m.listed_name in by_name],
        )
        if finding is Finding.POSSIBLE:
            result.notes.append(
                f"Closest listed name is {found[0].listed_name!r} at "
                f"{found[0].score:.0%} on shared tokens "
                f"{', '.join(found[0].shared_tokens)}. A human decides this one."
            )
        elif finding is Finding.NO_EVIDENCE_FOUND:
            result.notes.append(
                "Nothing in this list version matched. That is not a clearance."
            )
        report.suppliers.append(result)

    for code in hts_codes or []:
        scopes = [
            i
            for i in store.all()
            if i.kind is InstrumentKind.GOODS_SCOPE
            and i.in_force_on(as_of)
            and i.scope.matches(hts=code)
        ]
        result = GoodsResult(hts=code, in_scope=[i.id for i in scopes])
        for instrument in scopes:
            if instrument.programme is Programme.CBAM:
                result.notes.append(
                    f"{instrument.id}: CBAM reporting and certificate obligations "
                    "apply on EU import. Since 1 January 2026 a missing or wrong "
                    "CBAM code causes the declaration to be rejected outright."
                )
        report.goods.append(result)

    log.info(
        "screening complete",
        extra={
            "suppliers": len(report.suppliers),
            "hits": len(report.hits),
            "possible": len(report.possibles),
        },
    )
    return report
