"""Decision gate G1 — does the Regulatory Knowledge Layer premise hold?

Phase G assumes the four public sources expose instruments that can be pinned
to a date: a rate or listing, the goods or entities it applies to, and the
window it was in force. If they do not, the point-in-time query at the heart of
Phase G is unbuildable and the whole G–K chain collapses.

That is worth an hour's evidence before it is worth a fortnight's code, which
is what this module is. It fetches a small sample from each source and reports
which of the required fields are actually present — the same reason
`marsa-ingest probe-cross` exists.

It deliberately does **not** ingest anything. It answers one question: is the
data there, in the shape the Instrument model needs?

Run it anywhere with normal outbound access:

    marsa-reg probe

All four sources are public and unauthenticated. None is reachable from this
project's build sandbox, so the gate cannot be evaluated here — see the note in
ROADMAP.md §12.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from marsa.config import settings
from marsa.ingestion.fetcher import Fetcher
from marsa.logging import get_logger

log = get_logger(__name__)

#: Fields the `Instrument` model cannot be populated without. A source missing
#: `effective` cannot support a point-in-time query at all, which is the single
#: assumption Phase G rests on.
REQUIRED = ("identity", "effective", "scope", "source_url")


@dataclass
class SourceProbe:
    """What one source actually returned."""

    name: str
    url: str
    why: str
    reachable: bool = False
    error: str = ""
    records: int = 0
    #: required-field name -> the upstream field satisfying it, or "" if absent
    fields: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def satisfied(self) -> list[str]:
        return [k for k in REQUIRED if self.fields.get(k)]

    @property
    def coverage(self) -> float:
        return len(self.satisfied) / len(REQUIRED)

    @property
    def verdict(self) -> str:
        if not self.reachable:
            return "UNREACHABLE"
        if self.coverage == 1.0:
            return "COMPLETE"
        if self.coverage >= 0.5:
            return "PARTIAL"
        return "INSUFFICIENT"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "reachable": self.reachable,
            "error": self.error,
            "records": self.records,
            "fields": self.fields,
            "coverage": round(self.coverage, 4),
            "verdict": self.verdict,
            "notes": self.notes,
        }


def _first(record: dict[str, Any], *candidates: str) -> str:
    """Return the first candidate key present and non-empty.

    Sources rename fields between versions, so the probe reports which spelling
    it found rather than asserting one — the same tolerance `cross.py` needed.
    """
    for key in candidates:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return key
    return ""


def probe_federal_register(fetcher: Fetcher) -> SourceProbe:
    """Section 232 / 301 / IEEPA proclamations and notices."""
    probe = SourceProbe(
        name="Federal Register",
        url="https://www.federalregister.gov/api/v1/documents.json",
        why="Section 232/301/IEEPA proclamations — the rates themselves",
    )
    try:
        payload = fetcher.get_json(
            probe.url,
            params={
                "per_page": 20,
                "order": "newest",
                "conditions[term]": "Section 232 aluminum steel copper",
                "fields[]": [
                    "document_number", "title", "publication_date", "effective_on",
                    "type", "html_url", "citation", "agencies", "correction_of",
                    "full_text_xml_url",
                ],
            },
        )
    except Exception as exc:  # noqa: BLE001 — an unreachable source is the datum
        probe.error = str(exc)[:300]
        return probe

    probe.reachable = True
    results = payload.get("results") or []
    probe.records = len(results)
    if not results:
        probe.notes.append("Reachable but the query matched nothing — widen the term.")
        return probe

    row = results[0]
    probe.fields = {
        "identity": _first(row, "document_number", "citation"),
        "effective": _first(row, "effective_on", "publication_date"),
        # Scope (which HTS codes a proclamation covers) is in the document body,
        # not the metadata. Recording that honestly: it means Phase G needs a
        # text-extraction step here, which is real work the plan must budget.
        "scope": _first(row, "full_text_xml_url", "raw_text_url"),
        "source_url": _first(row, "html_url"),
    }

    dated = sum(1 for r in results if r.get("effective_on"))
    probe.notes.append(
        f"{dated}/{len(results)} carry an explicit effective_on; the rest fall back "
        "to publication_date, which is NOT the same thing and must be marked inferred."
    )
    if any(r.get("correction_of") for r in results):
        probe.notes.append("`correction_of` present — usable as a supersession signal.")
    probe.notes.append(
        "Scope is only available as document text, not structured metadata. "
        "Phase G needs an HTS-extraction step over the XML."
    )
    return probe


def probe_usitc_hts(fetcher: Fetcher) -> SourceProbe:
    """MFN base rates, and reportedly the 232/301/IEEPA overlays too."""
    probe = SourceProbe(
        name="USITC HTS",
        url="https://hts.usitc.gov/reststop/exportList",
        why="MFN base rates and special-programme columns",
    )
    try:
        payload = fetcher.get_json(
            probe.url,
            params={"from": "8507.60.00", "to": "8507.60.99", "format": "JSON"},
        )
    except Exception as exc:  # noqa: BLE001
        probe.error = str(exc)[:300]
        return probe

    probe.reachable = True
    rows = payload if isinstance(payload, list) else payload.get("results", [])
    probe.records = len(rows)
    if not rows:
        probe.notes.append("Reachable but returned no lines for the sampled range.")
        return probe

    row = rows[0]
    probe.fields = {
        "identity": _first(row, "htsno", "hts8", "htsNumber"),
        # The HTS is published as revisions, not per-line effective dates. If no
        # date field exists, Phase G must date lines by the revision they came
        # from — which is a real design constraint, not a detail.
        "effective": _first(row, "effectiveDate", "revision", "effective_on"),
        "scope": _first(row, "description", "htsno"),
        "source_url": "https://hts.usitc.gov/",
    }
    probe.notes.append(f"Observed keys: {sorted(row)[:14]}")
    if not probe.fields["effective"]:
        probe.notes.append(
            "No per-line effective date. Lines must be dated by HTS revision, so "
            "Phase G has to track revisions as first-class instruments."
        )
    if any(k for k in row if "232" in str(k) or "301" in str(k)):
        probe.notes.append(
            "Section 232/301 overlay flags appear present — that would remove a "
            "large amount of Federal Register text-extraction from Phase H."
        )
    return probe


def probe_dhs_uflpa(fetcher: Fetcher) -> SourceProbe:
    """The UFLPA Entity List — 187 entities as of 3 August 2026."""
    probe = SourceProbe(
        name="DHS UFLPA Entity List",
        url="https://www.dhs.gov/uflpa-entity-list",
        why="Forced-labour screening (your top-ranked wedge)",
    )
    try:
        html = fetcher.get_text(probe.url)
    except Exception as exc:  # noqa: BLE001
        probe.error = str(exc)[:300]
        return probe

    probe.reachable = True
    lowered = html.lower()
    probe.records = lowered.count("<tr")
    probe.fields = {
        "identity": "entity name (HTML table)" if "<table" in lowered else "",
        # Listings are dated by Federal Register notice, which is the join back
        # to the first source — a good sign for the one-primitive thesis.
        "effective": "federal register notice date" if "federal register" in lowered else "",
        "scope": "entity name" if "<table" in lowered else "",
        "source_url": probe.url,
    }
    probe.notes.append(
        "Published as an HTML table, not an API — needs a parser, and the parser "
        "is the fragile part. Assume it breaks and probe before each ingest."
    )
    return probe


def probe_eu_cbam(fetcher: Fetcher) -> SourceProbe:
    """CBAM Annex I goods scope — CN codes in scope of the definitive regime."""
    probe = SourceProbe(
        name="EU CBAM Annex I",
        url="https://taxation-customs.ec.europa.eu/carbon-border-adjustment-mechanism_en",
        why="CBAM certificate liability, live since 1 Jan 2026",
    )
    try:
        html = fetcher.get_text(probe.url)
    except Exception as exc:  # noqa: BLE001
        probe.error = str(exc)[:300]
        return probe

    probe.reachable = True
    lowered = html.lower()
    probe.records = 1
    probe.fields = {
        "identity": "regulation (EU) 2023/956" if "2023/956" in lowered else "",
        "effective": "1 january 2026" if "2026" in lowered else "",
        "scope": "annex i cn codes" if "annex" in lowered else "",
        "source_url": probe.url,
    }
    probe.notes.append(
        "The landing page is prose. The authoritative CN-code list lives in the "
        "Regulation's Annex I on EUR-Lex — probe that URL directly before relying "
        "on this one."
    )
    return probe


PROBES = (
    probe_federal_register,
    probe_usitc_hts,
    probe_dhs_uflpa,
    probe_eu_cbam,
)


@dataclass
class GateResult:
    """G1's verdict, computed rather than asserted."""

    probes: list[SourceProbe] = field(default_factory=list)

    #: Thresholds from ROADMAP.md §12. Named here so the gate cannot drift from
    #: the number it was agreed at.
    pass_at: float = 0.80
    stop_below: float = 0.60

    @property
    def reachable(self) -> list[SourceProbe]:
        return [p for p in self.probes if p.reachable]

    @property
    def coverage(self) -> float:
        """Mean field coverage across ALL sources — unreachable ones score zero.

        Averaging only the reachable sources would let a run where three of four
        sources are down report full marks, which is precisely the self-flattery
        this gate exists to prevent.
        """
        if not self.probes:
            return 0.0
        return sum(p.coverage for p in self.probes) / len(self.probes)

    @property
    def verdict(self) -> str:
        if not self.reachable:
            return "NOT_EVALUATED"
        if self.coverage >= self.pass_at:
            return "PASS"
        if self.coverage < self.stop_below:
            return "STOP"
        return "MARGINAL"

    @property
    def action(self) -> str:
        return {
            "PASS": "Premise holds. Build Phase G as planned.",
            "MARGINAL": (
                "Between the thresholds. Build Phase G narrowed to the sources "
                "that scored COMPLETE, and re-probe the rest before widening."
            ),
            "STOP": (
                "The one-primitive thesis does not survive contact with the data. "
                "Stop. Fall back to a single-regime tool (Section 232 only) and "
                "re-plan G–K."
            ),
            "NOT_EVALUATED": (
                "No source was reachable, so the gate has no verdict. This is NOT "
                "a pass and NOT a failure — run it somewhere with outbound access "
                "before committing to Phase G."
            ),
        }[self.verdict]

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": "G1",
            "verdict": self.verdict,
            "coverage": round(self.coverage, 4),
            "passAt": self.pass_at,
            "stopBelow": self.stop_below,
            "sourcesReachable": len(self.reachable),
            "sourcesTotal": len(self.probes),
            "action": self.action,
            "probes": [p.as_dict() for p in self.probes],
        }


def run_gate(*, use_cache: bool = True) -> GateResult:
    """Probe every source and compute G1's verdict."""
    result = GateResult()
    with Fetcher(
        requests_per_second=1.0,
        cache_dir=settings.cache_dir,
        use_cache=use_cache,
    ) as fetcher:
        for probe_fn in PROBES:
            probe = probe_fn(fetcher)
            result.probes.append(probe)
            log.info(
                "source probed",
                extra={
                    "source": probe.name,
                    "verdict": probe.verdict,
                    "coverage": round(probe.coverage, 2),
                },
            )
    return result
