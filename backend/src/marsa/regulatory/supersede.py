"""Supersession and contradiction — the two ways a rule set lies to you.

**Supersession** is the easy one, and only because the sources say so: an
instrument names what it replaces, and a replaced instrument must drop out of
the effective set even while its own date window is still open. Section 232's
June 2026 proclamation does not stack with the one before it; it replaces it.
Treating both as live double-counts the duty.

**Contradiction** is the hard one. Two instruments can be in force, in scope,
unsuperseded, and disagree — because regulations genuinely conflict, because an
ingestor mis-parsed, or because one was corrected without a formal notice. The
temptation is to pick the newer one and move on.

This module refuses to. A silently-resolved contradiction produces a confident
number with no signal that anything was wrong, which is precisely the failure
this project exists not to commit. Both instruments are returned, flagged, and
the caller must decide — or decline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from marsa.logging import get_logger
from marsa.regulatory.schema import (
    EffectKind,
    Instrument,
    InstrumentKind,
    Programme,
)

log = get_logger(__name__)


@dataclass
class Contradiction:
    """Two instruments that cannot both be right."""

    left: str
    right: str
    kind: InstrumentKind
    reason: str
    left_rate: float | None = None
    right_rate: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "left": self.left,
            "right": self.right,
            "kind": self.kind.value,
            "reason": self.reason,
            "leftRate": self.left_rate,
            "rightRate": self.right_rate,
        }


def superseded_ids(instruments: list[Instrument], *, on: date) -> set[str]:
    """IDs displaced by another instrument that is itself in force on `on`.

    The `in force` qualifier matters. A superseding instrument that has not yet
    commenced must not displace the one currently operating — otherwise a
    proclamation signed in June silently voids May's rate for an entry filed in
    May, and the answer for a past date becomes wrong the moment a future rule
    is ingested.
    """
    live = {i.id for i in instruments if i.in_force_on(on)}
    out: set[str] = set()
    for instrument in instruments:
        if instrument.id in live:
            out.update(instrument.supersedes)
    return out


def effective_set(
    instruments: list[Instrument],
    *,
    on: date,
    hts: str | None = None,
    origin: str | None = None,
    entity: str | None = None,
) -> list[Instrument]:
    """The instruments actually operating on a date, for an entry."""
    displaced = superseded_ids(instruments, on=on)
    return [
        i
        for i in instruments
        if i.id not in displaced
        and i.applies_to(hts=hts, origin=origin, entity=entity, on=on)
    ]


def find_contradictions(instruments: list[Instrument]) -> list[Contradiction]:
    """Pairs that are simultaneously in force, in the same lane, and disagree.

    Only compares instruments in the same **programme** whose scopes could
    cover a common entry. Two Section 232 rates on overlapping HTS prefixes at
    different percentages is a contradiction; a 232 rate and a 301 rate on the
    same code is not — those are designed to stack, and an entry legitimately
    attracts both.

    Comparing on `kind` alone was the first implementation and it was wrong:
    232 and 301 are both `TARIFF`, so every correct stack was reported as a
    conflict. A warning that fires on normal behaviour is one a reader learns
    to dismiss, which costs more than not warning at all.
    """
    found: list[Contradiction] = []

    for index, left in enumerate(instruments):
        for right in instruments[index + 1 :]:
            if left.kind is not right.kind:
                continue
            # Different legal authorities stack rather than conflict.
            if left.programme is not right.programme:
                continue
            # Two instruments of unknown programme cannot be shown to conflict;
            # they may be from different authorities that were never labelled.
            if left.programme is Programme.UNKNOWN:
                continue
            if not _windows_overlap(left, right):
                continue
            if not _scopes_could_collide(left, right):
                continue
            # An explicit supersession is a resolution, not a conflict.
            if right.id in left.supersedes or left.id in right.supersedes:
                continue

            if (
                left.effect.kind is EffectKind.AD_VALOREM
                and left.effect.rate_percent != right.effect.rate_percent
            ):
                found.append(
                    Contradiction(
                        left=left.id,
                        right=right.id,
                        kind=left.kind,
                        reason=(
                            "Both in force over an overlapping scope and window, "
                            "with different ad valorem rates and no supersession "
                            "between them."
                        ),
                        left_rate=left.effect.rate_percent,
                        right_rate=right.effect.rate_percent,
                    )
                )

    if found:
        log.warning("contradictions detected", extra={"count": len(found)})
    return found


def _windows_overlap(left: Instrument, right: Instrument) -> bool:
    from marsa.regulatory.schema import FOREVER

    return (
        left.effective_from <= (right.effective_to or FOREVER)
        and right.effective_from <= (left.effective_to or FOREVER)
    )


def _scopes_could_collide(left: Instrument, right: Instrument) -> bool:
    """Whether some entry could fall inside both scopes.

    Deliberately permissive: an unrestricted axis collides with anything, and
    HTS prefixes collide when either is a prefix of the other. Being too eager
    here surfaces a false contradiction, which a human dismisses. Being too
    strict hides a real one, which nobody ever sees.
    """
    l_hts, r_hts = left.scope.hts_prefixes, right.scope.hts_prefixes
    if (
        l_hts
        and r_hts
        and not any(a.startswith(b) or b.startswith(a) for a in l_hts for b in r_hts)
    ):
        return False

    l_geo, r_geo = left.scope.origin_countries, right.scope.origin_countries
    return not (l_geo and r_geo and not (set(l_geo) & set(r_geo)))


@dataclass
class ResolutionReport:
    """What `asof` actually returned, and what it could not resolve."""

    on: date
    effective: list[Instrument] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)
    inferred_dates: list[str] = field(default_factory=list)

    @property
    def is_trustworthy(self) -> bool:
        """Whether this resolution can carry a published figure.

        False when anything is contradictory or rests on an inferred date. The
        caller is expected to degrade rather than proceed — the same contract as
        RESULTS.md's publication gate.
        """
        return not self.contradictions and not self.inferred_dates

    def as_dict(self) -> dict[str, Any]:
        return {
            "on": self.on.isoformat(),
            "effective": [i.as_dict() for i in self.effective],
            "superseded": self.superseded,
            "contradictions": [c.as_dict() for c in self.contradictions],
            "inferredDates": self.inferred_dates,
            "isTrustworthy": self.is_trustworthy,
        }


def resolve(
    instruments: list[Instrument],
    *,
    on: date,
    hts: str | None = None,
    origin: str | None = None,
    entity: str | None = None,
) -> ResolutionReport:
    """Point-in-time resolution, with everything it could not settle attached."""
    effective = effective_set(instruments, on=on, hts=hts, origin=origin, entity=entity)
    return ResolutionReport(
        on=on,
        effective=effective,
        superseded=sorted(superseded_ids(instruments, on=on)),
        contradictions=find_contradictions(effective),
        inferred_dates=[i.id for i in effective if not i.date_is_trustworthy],
    )
