"""The `compute` route — a path with no LLM in it at all.

The other three paths retrieve, then ask a model to write an answer. This one
computes a table and renders it. Zero tokens in the answer path; the classifier
call ahead of it is the only model invocation, and Phase F's benchmark should
show it as simultaneously the cheapest and the most correct route in the system.

That is the interesting result. The existing Phase F finding is that cheaper
routing costs quality — the classifier under-routes and returns thin answers.
For this one query class the relationship inverts: arithmetic done by a
calculator is both free and exact, where the same question handed to a language
model produces confident, plausible, wrong numbers.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from marsa.config import settings
from marsa.duty.detect import detect
from marsa.duty.engine import DutyQuote, quote
from marsa.logging import get_logger
from marsa.regulatory.store import InstrumentStore
from marsa.router.state import RetrievalStep, Source, Stopwatch

log = get_logger(__name__)

#: Assumed when the query names no value. The percentages are the answer; the
#: cash figure is illustrative and the answer says so.
DEFAULT_CUSTOMS_VALUE = Decimal("10000")


def _store() -> InstrumentStore:
    return InstrumentStore.load(settings.data_dir / "regulatory" / "instruments.json")


def _origin(query: str) -> str | None:
    """Pull an origin out of the query text.

    Deliberately small and explicit. A fuzzy country matcher here would let
    'China' in 'China Southern Airlines' set the origin, and an origin is worth
    tens of percentage points.
    """
    table = {
        "china": "CN", "vietnam": "VN", "mexico": "MX", "canada": "CA",
        "japan": "JP", "korea": "KR", "germany": "DE", "india": "IN",
        "brazil": "BR", "turkey": "TR", "taiwan": "TW", "switzerland": "CH",
        "united kingdom": "GB", "uk": "GB",
    }
    lowered = query.lower()
    for name, code in table.items():
        if name in lowered:
            return code
    return None


def render(result: DutyQuote) -> str:
    """The table, as prose a broker could check line by line."""
    if not result.answered:
        lines = [
            f"No duty figure for HTS {result.hts} from {result.origin} on "
            f"{result.on.isoformat()}. The instrument set cannot support an exact "
            "answer, and an approximate one would understate duty:",
            "",
        ]
        lines += [f"- {reason}" for reason in result.refusals]
        lines += [
            "",
            "This is a refusal, not a zero. Resolve the above and the same query "
            "will price exactly.",
        ]
        return "\n".join(lines)

    lines = [
        f"Duty on HTS {result.hts} from {result.origin}, entered "
        f"{result.on.isoformat()}, customs value "
        f"${result.customs_value:,.2f}:",
        "",
    ]
    for layer in result.layers:
        lines.append(
            f"  {layer.programme.value:<14} {layer.rate_percent:>6}%  "
            f"${layer.amount:>12,.2f}   [{layer.instrument_id}]"
        )
    for adjustment in result.adjustments:
        lines.append(
            f"  → {adjustment.description} "
            f"({adjustment.before_percent}% → {adjustment.after_percent}%)"
        )
    lines += [
        "",
        f"  TOTAL          {result.total_percent:>6}%  "
        f"${result.total_amount:>12,.2f}",
    ]
    if result.warnings:
        lines += [""] + [f"! {w}" for w in result.warnings]
    lines += [
        "",
        "Computed arithmetically from the cited instruments. No language model "
        "was involved in any figure above.",
    ]
    return "\n".join(lines)


def run_compute_path(
    query: str, *, on: date | None = None, customs_value: Decimal | None = None
) -> dict[str, Any]:
    """Route a duty question to the calculator."""
    watch = Stopwatch()
    steps: list[RetrievalStep] = []

    found = detect(query)
    steps.append(
        RetrievalStep(
            "Parse", f"HTS {found.hts or 'not found'}, origin {_origin(query) or 'not found'}",
            watch.lap(),
        )
    )

    store = _store()
    if not store.instruments:
        return {
            "steps": steps
            + [RetrievalStep("No instruments", "run `marsa-reg fixtures`", watch.lap())],
            "sources": [],
            "answer": (
                "No regulatory instruments are loaded, so nothing can be priced. "
                "Run `marsa-reg fixtures` or `marsa-reg ingest` first."
            ),
            "warnings": ["regulatory_store_empty"],
        }

    origin = _origin(query)
    if not found.hts or not origin:
        missing = [n for n, v in (("HTS code", found.hts), ("origin country", origin)) if not v]
        return {
            "steps": steps,
            "sources": [],
            "answer": (
                f"A duty calculation needs an HTS code and an origin country; this "
                f"query gives no {' and no '.join(missing)}. Rephrase with both, "
                "e.g. 'duty on 7326.90.86 from China'."
            ),
            "warnings": ["insufficient_query"],
        }

    result = quote(
        store,
        hts=found.hts,
        origin=origin,
        customs_value=customs_value or DEFAULT_CUSTOMS_VALUE,
        on=on or date.today(),
    )
    steps.append(
        RetrievalStep(
            "Resolve instruments",
            f"{len(result.layers)} layer(s) in force" if result.answered
            else f"{len(result.refusals)} blocker(s)",
            watch.lap(),
        )
    )
    steps.append(RetrievalStep("Compute", "deterministic — no model call", watch.lap()))

    sources = [
        Source(ref=layer.instrument_id, kind="instrument", detail=layer.instrument_title)
        for layer in result.layers
    ]

    warnings = list(result.warnings)
    if not result.answered:
        warnings.append("duty_refused")
    if store.is_stale:
        warnings.append("instrument_set_stale")
    if store.any_synthetic:
        warnings.append("synthetic_instruments")

    log.info(
        "compute path",
        extra={"hts": found.hts, "origin": origin, "answered": result.answered},
    )
    return {
        "steps": steps,
        "sources": sources,
        "answer": render(result),
        "warnings": warnings,
        "quote": result.as_dict(),
    }
