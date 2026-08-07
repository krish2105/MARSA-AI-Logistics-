"""Ingestor contracts, exercised against recorded response shapes."""

from __future__ import annotations

import httpx
import pytest
import respx

from marsa.ingestion.comtrade import BASE_URL, ComtradeClient, grid_size
from marsa.ingestion.cross import (
    CrossClient,
    extract_hts_codes,
    extract_ruling_refs,
    html_to_text,
)
from marsa.ingestion.fetcher import Fetcher, UpstreamShapeError
from marsa.ingestion.worldbank import WorldBankClient


@pytest.fixture
def fetcher(tmp_path):
    with Fetcher(requests_per_second=1000, cache_dir=tmp_path / "c", use_cache=False) as f:
        yield f


# ─── Comtrade ────────────────────────────────────────────────────────────────

COMTRADE_ROW = {
    "period": "2023", "refYear": 2023,
    "reporterCode": 784, "reporterISO": "ARE", "reporterDesc": "United Arab Emirates",
    "partnerCode": 156, "partnerISO": "CHN", "partnerDesc": "China",
    "flowCode": "M", "flowDesc": "Import",
    "cmdCode": "85", "cmdDesc": "Electrical machinery",
    "primaryValue": 1234567.89, "netWgt": 45000.0, "qty": 1200.0, "qtyUnitAbbr": "kg",
}


class TestComtrade:
    @respx.mock
    def test_sends_lowercase_reportercode(self, fetcher):
        """The API ignores `reporterCode` and silently returns all reporters.

        This is the single highest-consequence detail in the client, so it is
        asserted rather than trusted.
        """
        route = respx.get(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json={"count": 1, "data": [COMTRADE_ROW]})
        )
        ComtradeClient(fetcher).fetch_flows(
            reporter_code=784, partner_code=156, cmd_code="85", period=2023
        )
        params = route.calls[0].request.url.params
        assert params["reportercode"] == "784"
        assert "reporterCode" not in params

    @respx.mock
    def test_maps_row_to_model(self, fetcher):
        respx.get(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json={"count": 1, "data": [COMTRADE_ROW]})
        )
        rows = ComtradeClient(fetcher).fetch_flows(
            reporter_code=784, partner_code=156, cmd_code="85", period=2023
        )
        model = ComtradeClient.to_model(rows[0])
        assert model is not None
        assert model.reporter_iso == "ARE"
        assert model.primary_value == pytest.approx(1234567.89)
        assert model.doc_id == "comtrade::2023::784::156::85::M"

    @respx.mock
    def test_missing_data_key_raises(self, fetcher):
        respx.get(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json={"error": "invalid subscription"})
        )
        with pytest.raises(UpstreamShapeError, match="no 'data' key"):
            ComtradeClient(fetcher).fetch_flows(
                reporter_code=784, partner_code=156, cmd_code="85", period=2023
            )

    def test_bad_row_is_skipped_not_fatal(self):
        assert ComtradeClient.to_model({"cmdCode": "85"}) is None

    def test_grid_size_is_reported_honestly(self):
        # 1 reporter x 8 partners x 6 chapters x 3 years x 2 flows
        assert grid_size() == 288


# ─── CROSS ───────────────────────────────────────────────────────────────────

CROSS_RULING = {
    "rulingNumber": "NY N302241",
    "collection": "NY",
    "rulingDate": "2019-02-14",
    "subject": "Tariff classification of a lithium-ion power bank from China",
    "rulingText": (
        "<p>The applicable subheading will be <b>8507.60.0020</b>.</p>"
        "<p>See also NY N298877 and HQ H289765 for consistent treatment.</p>"
    ),
}


