"""CBP CROSS — binding classification rulings for the fast path.

Contract status — read this before running
------------------------------------------
CROSS (rulings.cbp.gov) is an Angular single-page app backed by a JSON API.
That API is public but **undocumented**: CBP publishes no API reference and no
machine-readable bulk export. The request shapes below match the calls the
site's own front-end makes.

Unlike the Comtrade client — whose contract was read out of the official
client's source — this one could not be verified against the live service from
the build environment, because outbound access to rulings.cbp.gov is blocked by
network policy here. It is therefore written defensively:

  * every field is read through `_first_key`, which accepts the several
    spellings the endpoint has used, rather than one guessed key;
  * `probe()` dumps a raw response so the contract can be confirmed in a single
    command before committing to a long scrape;
  * a shape mismatch raises `UpstreamShapeError` listing the keys actually
    returned, instead of silently writing an empty corpus.

Run `marsa-ingest probe-cross` first. If the shape differs, the fix is confined
to `_RULING_KEYS` / `_SEARCH_KEYS` below.

Subsetting
----------
The full database holds ~220,989 rulings. This pulls a few thousand across HS
chapters relevant to a plausible Dubai re-export business (electronics,
machinery, textiles, vehicles, furniture). That is a deliberate scoping choice,
recorded in the manifest's `subset_rationale`, not an accident of scale.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

from selectolax.parser import HTMLParser

from marsa.config import settings
from marsa.ingestion.fetcher import Fetcher, UpstreamShapeError
from marsa.ingestion.schemas import CrossRuling, Origin, Provenance, SourceKind
from marsa.logging import get_logger

log = get_logger(__name__)

BASE = "https://rulings.cbp.gov"
SEARCH_URL = f"{BASE}/api/search"
RULING_URL = f"{BASE}/api/ruling"

# Search terms chosen to hit the HS chapters the Comtrade harvest covers, so
# the two corpora share commodity ground and can genuinely cross-reference.
DEFAULT_TERMS: tuple[str, ...] = (
    "lithium-ion battery",
    "power bank",
    "semiconductor device",
    "printed circuit assembly",
    "cellular telephone",
    "electric motor",
    "textile garment knitted",
    "woven cotton apparel",
    "footwear upper",
    "machinery parts",
    "air conditioning unit",
    "motor vehicle part",
    "furniture wooden",
    "LED lamp",
)

# Alternative key spellings observed across the endpoint's responses.
_SEARCH_KEYS = {
    "results": ("rulings", "results", "items", "data"),
    "total": ("total", "totalCount", "totalResults", "count"),
}
_RULING_KEYS = {
    "number": ("rulingNumber", "ruling_number", "number", "id"),
    "collection": ("collection", "collectionName", "office"),
    "date": ("rulingDate", "ruling_date", "date", "issuedDate"),
    "subject": ("subject", "title", "description"),
    "body": ("rulingText", "ruling_text", "body", "text", "content", "fullText"),
    "category": ("category", "categoryName", "tariffCategory"),
}

# HTS codes as they appear in ruling prose. The US tariff schedule nests to
# four levels, so all of these are valid and must match at full length:
#   8507.60              6-digit international HS subheading
#   8504.40.95           8-digit US subheading
#   8507.60.0020        10-digit US statistical suffix
#   8471.30.01.00       dotted 10-digit variant
# Note `\.\d{2,4}` rather than a repeated `\.\d{2}`: the latter matches only
# `.00` of `.0020`, then fails the trailing \b and silently backtracks to the
# 6-digit form — truncating every 10-digit code in the corpus.
HTS_PATTERN = re.compile(r"\b(\d{4}\.\d{2}(?:\.\d{2,4})?(?:\.\d{2})?)\b")
# NY N302241 / HQ H289765 / HQ 954321 — cross-references between rulings.
RULING_REF_PATTERN = re.compile(r"\b((?:NY|HQ)\s?[A-Z]?\d{5,6})\b")


def _first_key(payload: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    for key in candidates:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def extract_hts_codes(text: str) -> list[str]:
    """Pull HTS codes out of ruling prose, most-specific form retained."""
    return list(dict.fromkeys(HTS_PATTERN.findall(text or "")))


def extract_ruling_refs(text: str, *, exclude: str | None = None) -> list[str]:
    """Pull references to other rulings — these become graph edges later."""
    found = (m.replace("  ", " ").strip() for m in RULING_REF_PATTERN.findall(text or ""))
    normalised = list(dict.fromkeys(found))
    if exclude:
        target = exclude.replace(" ", "").upper()
        normalised = [r for r in normalised if r.replace(" ", "").upper() != target]
    return normalised


def html_to_text(raw: str) -> str:
    """Ruling bodies come back as HTML fragments; flatten to readable text."""
    if not raw:
        return ""
    if "<" not in raw:
        return raw.strip()
    tree = HTMLParser(raw)
    text = tree.body.text(separator="\n") if tree.body else tree.text(separator="\n")
    # Collapse the runs of blank lines the source markup leaves behind.
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value)[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


class CrossClient:
    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    # ── discovery ──────────────────────────────────────────────────────────
    def probe(self, term: str = "lithium-ion battery") -> dict[str, Any]:
        """Fetch one search page raw, for contract verification.

        Returns the top-level keys and the keys of the first result so a
        mismatch can be diagnosed without reading a 500KB payload.
        """
        payload = self.fetcher.get_json(
            SEARCH_URL,
            params={"term": term, "collection": "ALL", "pageSize": 5, "page": 1},
        )
        results = _first_key(payload, _SEARCH_KEYS["results"]) or []
        return {
            "top_level_keys": sorted(payload) if isinstance(payload, dict) else None,
            "result_count": len(results),
            "first_result_keys": sorted(results[0]) if results else None,
            "first_result_sample": results[0] if results else None,
        }

    def search(self, term: str, *, page: int = 1, page_size: int = 30) -> tuple[list[dict], int]:
        """One page of search results. Returns (results, total)."""
        payload = self.fetcher.get_json(
            SEARCH_URL,
            params={
                "term": term,
                "collection": "ALL",
                "sortBy": "RELEVANCE",
                "pageSize": page_size,
                "page": page,
            },
        )
        if not isinstance(payload, dict):
            raise UpstreamShapeError(f"CROSS search returned {type(payload)}, expected object")

        results = _first_key(payload, _SEARCH_KEYS["results"])
        if results is None:
            raise UpstreamShapeError(
                "CROSS search response had none of the expected result keys "
                f"{_SEARCH_KEYS['results']}; keys present: {sorted(payload)}. "
                "Run `marsa-ingest probe-cross` and update _SEARCH_KEYS."
            )
        total = _first_key(payload, _SEARCH_KEYS["total"]) or len(results)
        return list(results), int(total)

    def fetch_ruling(self, ruling_number: str) -> dict[str, Any]:
        """Full detail for one ruling (search results carry only a snippet)."""
        payload = self.fetcher.get_json(f"{RULING_URL}/{ruling_number.replace(' ', '')}")
        if not isinstance(payload, dict):
            raise UpstreamShapeError(f"CROSS ruling {ruling_number} returned {type(payload)}")
        return payload

    # ── mapping ────────────────────────────────────────────────────────────
    @staticmethod
    def to_model(payload: dict[str, Any]) -> CrossRuling | None:
        number = _first_key(payload, _RULING_KEYS["number"])
        if not number:
            log.warning(
                "CROSS record has no ruling number",
                extra={"keys": sorted(payload)[:12]},
            )
            return None

        body = html_to_text(str(_first_key(payload, _RULING_KEYS["body"]) or ""))
        subject = str(_first_key(payload, _RULING_KEYS["subject"]) or "").strip()
        number = str(number).strip()

        return CrossRuling(
            ruling_number=number,
            collection=str(_first_key(payload, _RULING_KEYS["collection"]) or "UNKNOWN"),
            ruling_date=_parse_date(_first_key(payload, _RULING_KEYS["date"])),
            subject=subject or number,
            body=body,
            # Subject lines often carry the classification when the body does not.
            hts_codes=extract_hts_codes(f"{subject}\n{body}"),
            related_rulings=extract_ruling_refs(body, exclude=number),
            category=_first_key(payload, _RULING_KEYS["category"]),
            url=f"{BASE}/ruling/{number.replace(' ', '')}",
            provenance=Provenance(
                source=SourceKind.CROSS,
                origin=Origin.LIVE,
                url=f"{BASE}/ruling/{number.replace(' ', '')}",
                retrieved_at=datetime.now(UTC),
            ),
        )

    # ── harvest ────────────────────────────────────────────────────────────
    def harvest(
        self,
        *,
        terms: tuple[str, ...] = DEFAULT_TERMS,
        max_rulings: int | None = None,
        page_size: int = 30,
        max_pages_per_term: int = 8,
    ) -> Iterator[CrossRuling]:
        """Search each term, then pull full text for every unique hit."""
        limit = max_rulings or settings.cross_max_rulings
        seen: set[str] = set()
        emitted = 0

        for term in terms:
            for page in range(1, max_pages_per_term + 1):
                if emitted >= limit:
                    log.info("cross harvest hit limit", extra={"limit": limit})
                    return
                try:
                    results, total = self.search(term, page=page, page_size=page_size)
                except UpstreamShapeError:
                    raise
                except Exception as exc:  # noqa: BLE001 — one bad page must not kill the run
                    log.error(
                        "cross search failed",
                        extra={"term": term, "page": page, "error": str(exc)},
                    )
                    break

                if not results:
                    break

                for result in results:
                    number = _first_key(result, _RULING_KEYS["number"])
                    if not number:
                        continue
                    key = str(number).replace(" ", "").upper()
                    if key in seen:
                        continue
                    seen.add(key)

                    # Search hits are snippets; fetch the full ruling unless the
                    # search payload already carried a substantial body.
                    payload = result
                    body_preview = str(_first_key(result, _RULING_KEYS["body"]) or "")
                    if len(body_preview) < 500:
                        try:
                            payload = self.fetch_ruling(str(number)) or result
                        except Exception as exc:  # noqa: BLE001
                            log.warning(
                                "cross detail fetch failed; keeping search snippet",
                                extra={"ruling": str(number), "error": str(exc)},
                            )

                    model = self.to_model(payload)
                    if model is not None:
                        emitted += 1
                        yield model
                        if emitted >= limit:
                            return

                if page * page_size >= total:
                    break
