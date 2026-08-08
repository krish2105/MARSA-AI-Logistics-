"""The four source ingestors.

**These are written against evidence, not against a response.** None of the
four sources is reachable from this build environment, so the field names below
come from published API documentation and from `marsa-reg probe`'s design —
not from a payload anyone here has seen.

That is exactly the bet the CROSS client lost in Phase A, where a guessed key
spelling produced an empty corpus that looked like a working one. So every
extraction goes through `pick()`, which accepts several spellings and records
which one it found, and a source that yields nothing raises rather than
returning `[]`. Run `marsa-reg probe` first; it exists to tell you which
spelling is real before a long ingest depends on it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from marsa.ingestion.fetcher import Fetcher, UpstreamShapeError
from marsa.ingestion.schemas import Origin
from marsa.logging import get_logger
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

log = get_logger(__name__)


def pick(record: dict[str, Any], *candidates: str) -> Any:
    """First non-empty value among several possible key spellings."""
    for key in candidates:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(value.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def _effective(record: dict[str, Any]) -> tuple[date | None, DateBasis]:
    """Effective date, and honestly where it came from.

    Publication is not commencement — the gap is routinely weeks — so falling
    back to it is recorded as inferred rather than passed off as stated.
    """
    explicit = _parse_date(pick(record, "effective_on", "effectiveDate", "effective_date"))
    if explicit:
        return explicit, DateBasis.EXPLICIT
    published = _parse_date(pick(record, "publication_date", "publicationDate", "signing_date"))
    if published:
        return published, DateBasis.INFERRED_FROM_PUBLICATION
    return None, DateBasis.UNKNOWN


# ─────────────────────────────────────────────────────────────────────────────
# Federal Register — Section 232 / 301 / IEEPA proclamations
# ─────────────────────────────────────────────────────────────────────────────

FEDERAL_REGISTER_URL = "https://www.federalregister.gov/api/v1/documents.json"

FR_FIELDS = [
    "document_number", "title", "publication_date", "effective_on", "type",
    "html_url", "citation", "correction_of", "full_text_xml_url", "agencies",
]


def ingest_federal_register(
    fetcher: Fetcher, *, term: str = "Section 232 aluminum steel copper", limit: int = 50
) -> list[Instrument]:
    payload = fetcher.get_json(
        FEDERAL_REGISTER_URL,
        params={
            "per_page": min(limit, 100),
            "order": "newest",
            "conditions[term]": term,
            "fields[]": FR_FIELDS,
        },
    )
    results = payload.get("results") or []
    if not results:
        raise UpstreamShapeError(
            f"Federal Register returned no results for {term!r}. Either the term "
            "matched nothing or the response shape changed — run `marsa-reg probe`."
        )

    out: list[Instrument] = []
    for row in results:
        identifier = pick(row, "document_number", "citation")
        effective_from, basis = _effective(row)
        if not identifier or effective_from is None:
            # An instrument with no date cannot answer a point-in-time query,
            # which is the only question Phase G exists for. Skip loudly.
            log.warning("undated instrument skipped", extra={"id": str(identifier)})
            continue

        superseded = pick(row, "correction_of")
        out.append(
            Instrument(
                id=f"FR-{identifier}",
                issuer=Issuer.USTR,
                kind=InstrumentKind.TARIFF,
                # The programme is in the document text; leaving it UNKNOWN means
                # the resolver will not claim a conflict it cannot substantiate.
                programme=Programme.UNKNOWN,
                title=str(pick(row, "title") or "")[:300],
                # Scope lives in the document body, not the metadata. Leaving it
                # unrestricted would make every proclamation apply to every
                # entry, so it stays empty and Phase H must not treat an empty
                # scope here as "applies to all" without an extraction step.
                scope=Scope(),
                effect=Effect(
                    kind=EffectKind.AD_VALOREM,
                    rate_percent=0.0,
                    additive=True,
                    note=(
                        "Rate and HTS scope require extraction from the document "
                        "text; metadata alone does not carry them."
                    ),
                ),
                effective_from=effective_from,
                date_basis=basis,
                supersedes=[f"FR-{superseded}"] if superseded else [],
                source_url=str(pick(row, "html_url") or ""),
                retrieved_at=datetime.now(UTC),
                text_hash=hash_text(str(row)),
                origin=Origin.LIVE,
            )
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# USITC HTS — MFN base rates
# ─────────────────────────────────────────────────────────────────────────────

USITC_URL = "https://hts.usitc.gov/reststop/exportList"


def ingest_usitc_hts(
    fetcher: Fetcher, *, hts_from: str = "8507.60.00", hts_to: str = "8507.60.99"
) -> list[Instrument]:
    payload = fetcher.get_json(
        USITC_URL, params={"from": hts_from, "to": hts_to, "format": "JSON"}
    )
    rows = payload if isinstance(payload, list) else payload.get("results", [])
    if not rows:
        raise UpstreamShapeError(
            f"USITC returned no lines for {hts_from}–{hts_to} — run `marsa-reg probe`."
        )

    # The HTS is published as dated revisions, not per-line effective dates, so
    # every line inherits the revision's date. Where the payload carries none,
    # the whole batch is marked inferred rather than invented per row.
    revision = _parse_date(pick(rows[0], "effectiveDate", "revision")) or date.today()
    basis = (
        DateBasis.EXPLICIT
        if pick(rows[0], "effectiveDate", "revision")
        else DateBasis.INFERRED_FROM_PUBLICATION
    )

    out: list[Instrument] = []
    for row in rows:
        code = pick(row, "htsno", "hts8", "htsNumber")
        if not code:
            continue
        out.append(
            Instrument(
                id=f"HTS-{str(code).replace('.', '')}",
                issuer=Issuer.USITC,
                kind=InstrumentKind.BASE_RATE,
                programme=Programme.MFN,
                title=str(pick(row, "description") or "")[:300],
                scope=Scope(hts_prefixes=[str(code)]),
                effect=Effect(
                    kind=EffectKind.AD_VALOREM,
                    rate_percent=_rate_percent(pick(row, "general", "generalRate")),
                    # The base rate is what overlays stack *on*, not another layer.
                    additive=False,
                ),
                effective_from=revision,
                date_basis=basis,
                source_url="https://hts.usitc.gov/",
                retrieved_at=datetime.now(UTC),
                text_hash=hash_text(str(row)),
                origin=Origin.LIVE,
            )
        )
    return out


def _rate_percent(raw: Any) -> float:
    """'2.5%' / 'Free' / 2.5 → a number. Unparseable becomes 0.0, not None.

    `Free` is genuinely 0%, and it is the single most common value in the
    schedule, so treating it as missing would blank most of the corpus.
    """
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().lower()
    if text in ("free", "", "0"):
        return 0.0
    digits = "".join(c for c in text if c.isdigit() or c == ".")
    try:
        return float(digits) if digits else 0.0
    except ValueError:
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# DHS UFLPA Entity List
# ─────────────────────────────────────────────────────────────────────────────

DHS_UFLPA_URL = "https://www.dhs.gov/uflpa-entity-list"


def ingest_dhs_uflpa(fetcher: Fetcher, *, listed_on: date | None = None) -> list[Instrument]:
    """Parse the UFLPA Entity List.

    Published as an HTML table rather than an API, so this is a parser and
    parsers of other people's markup break. It raises on an unexpected shape
    instead of returning an empty list, because an empty screening list reads
    as "no matches" — the most dangerous possible output for this wedge.
    """
    from selectolax.parser import HTMLParser

    html = fetcher.get_text(DHS_UFLPA_URL)
    tree = HTMLParser(html)
    rows = tree.css("table tbody tr") or tree.css("table tr")
    if not rows:
        raise UpstreamShapeError(
            "No table found on the UFLPA Entity List page. The markup changed; "
            "returning an empty screening list would read as 'no matches'."
        )

    effective_from = listed_on or date.today()
    out: list[Instrument] = []
    for row in rows:
        cells = [c.text(strip=True) for c in row.css("td")]
        if not cells or not cells[0]:
            continue
        name = cells[0]
        out.append(
            Instrument(
                id=f"UFLPA-{hash_text(name)[:12]}",
                issuer=Issuer.DHS,
                kind=InstrumentKind.ENTITY_LISTING,
                programme=Programme.UFLPA,
                title=name[:300],
                scope=Scope(entity_names=[name]),
                effect=Effect(
                    kind=EffectKind.PROHIBITION,
                    additive=False,
                    note=(
                        "Rebuttable presumption of forced labour. Goods are "
                        "detained unless the importer shows clear and convincing "
                        "evidence within 30 days."
                    ),
                ),
                effective_from=effective_from,
                # The listing date is carried by the Federal Register notice, not
                # the table, so unless one is supplied this is inferred.
                date_basis=DateBasis.EXPLICIT if listed_on else DateBasis.INFERRED_FROM_PUBLICATION,
                source_url=DHS_UFLPA_URL,
                retrieved_at=datetime.now(UTC),
                text_hash=hash_text(name),
                origin=Origin.LIVE,
            )
        )
    if not out:
        raise UpstreamShapeError("UFLPA table parsed but yielded no entities.")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# EU CBAM Annex I goods scope
# ─────────────────────────────────────────────────────────────────────────────

CBAM_URL = "https://taxation-customs.ec.europa.eu/carbon-border-adjustment-mechanism_en"

#: CBAM's definitive period opened on 1 January 2026 — a fixed statutory date,
#: not something to scrape.
CBAM_DEFINITIVE_FROM = date(2026, 1, 1)


def ingest_eu_cbam(fetcher: Fetcher) -> list[Instrument]:
    """CBAM Annex I sector scope.

    The landing page is prose; the authoritative CN-code list is in the
    Regulation's Annex I on EUR-Lex. This models the six sectors at chapter
    level, which is correct but coarse — a real implementation must parse
    Annex I. Marked in the note rather than left to be discovered.
    """
    html = fetcher.get_text(CBAM_URL)

    sectors = {
        "cement": ["2523"],
        "iron_and_steel": ["72", "7301", "7302", "7303", "7304"],
        "aluminium": ["76"],
        "fertilisers": ["3102", "3105", "2808"],
        "electricity": ["2716"],
        "hydrogen": ["280410"],
    }
    return [
        Instrument(
            id=f"CBAM-{sector}",
            issuer=Issuer.EU_COMMISSION,
            kind=InstrumentKind.GOODS_SCOPE,
            programme=Programme.CBAM,
            title=f"CBAM Annex I — {sector.replace('_', ' ')}",
            scope=Scope(hts_prefixes=prefixes),
            effect=Effect(
                kind=EffectKind.CERTIFICATE_REQUIRED,
                additive=False,
                note=(
                    "Sector modelled at chapter level. The authoritative CN-code "
                    "list is Annex I of Regulation (EU) 2023/956 on EUR-Lex; this "
                    "is coarser and will over-match."
                ),
            ),
            effective_from=CBAM_DEFINITIVE_FROM,
            date_basis=DateBasis.EXPLICIT,
            source_url=CBAM_URL,
            retrieved_at=datetime.now(UTC),
            text_hash=hash_text(html[:20000]),
            origin=Origin.LIVE,
        )
        for sector, prefixes in sectors.items()
    ]


INGESTORS: dict[str, Callable[[Fetcher], list[Instrument]]] = {
    "federal_register": ingest_federal_register,
    "usitc": ingest_usitc_hts,
    "dhs_uflpa": ingest_dhs_uflpa,
    "eu_cbam": ingest_eu_cbam,
}