class TestCrossParsing:
    def test_extracts_hts_codes(self):
        codes = extract_hts_codes("classified under 8507.60.0020 not 8504.40.95")
        assert "8507.60.0020" in codes
        assert "8504.40.95" in codes

    def test_extracts_ruling_refs_excluding_self(self):
        refs = extract_ruling_refs(
            "Consistent with NY N298877 and HQ H289765.", exclude="NY N302241"
        )
        assert refs == ["NY N298877", "HQ H289765"]

    def test_self_reference_is_dropped(self):
        assert extract_ruling_refs("See NY N302241.", exclude="NY N302241") == []

    def test_html_is_flattened(self):
        assert "8507.60.0020" in html_to_text(CROSS_RULING["rulingText"])
        assert "<b>" not in html_to_text(CROSS_RULING["rulingText"])

    def test_plain_text_passes_through(self):
        assert html_to_text("already plain") == "already plain"

    def test_to_model_populates_derived_fields(self):
        model = CrossClient.to_model(CROSS_RULING)
        assert model is not None
        assert model.ruling_number == "NY N302241"
        assert "8507.60.0020" in model.hts_codes
        assert "HQ H289765" in model.related_rulings
        assert model.doc_id == "cross::NYN302241"

    def test_alternative_key_spellings_are_accepted(self):
        """The endpoint is undocumented, so the mapper accepts known variants."""
        model = CrossClient.to_model(
            {"number": "HQ H123456", "title": "Widget", "body": "Subheading 8481.80.9005."}
        )
        assert model is not None
        assert model.ruling_number == "HQ H123456"
        assert "8481.80.9005" in model.hts_codes

    def test_record_without_number_is_skipped(self):
        assert CrossClient.to_model({"subject": "orphan"}) is None


class TestCrossSearch:
    @respx.mock
    def test_unexpected_shape_names_the_keys(self, fetcher):
        """A silent empty corpus is the expensive failure; fail loudly instead."""
        respx.get(url__startswith="https://rulings.cbp.gov/api/search").mock(
            return_value=httpx.Response(200, json={"unexpected": []})
        )
        with pytest.raises(UpstreamShapeError, match="probe-cross"):
            CrossClient(fetcher).search("battery")

    @respx.mock
    def test_probe_reports_contract(self, fetcher):
        respx.get(url__startswith="https://rulings.cbp.gov/api/search").mock(
            return_value=httpx.Response(200, json={"rulings": [CROSS_RULING], "total": 1})
        )
        result = CrossClient(fetcher).probe("battery")
        assert result["result_count"] == 1
        assert "rulingNumber" in result["first_result_keys"]


# ─── World Bank ──────────────────────────────────────────────────────────────


class TestWorldBank:
    @respx.mock
    def test_parses_two_element_envelope(self, fetcher):
        """The World Bank API returns [metadata, rows] — an array, not an object."""
        respx.get(url__startswith="https://api.worldbank.org").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"page": 1, "total": 1},
                    [
                        {
                            "countryiso3code": "ARE",
                            "country": {"id": "ARE", "value": "United Arab Emirates"},
                            "date": "2023",
                            "value": 4.05,
                        }
                    ],
                ],
            )
        )
        rows = WorldBankClient(fetcher).fetch_indicator("LP.LPI.OVRL.XQ")
        assert rows[0]["countryiso3code"] == "ARE"

    @respx.mock
    def test_object_response_raises(self, fetcher):
        respx.get(url__startswith="https://api.worldbank.org").mock(
            return_value=httpx.Response(200, json={"message": "invalid indicator"})
        )
        with pytest.raises(UpstreamShapeError):
            WorldBankClient(fetcher).fetch_indicator("BAD")

    @respx.mock
    def test_harvest_pivots_indicators_into_one_record(self, fetcher):
        def responder(request):
            return httpx.Response(
                200,
                json=[
                    {"page": 1},
                    [
                        {
                            "countryiso3code": "ARE",
                            "country": {"id": "ARE", "value": "United Arab Emirates"},
                            "date": "2023",
                            "value": 4.0,
                        }
                    ],
                ],
            )

        respx.get(url__startswith="https://api.worldbank.org").mock(side_effect=responder)
        records = list(WorldBankClient(fetcher).harvest_lpi(countries=("ARE",)))
        assert len(records) == 1, "seven indicators must collapse to one country-year row"
        assert records[0].country_iso3 == "ARE"
        assert records[0].lpi_score == 4.0
        assert records[0].customs_score == 4.0
