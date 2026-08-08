"""Synthetic instruments, so Phase G is testable where the sources are not.

Modelled on the real 2026 regime — a superseded Section 232, its June
replacement, a Section 301 China overlay, an IEEPA reciprocal tariff, the 15%
country cap, CBAM sector scope and UFLPA listings — because a fixture that does
not reproduce the *shape* of the problem (stacking, supersession, caps) proves
nothing about the code that has to handle it.

Every instrument carries `origin=SYNTHETIC`. The same rule as Phase A applies
and is enforced by a test: **no figure derived from these may be published.**
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from marsa.ingestion.schemas import Origin
from marsa.regulatory.schema import (
    DateBasis,
    Effect,
    EffectKind,
    Instrument,
    InstrumentKind,
    Issuer,
    Programme,
    Scope,
    hash_text,
)

NOTE = "SYNTHETIC — not a real legal instrument. Never publish a figure from this."

#: Origins capped at 15% total duty. From the June 2026 Section 232 modification.
CAPPED_ORIGINS = ["EU", "GB", "JP", "KR", "CH", "TW", "AR", "EC", "SV", "GT", "LI"]


def _make(
    id: str,
    issuer: Issuer,
    kind: InstrumentKind,
    programme: Programme,
    title: str,
    scope: Scope,
    effect: Effect,
    effective_from: date,
    effective_to: date | None = None,
    supersedes: list[str] | None = None,
    basis: DateBasis = DateBasis.EXPLICIT,
) -> Instrument:
    return Instrument(
        id=id,
        issuer=issuer,
        kind=kind,
        programme=programme,
        title=title,
        scope=scope,
        effect=effect,
        effective_from=effective_from,
        effective_to=effective_to,
        date_basis=basis,
        supersedes=supersedes or [],
        source_url=f"https://example.invalid/synthetic/{id}",
        retrieved_at=datetime.now(UTC),
        text_hash=hash_text(id + title),
        origin=Origin.SYNTHETIC,
    )


def generate_instruments(*, with_contradiction: bool = False) -> list[Instrument]:
    """A corpus that exercises every behaviour Phase G claims.

    Set `with_contradiction` to inject a pair that cannot both be right, for
    demonstrating that the resolver flags rather than silently picks.
    """
    steel_alu = ["72", "73", "76"]

    out = [
        # ── Section 232: superseded, and its replacement ──────────────────
        # The pair that makes supersession demonstrable. The February
        # instrument's own window never closes; it stops applying because June
        # replaces it. A resolver that only checked dates would double-count.
        _make(
            "FR-2026-02-232-STEEL",
            Issuer.USTR,
            InstrumentKind.TARIFF,
            Programme.SECTION_232,
            "Section 232 — steel, aluminium and copper (February 2026)",
            Scope(hts_prefixes=steel_alu),
            Effect(kind=EffectKind.AD_VALOREM, rate_percent=25.0, additive=True, note=NOTE),
            date(2026, 2, 1),
        ),
        _make(
            "FR-2026-06-232-STEEL",
            Issuer.USTR,
            InstrumentKind.TARIFF,
            Programme.SECTION_232,
            "Section 232 — steel, aluminium and copper, as modified (June 2026)",
            Scope(hts_prefixes=steel_alu),
            Effect(
                kind=EffectKind.AD_VALOREM,
                rate_percent=50.0,
                additive=True,
                cap_percent=15.0,
                cap_origins=CAPPED_ORIGINS,
                note=f"{NOTE} Capped at 15% total duty for {', '.join(CAPPED_ORIGINS[:4])} et al.",
            ),
            date(2026, 6, 8),
            effective_to=date(2027, 12, 31),
            supersedes=["FR-2026-02-232-STEEL"],
        ),
        # ── Section 301 — stacks with 232 rather than replacing it ────────
        _make(
            "FR-2026-301-CN",
            Issuer.USTR,
            InstrumentKind.TARIFF,
            Programme.SECTION_301,
            "Section 301 — China",
            Scope(hts_prefixes=["72", "73", "76", "85"], origin_countries=["CN"]),
            Effect(kind=EffectKind.AD_VALOREM, rate_percent=25.0, additive=True, note=NOTE),
            date(2026, 1, 1),
        ),
        # ── IEEPA reciprocal — an inferred date, on purpose ───────────────
        # So the resolver has something to mark untrustworthy and `asof` has
        # something to warn about.
        _make(
            "FR-2026-IEEPA-RECIP",
            Issuer.USTR,
            InstrumentKind.TARIFF,
            Programme.IEEPA,
            "IEEPA reciprocal tariff",
            Scope(origin_countries=["BR", "IN", "VN", "TR"]),
            Effect(kind=EffectKind.AD_VALOREM, rate_percent=10.0, additive=True, note=NOTE),
            date(2026, 4, 15),
            basis=DateBasis.INFERRED_FROM_PUBLICATION,
        ),
        # ── USITC base rates — what the overlays stack on ─────────────────
        _make(
            "HTS-73269086",
            Issuer.USITC,
            InstrumentKind.BASE_RATE,
            Programme.MFN,
            "Other articles of iron or steel",
            Scope(hts_prefixes=["73269086"]),
            Effect(kind=EffectKind.AD_VALOREM, rate_percent=2.9, additive=False, note=NOTE),
            date(2026, 1, 1),
        ),
        _make(
            "HTS-85076000",
            Issuer.USITC,
            InstrumentKind.BASE_RATE,
            Programme.MFN,
            "Lithium-ion accumulators",
            Scope(hts_prefixes=["85076000"]),
            Effect(kind=EffectKind.AD_VALOREM, rate_percent=3.4, additive=False, note=NOTE),
            date(2026, 1, 1),
        ),
        # ── CBAM ──────────────────────────────────────────────────────────
        _make(
            "CBAM-iron_and_steel",
            Issuer.EU_COMMISSION,
            InstrumentKind.GOODS_SCOPE,
            Programme.CBAM,
            "CBAM Annex I — iron and steel",
            Scope(hts_prefixes=["72", "7301", "7302"]),
            Effect(
                kind=EffectKind.CERTIFICATE_REQUIRED,
                additive=False,
                note=f"{NOTE} €100/tonne CO2e penalty for uncovered emissions.",
            ),
            date(2026, 1, 1),
        ),
        # ── UFLPA listings ────────────────────────────────────────────────
        *[
            _make(
                f"UFLPA-{hash_text(name)[:12]}",
                Issuer.DHS,
                InstrumentKind.ENTITY_LISTING,
                Programme.UFLPA,
                name,
                Scope(entity_names=[name]),
                Effect(kind=EffectKind.PROHIBITION, additive=False, note=NOTE),
                listed,
            )
            for name, listed in [
                ("Sunrise Textile Manufacturing Co., Ltd.", date(2026, 3, 12)),
                ("Northern Silica Materials Group", date(2026, 8, 3)),
                ("Hongyuan Photovoltaic Industries", date(2026, 8, 3)),
            ]
        ],
    ]

    if with_contradiction:
        # Same lane, overlapping scope and window, different rate, no
        # supersession — the resolver must flag both and pick neither.
        out.append(
            _make(
                "FR-2026-06-232-STEEL-ALT",
                Issuer.USTR,
                InstrumentKind.TARIFF,
                Programme.SECTION_232,
                "Section 232 — steel (conflicting parse, deliberately injected)",
                Scope(hts_prefixes=steel_alu),
                Effect(kind=EffectKind.AD_VALOREM, rate_percent=35.0, additive=True, note=NOTE),
                date(2026, 6, 8),
                effective_to=date(2027, 12, 31),
            )
        )

    return out
