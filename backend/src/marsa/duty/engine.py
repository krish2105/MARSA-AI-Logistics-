"""Phase H — the layered duty calculator.

**No LLM touches this arithmetic, ever.** That is the whole design constraint.
A language model asked what 2.9% + 50% + 25% comes to on a $40,000 shipment
will produce a number that looks right, and the number is the deliverable. So
the model's only job downstream is to *explain* a table it did not compute.

The stack, in the order the layers actually apply:

    MFN base                    (USITC column 1 general — the floor, not a layer)
  + Section 232                 (steel/aluminium/copper, additive)
  + Section 301                 (China, additive)
  + IEEPA reciprocal            (additive)
  → total-duty cap             (15% ceiling for EU/UK/JP/KR/CH/TW…)
  → USMCA non-US-content rule  (duty on non-US content only, 15% floor)

Two decisions worth stating, because both are contestable:

**The cap is a ceiling on the total, not on any one layer.** The June 2026
modification is described as capping affected origins at a 15% *total duty
outcome*, so a capped origin can pay less than an uncapped one under identical
stacking. Capping only the 232 component would produce materially higher
figures for China-origin goods attracting both 232 and 301.

**A gap is a refusal, not a zero.** If there is no MFN rate for a code, or two
instruments contradict, or an effective date was inferred rather than stated,
the engine returns no total. A missing layer silently treated as 0% understates
duty, and understated duty is the expensive direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from marsa.logging import get_logger
from marsa.regulatory.schema import EffectKind, Instrument, InstrumentKind, Programme
from marsa.regulatory.store import InstrumentStore
from marsa.regulatory.supersede import ResolutionReport

log = get_logger(__name__)

#: Order layers appear in the output. Presentation only — addition commutes,
#: but a table that lists them out of legal order is hard to check by hand.
LAYER_ORDER = (
    Programme.MFN,
    Programme.SECTION_232,
    Programme.SECTION_301,
    Programme.IEEPA,
)

#: USMCA-covered origins: duty applies to non-US content only.
USMCA_ORIGINS = frozenset({"CA", "MX"})

#: Minimum effective rate under the USMCA carve-out.
USMCA_FLOOR_PERCENT = Decimal("15")

#: Money is Decimal throughout. A float total on a $2.4m entry drifts by cents,
#: and cents are what a customs broker reconciles against.
CENTS = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


@dataclass
class Layer:
    """One duty component, and the instrument that imposes it."""

    programme: Programme
    rate_percent: Decimal
    amount: Decimal
    instrument_id: str
    instrument_title: str
    source_url: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "programme": self.programme.value,
            "ratePercent": float(self.rate_percent),
            "amount": float(self.amount),
            "instrumentId": self.instrument_id,
            "instrumentTitle": self.instrument_title,
            "sourceUrl": self.source_url,
            "note": self.note,
        }


@dataclass
class Adjustment:
    """A cap or floor applied after the layers are summed."""

    kind: str  # "total_duty_cap" | "usmca_content" | "usmca_floor"
    description: str
    before_percent: Decimal
    after_percent: Decimal
    instrument_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "description": self.description,
            "beforePercent": float(self.before_percent),
            "afterPercent": float(self.after_percent),
            "instrumentId": self.instrument_id,
        }


@dataclass
class DutyQuote:
    """The computed stack, or an explicit refusal."""

    hts: str
    origin: str
    customs_value: Decimal
    on: date

    layers: list[Layer] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)

    #: Populated when the engine will not produce a number. Non-empty means
    #: `total_percent` and `total_amount` are None, not zero.
    refusals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    us_content_percent: Decimal = Decimal("0")

    @property
    def answered(self) -> bool:
        return not self.refusals

    @property
    def total_percent(self) -> Decimal | None:
        if not self.answered:
            return None
        rate = sum((layer.rate_percent for layer in self.layers), Decimal("0"))
        for adjustment in self.adjustments:
            rate = adjustment.after_percent
        return rate

    @property
    def total_amount(self) -> Decimal | None:
        rate = self.total_percent
        if rate is None:
            return None
        return _money(self.customs_value * rate / Decimal("100"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": {
                "hts": self.hts,
                "origin": self.origin,
                "customsValue": float(self.customs_value),
                "on": self.on.isoformat(),
                "usContentPercent": float(self.us_content_percent),
            },
            "answered": self.answered,
            "layers": [layer.as_dict() for layer in self.layers],
            "adjustments": [a.as_dict() for a in self.adjustments],
            "totalPercent": float(self.total_percent) if self.answered else None,
            "totalAmount": float(self.total_amount) if self.answered else None,
            "refusals": self.refusals,
            "warnings": self.warnings,
        }


def _rate(instrument: Instrument) -> Decimal:
    # str() first: Decimal(float) inherits the float's binary error, so
    # Decimal(2.9) is 2.899999... and the cent-level rounding stops matching.
    return Decimal(str(instrument.effect.rate_percent or 0))


def quote(
    store: InstrumentStore,
    *,
    hts: str,
    origin: str,
    customs_value: Decimal | float | str,
    on: date,
    us_content_percent: Decimal | float | str = 0,
) -> DutyQuote:
    """Compute the layered duty for one entry, or refuse and say why."""
    value = Decimal(str(customs_value))
    us_content = Decimal(str(us_content_percent))
    origin = origin.strip().upper()

    result = DutyQuote(
        hts=hts, origin=origin, customs_value=value, on=on,
        us_content_percent=us_content,
    )

    resolution: ResolutionReport = store.asof(on, hts=hts, origin=origin)

    # ── Refusals, before any arithmetic ─────────────────────────────────────
    for contradiction in resolution.contradictions:
        result.refusals.append(
            f"{contradiction.left} and {contradiction.right} are both in force "
            f"over this entry at different rates ({contradiction.left_rate}% vs "
            f"{contradiction.right_rate}%) and neither supersedes the other. "
            "Resolve the conflict; the engine will not pick one."
        )
    for instrument_id in resolution.inferred_dates:
        result.refusals.append(
            f"{instrument_id} has an inferred effective date — it was taken from "
            "the publication date, which is not commencement. The window may be "
            "wrong, so this entry cannot be priced to a date."
        )

    # ── Collect the layers ──────────────────────────────────────────────────
    applicable = [
        i
        for i in resolution.effective
        if i.kind in (InstrumentKind.BASE_RATE, InstrumentKind.TARIFF)
        and i.effect.kind is EffectKind.AD_VALOREM
    ]

    base = [i for i in applicable if i.programme is Programme.MFN]
    if not base:
        result.refusals.append(
            f"No MFN base rate held for HTS {hts} on {on.isoformat()}. Every "
            "overlay stacks on the base, so without it the total would be "
            "understated — which is the expensive direction to be wrong in."
        )

    if result.refusals:
        log.info("duty quote refused", extra={"hts": hts, "reasons": len(result.refusals)})
        return result

    by_programme = {i.programme: i for i in applicable}
    for programme in LAYER_ORDER:
        instrument = by_programme.get(programme)
        if instrument is None:
            continue
        rate = _rate(instrument)
        result.layers.append(
            Layer(
                programme=programme,
                rate_percent=rate,
                amount=_money(value * rate / Decimal("100")),
                instrument_id=instrument.id,
                instrument_title=instrument.title,
                source_url=instrument.source_url,
                note=instrument.effect.note,
            )
        )

    unknown = [i for i in applicable if i.programme is Programme.UNKNOWN]
    if unknown:
        result.warnings.append(
            f"{len(unknown)} instrument(s) apply but carry no programme label, so "
            "they are NOT in the stack: "
            f"{', '.join(i.id for i in unknown)}. The total is a lower bound."
        )

    gross = sum((layer.rate_percent for layer in result.layers), Decimal("0"))
    running = gross

    # ── Total-duty cap ──────────────────────────────────────────────────────
    # A ceiling on the sum, not on any one layer. So a capped origin can pay
    # less than an uncapped one under otherwise identical stacking.
    caps = [
        (i, Decimal(str(i.effect.cap_percent)))
        for i in applicable
        if i.effect.cap_applies_to(origin)
    ]
    if caps:
        instrument, cap = min(caps, key=lambda pair: pair[1])
        if running > cap:
            result.adjustments.append(
                Adjustment(
                    kind="total_duty_cap",
                    description=(
                        f"Total duty capped at {cap}% for origin {origin}; the "
                        f"layers summed to {running}%."
                    ),
                    before_percent=running,
                    after_percent=cap,
                    instrument_id=instrument.id,
                )
            )
            running = cap

    # ── USMCA non-US content ────────────────────────────────────────────────
    if origin in USMCA_ORIGINS:
        # Duty applies only to the non-US portion, then a floor applies to the
        # effective rate. Ordering matters: applying the floor before the
        # content reduction would produce a materially higher figure.
        non_us = (Decimal("100") - us_content) / Decimal("100")
        reduced = running * non_us
        result.adjustments.append(
            Adjustment(
                kind="usmca_content",
                description=(
                    f"USMCA: duty applies to the {(non_us * 100).normalize()}% "
                    f"non-US content only."
                ),
                before_percent=running,
                after_percent=reduced,
            )
        )
        running = reduced

        if running < USMCA_FLOOR_PERCENT:
            result.adjustments.append(
                Adjustment(
                    kind="usmca_floor",
                    description=(
                        f"USMCA minimum effective rate of {USMCA_FLOOR_PERCENT}% "
                        "applies."
                    ),
                    before_percent=running,
                    after_percent=USMCA_FLOOR_PERCENT,
                )
            )
            running = USMCA_FLOOR_PERCENT

    log.info(
        "duty quoted",
        extra={"hts": hts, "origin": origin, "total_percent": float(running)},
    )
    return result
