"""Phase G — the instrument model.

Every wedge on the roadmap reduces to one sentence: *apply a versioned legal
instrument to a shipment, and cite it.* This module is that sentence as types.

The design pressure is entirely temporal. A tariff engine that answers with a
superseded rate has not made a mistake — it has produced a number someone files
a customs entry on. Section 232 was modified in June 2026; anything that
retrieved May's rate without knowing it had been replaced is confidently,
citably wrong.

So `Instrument` is not "a rule". It is **a rule with a window**, and every
query against it is a query at a date.

Two honesty rules carried forward from Phase A:

1. An inferred effective date is marked `DateBasis.INFERRED_FROM_PUBLICATION`,
   never silently treated as explicit. Publication is not commencement, and the
   gap is routinely weeks.
2. `origin` is `LIVE` or `SYNTHETIC`, set explicitly, and no synthetic
   instrument may produce a published figure.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from marsa.ingestion.schemas import Origin

#: Practical open-ended upper bound. `None` means "still in force", and this is
#: what it becomes when a date is needed for comparison.
FOREVER = date(9999, 12, 31)


class Issuer(StrEnum):
    USTR = "ustr"
    CBP = "cbp"
    DHS = "dhs"
    USITC = "usitc"
    EU_COMMISSION = "eu_commission"


class InstrumentKind(StrEnum):
    BASE_RATE = "base_rate"          # USITC HTS column 1 general (MFN)
    TARIFF = "tariff"                # 232 / 301 / IEEPA — additive overlays
    ENTITY_LISTING = "entity_listing"  # UFLPA Entity List
    GOODS_SCOPE = "goods_scope"      # CBAM Annex I
    RULING = "ruling"                # CBP CROSS binding ruling


class Programme(StrEnum):
    """The legal authority an instrument operates under.

    Distinct from `kind`, and the distinction is load-bearing: Section 232 and
    Section 301 are both `TARIFF`, but they are *designed to stack* — an entry
    can attract both. Two Section 232 rates over the same goods cannot both be
    right.

    So conflict is detected **within** a programme and stacking happens
    **across** them. Without this field the resolver reports every legitimate
    232+301 stack as a contradiction, which trains a reader to ignore the
    warning that matters.
    """

    MFN = "mfn"
    SECTION_232 = "section_232"
    SECTION_301 = "section_301"
    IEEPA = "ieepa"
    CBAM = "cbam"
    UFLPA = "uflpa"
    RULING = "ruling"
    UNKNOWN = "unknown"


class DateBasis(StrEnum):
    """Where the effective date came from. Never guessed silently."""

    EXPLICIT = "explicit"                              # the source stated it
    INFERRED_FROM_PUBLICATION = "inferred_from_publication"
    UNKNOWN = "unknown"                                # no date at all


class EffectKind(StrEnum):
    AD_VALOREM = "ad_valorem"                # a percentage of customs value
    PROHIBITION = "prohibition"              # UFLPA — rebuttable presumption
    CERTIFICATE_REQUIRED = "certificate_required"  # CBAM
    CLASSIFICATION = "classification"        # a ruling assigning an HTS code


class Scope(BaseModel):
    """What an instrument applies to.

    Empty means *unrestricted on that axis*, not "matches nothing" — a Section
    232 proclamation restricted by HTS but not by country has an empty
    `origin_countries` and applies to every origin. Getting that backwards
    silently narrows every instrument to nothing.
    """

    hts_prefixes: list[str] = Field(default_factory=list)
    origin_countries: list[str] = Field(default_factory=list)
    entity_names: list[str] = Field(default_factory=list)

    @field_validator("hts_prefixes", mode="after")
    @classmethod
    def _normalise_hts(cls, prefixes: list[str]) -> list[str]:
        # Dots are presentational: "7326.90" and "732690" are the same prefix,
        # and sources are not consistent about which they emit.
        return [p.replace(".", "").strip() for p in prefixes if p.strip()]

    @field_validator("origin_countries", mode="after")
    @classmethod
    def _normalise_country(cls, countries: list[str]) -> list[str]:
        return [c.strip().upper() for c in countries if c.strip()]

    def matches(
        self,
        *,
        hts: str | None = None,
        origin: str | None = None,
        entity: str | None = None,
    ) -> bool:
        """Does this scope cover the given entry?

        Note the asymmetry with `is_unrestricted`. An *empty* axis is
        unrestricted, but a *populated* axis the caller did not supply a value
        for is a miss, not a pass. Without that, a UFLPA entity listing — whose
        scope is a company name and nothing else — matches every duty query
        that names only an HTS code, and a single lookup returns all 187
        listings as applicable. That is not a cosmetic problem: it would put
        forced-labour prohibitions into the duty stack.
        """
        if self.hts_prefixes:
            if hts is None:
                return False
            flat = hts.replace(".", "").strip()
            if not any(flat.startswith(p) for p in self.hts_prefixes):
                return False
        if self.origin_countries:
            if origin is None:
                return False
            if origin.strip().upper() not in self.origin_countries:
                return False
        if self.entity_names:
            if entity is None:
                return False
            needle = entity.strip().casefold()
            if not any(needle == name.strip().casefold() for name in self.entity_names):
                return False
        return True

    @property
    def is_unrestricted(self) -> bool:
        return not (self.hts_prefixes or self.origin_countries or self.entity_names)


class Effect(BaseModel):
    """What the instrument does once it applies."""

    kind: EffectKind
    rate_percent: float | None = None
    #: Whether this rate stacks on top of others. Section 232 and 301 are
    #: additive with each other and with the MFN base; a base rate is not.
    additive: bool = True
    #: Total-duty ceiling, e.g. the 15% cap for EU/UK/JP/KR origins.
    cap_percent: float | None = None
    #: Origins the cap applies to. Empty means it applies to every origin the
    #: instrument covers.
    #:
    #: Load-bearing, and not obviously so: the June 2026 proclamation imposes a
    #: 50% rate on everyone and caps *some* origins at 15% total. Modelling the
    #: cap without its own origin list applies it to all of them, which turns a
    #: 77.9% China stack into 15% — understating duty by a factor of five, and
    #: understated duty is the expensive direction to be wrong in.
    cap_origins: list[str] = Field(default_factory=list)
    note: str = ""

    @field_validator("cap_origins", mode="after")
    @classmethod
    def _normalise_cap_origins(cls, origins: list[str]) -> list[str]:
        return [o.strip().upper() for o in origins if o.strip()]

    def cap_applies_to(self, origin: str | None) -> bool:
        if self.cap_percent is None:
            return False
        if not self.cap_origins:
            return True
        return bool(origin) and origin.strip().upper() in self.cap_origins

    @model_validator(mode="after")
    def _rate_required_for_ad_valorem(self) -> Effect:
        if self.kind is EffectKind.AD_VALOREM and self.rate_percent is None:
            raise ValueError("ad valorem effect needs a rate_percent")
        return self


class Instrument(BaseModel):
    """A legal instrument with the window it was in force."""

    id: str
    issuer: Issuer
    kind: InstrumentKind
    programme: Programme = Programme.UNKNOWN
    title: str = ""
    scope: Scope = Field(default_factory=Scope)
    effect: Effect

    effective_from: date
    effective_to: date | None = None  # None = still in force
    date_basis: DateBasis = DateBasis.EXPLICIT

    #: IDs this instrument replaces. The June 2026 Section 232 proclamation
    #: supersedes the earlier one rather than stacking with it.
    supersedes: list[str] = Field(default_factory=list)

    source_url: str = ""
    retrieved_at: datetime
    #: Hash of the source text. Sources edit in place without notice; comparing
    #: this across fetches is the only way to notice.
    text_hash: str = ""

    origin: Origin = Origin.SYNTHETIC

    @model_validator(mode="after")
    def _window_is_ordered(self) -> Instrument:
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError(
                f"{self.id}: effective_to {self.effective_to} precedes "
                f"effective_from {self.effective_from}"
            )
        return self

    def in_force_on(self, on: date) -> bool:
        return self.effective_from <= on <= (self.effective_to or FOREVER)

    def applies_to(
        self,
        *,
        hts: str | None = None,
        origin: str | None = None,
        entity: str | None = None,
        on: date,
    ) -> bool:
        return self.in_force_on(on) and self.scope.matches(
            hts=hts, origin=origin, entity=entity
        )

    @property
    def date_is_trustworthy(self) -> bool:
        """Only an explicit date can carry a point-in-time claim.

        An inferred one is usable — it is better than nothing — but any answer
        resting on it has to say so, which is what `asof` reports.
        """
        return self.date_basis is DateBasis.EXPLICIT

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def hash_text(text: str) -> str:
    """Stable content hash, for detecting silent upstream edits."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
