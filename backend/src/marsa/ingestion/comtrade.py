"""UN Comtrade — bilateral trade flows for the agentic path.

Contract note
-------------
The endpoint and parameter names below were taken from the source of the
official `comtradeapicall` package (v1.3.2, `PreviewGet.getPreviewData`) rather
than from prose documentation, because the API has a casing quirk that prose
gets wrong: the reporter parameter is **`reportercode`** (lowercase `c`) while
every other parameter is camelCase. Sending `reporterCode` is silently ignored
by the server, which returns data for *all* reporters — a wrong result that
looks like a working one.

Free-tier limits, honestly stated:
  * no subscription key required on `/public/v1/preview/...`
  * a single response is capped at 500 records
  * the tier is rate-limited per IP

This is therefore a *sampled* corpus, not a mirror of Comtrade. The manifest
records exactly which reporter/partner/commodity/period combinations were
requested so the volume claim is auditable.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import product
from typing import Any

from marsa.config import settings
from marsa.ingestion.fetcher import Fetcher, UpstreamShapeError
from marsa.ingestion.schemas import (
    ComtradeFlow,
    Origin,
    Provenance,
    SourceKind,
)
from marsa.logging import get_logger

log = get_logger(__name__)

BASE_URL = "https://comtradeapi.un.org/public/v1/preview"

# UN M49 numeric codes — Comtrade does not accept ISO alpha-3 here.
UAE = 784
REPORTERS: dict[int, str] = {UAE: "United Arab Emirates"}
PARTNERS: dict[int, str] = {
    156: "China",
    699: "India",
    840: "USA",
    276: "Germany",
    392: "Japan",
    410: "Republic of Korea",
    702: "Singapore",
    826: "United Kingdom",
}

# HS chapters chosen to overlap the CROSS subset, so the agentic path can
# actually cross-reference the two corpora rather than joining on nothing.
HS_CHAPTERS: dict[str, str] = {
    "85": "Electrical machinery and equipment",
    "84": "Machinery, mechanical appliances",
    "62": "Apparel and clothing, not knitted",
    "61": "Apparel and clothing, knitted",
    "87": "Vehicles other than railway",
    "94": "Furniture, bedding, lamps",
}

DEFAULT_YEARS = (2021, 2022, 2023)


class ComtradeClient:
    """Client for the free public preview tier."""

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def fetch_flows(
        self,
        *,
        reporter_code: int,
        partner_code: int,
        cmd_code: str,
        period: int | str,
        flow_code: str = "M",
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        """One preview call. Returns the raw `data` array."""
        params = {
            # Lowercase 'c' is deliberate — see the module docstring.
            "reportercode": reporter_code,
            "partnerCode": partner_code,
            "cmdCode": cmd_code,
            "period": period,
            "flowCode": flow_code,
            "maxRecords": max_records or settings.comtrade_max_records,
            "format": "JSON",
            "includeDesc": "TRUE",
            "breakdownMode": "classic",
        }
        url = f"{BASE_URL}/C/A/HS"  # typeCode=C (goods), freqCode=A (annual), clCode=HS
        payload = self.fetcher.get_json(url, params=params)

        if not isinstance(payload, dict):
            raise UpstreamShapeError(f"expected a JSON object from {url}, got {type(payload)}")
        if "data" not in payload:
            # Comtrade puts errors in these keys; surface whichever is present.
            detail = payload.get("error") or payload.get("message") or list(payload)[:8]
            raise UpstreamShapeError(f"no 'data' key in Comtrade response; got {detail!r}")

        data = payload["data"] or []
        if not isinstance(data, list):
            raise UpstreamShapeError(f"'data' was {type(data)}, expected list")
        return data

    @staticmethod
    def to_model(row: dict[str, Any], *, url: str | None = None) -> ComtradeFlow | None:
        """Map one raw row onto the normalised schema.

        Returns None for rows missing the identifying fields rather than
        raising — a single malformed row should not abort a 500-record page.
        """
        try:
            return ComtradeFlow(
                period=str(row.get("period") or row.get("refPeriodId") or ""),
                ref_year=int(row.get("refYear") or str(row.get("period", ""))[:4]),
                reporter_code=int(row["reporterCode"]),
                reporter_iso=row.get("reporterISO"),
                reporter_desc=row.get("reporterDesc"),
                partner_code=int(row["partnerCode"]),
                partner_iso=row.get("partnerISO"),
                partner_desc=row.get("partnerDesc"),
                flow_code=str(row.get("flowCode") or ""),
                flow_desc=row.get("flowDesc"),
                cmd_code=str(row.get("cmdCode") or ""),
                cmd_desc=row.get("cmdDesc"),
                primary_value=_as_float(row.get("primaryValue")),
                net_weight_kg=_as_float(row.get("netWgt")),
                qty=_as_float(row.get("qty")),
                qty_unit=row.get("qtyUnitAbbr"),
                provenance=Provenance(
                    source=SourceKind.COMTRADE,
                    origin=Origin.LIVE,
                    url=url,
                    retrieved_at=datetime.now(UTC),
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("skipping unparseable Comtrade row", extra={"error": str(exc)})
            return None

    def harvest(
        self,
        *,
        reporters: dict[int, str] | None = None,
        partners: dict[int, str] | None = None,
        chapters: dict[str, str] | None = None,
        years: tuple[int, ...] = DEFAULT_YEARS,
        flows: tuple[str, ...] = ("M", "X"),
    ) -> Iterator[ComtradeFlow]:
        """Sweep the configured reporter × partner × chapter × year × flow grid.

        Each combination is one preview request. The grid size is the honest
        answer to "how much data did you pull" and is recorded in the manifest.
        """
        reporters = reporters or REPORTERS
        partners = partners or PARTNERS
        chapters = chapters or HS_CHAPTERS

        combos = list(product(reporters, partners, chapters, years, flows))
        log.info("comtrade harvest starting", extra={"combinations": len(combos)})

        for reporter, partner, chapter, year, flow in combos:
            try:
                rows = self.fetch_flows(
                    reporter_code=reporter,
                    partner_code=partner,
                    cmd_code=chapter,
                    period=year,
                    flow_code=flow,
                )
            except UpstreamShapeError as exc:
                log.error(
                    "comtrade request failed",
                    extra={
                        "reporter": reporter,
                        "partner": partner,
                        "chapter": chapter,
                        "year": year,
                        "flow": flow,
                        "error": str(exc),
                    },
                )
                continue

            for row in rows:
                model = self.to_model(row, url=BASE_URL)
                if model is not None:
                    yield model


def _as_float(value: Any) -> float | None:
    if value in (None, "", "NA"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def grid_size(
    reporters: dict[int, str] | None = None,
    partners: dict[int, str] | None = None,
    chapters: dict[str, str] | None = None,
    years: tuple[int, ...] = DEFAULT_YEARS,
    flows: tuple[str, ...] = ("M", "X"),
) -> int:
    """Number of API requests a full harvest will make."""
    return (
        len(reporters or REPORTERS)
        * len(partners or PARTNERS)
        * len(chapters or HS_CHAPTERS)
        * len(years)
        * len(flows)
    )
