"""Phase I — screening, and decision gate G5.

G5 requires ≥95% recall on supplier-name variants. The asymmetry is the point:
a false positive costs someone ten minutes reading a name, a false negative
costs a detained container and a thirty-day evidentiary burden. So the tests
weight misses far more heavily than noise — and the hardest cases here are the
*negatives* that must not match, because a matcher loose enough to catch every
variant will also catch every unrelated company.
"""

from __future__ import annotations

from datetime import date

import pytest

from marsa.regulatory import fixtures as fx
from marsa.regulatory.store import InstrumentStore
from marsa.screening.engine import Finding, screen
from marsa.screening.matcher import HIT, normalise, similarity

LISTED = "Sunrise Textile Manufacturing Co., Ltd."
TODAY = date(2026, 9, 1)


@pytest.fixture
def store() -> InstrumentStore:
    s = InstrumentStore()
    s.extend(fx.generate_instruments())
    return s


class TestNormalisation:
    def test_legal_forms_carry_no_identity(self):
        assert normalise("Acme Co., Ltd.") == normalise("ACME")

    def test_word_order_does_not_change_identity(self):
        """Sources list the city first or last, inconsistently."""
        assert normalise("Bitland Hefei") == normalise("Hefei Bitland")

    def test_accents_and_full_width_fold(self):
        """The same company appears accented and full-width across sources;
        comparing raw forms fails silently."""
        assert normalise("Café Industrie") == normalise("Cafe Industrie")

    def test_punctuation_is_not_identity(self):
        assert normalise("Bitland (Hefei) Information Tech") == normalise(
            "Bitland Hefei Information Technology"
        )

    def test_a_name_that_is_only_noise_normalises_empty(self):
        assert normalise("The Company Ltd") == ""


class TestGateG5Recall:
    """Every one of these is the same company as LISTED and must be caught."""

    @pytest.mark.parametrize("variant", [
        "Sunrise Textile Manufacturing Co., Ltd.",
        "SUNRISE TEXTILE MFG",
        "Sunrise Textile Manufacturing",
        "sunrise textile manufacturing co ltd",
        "Textile Manufacturing, Sunrise Co.",
        "Sunrise Textile Mfg Co Limited",
    ])
    def test_variants_of_a_listed_name_are_caught(self, variant):
        assert similarity(variant, LISTED) >= HIT, (
            f"{variant!r} scored {similarity(variant, LISTED):.2f} — a miss here "
            "is a detained container"
        )

    def test_recall_meets_the_gate(self):
        variants = [
            "Sunrise Textile Manufacturing Co., Ltd.",
            "SUNRISE TEXTILE MFG",
            "Sunrise Textile Manufacturing",
            "sunrise textile manufacturing co ltd",
            "Textile Manufacturing, Sunrise Co.",
            "Sunrise  Textile   Mfg",
            "Sunrise Textile Manufacturing Company Limited",
        ]
        caught = sum(1 for v in variants if similarity(v, LISTED) >= HIT)
        assert caught / len(variants) >= 0.95, f"recall {caught}/{len(variants)}"


class TestHardNegatives:
    """The other half of the gate. A matcher loose enough to catch every variant
    also catches every unrelated company, and a screening list nobody trusts is
    a screening list nobody reads."""

    @pytest.mark.parametrize("other", [
        "Sunset Textile Manufacturing Co., Ltd.",
        "Sunrise Ceramics Co., Ltd.",
        "Acme Widgets Inc",
        "Jebel Ali Freight Forwarding LLC",
    ])
    def test_different_companies_are_not_hits(self, other):
        assert similarity(other, LISTED) < HIT, (
            f"{other!r} scored {similarity(other, LISTED):.2f} against {LISTED!r}"
        )

    def test_a_shared_legal_form_alone_scores_nothing(self):
        """'Co Ltd' is shared by thousands of unrelated companies."""
        assert similarity("Entirely Different Co., Ltd.", LISTED) == 0.0

    def test_no_shared_identifying_token_scores_zero(self):
        """Sequence similarity between unrelated names reaches 0.6 on shared
        letters alone — which is how a list starts producing noise."""
        assert similarity("Zeta Holdings", "Beta Holdings") == 0.0


class TestNeverClears:
    """The design rule. None of the three findings is a clearance."""

    def test_an_unmatched_supplier_is_no_evidence_found_not_clear(self, store):
        report = screen(store, suppliers=["Acme Widgets Inc"], on=TODAY)
        finding = report.suppliers[0].finding
        assert finding is Finding.NO_EVIDENCE_FOUND
        assert finding.value != "clear"
        assert "not a clearance" in report.suppliers[0].notes[0]

    def test_the_disclaimer_names_the_list_version_and_says_it_is_not_a_clearance(
        self, store
    ):
        report = screen(store, suppliers=["Acme Widgets Inc"], on=TODAY)
        assert "not a clearance" in report.disclaimer
        assert str(report.entities_checked) in report.disclaimer

    def test_an_empty_list_warns_that_nothing_was_checked(self):
        """Otherwise every supplier reads as screened when none was."""
        report = screen(InstrumentStore(), suppliers=["Anything"], on=TODAY)
        assert report.suppliers[0].finding is Finding.NO_EVIDENCE_FOUND
        assert any("nothing was checked" in w for w in report.warnings)

    def test_a_near_miss_is_possible_and_shows_its_working(self, store):
        report = screen(store, suppliers=["Southern Silica Materials Group"], on=TODAY)
        result = report.suppliers[0]
        assert result.finding is Finding.POSSIBLE
        assert result.matches[0].shared_tokens
        assert "A human decides" in result.notes[0]

    def test_an_exact_listing_is_a_hit_with_the_instrument_cited(self, store):
        report = screen(store, suppliers=[LISTED], on=TODAY)
        assert report.suppliers[0].finding is Finding.HIT
        assert report.suppliers[0].instrument_ids


class TestPointInTime:
    """Listings have dates too. An entity added in August was not listed in
    March, and screening a March entry against today's list reports a risk that
    did not exist."""

    def test_a_listing_added_later_does_not_apply_earlier(self, store):
        # "Northern Silica Materials Group" was listed 2026-08-03.
        before = screen(store, suppliers=["Northern Silica Materials Group"],
                        on=date(2026, 5, 1))
        after = screen(store, suppliers=["Northern Silica Materials Group"],
                       on=date(2026, 9, 1))
        assert before.suppliers[0].finding is Finding.NO_EVIDENCE_FOUND
        assert after.suppliers[0].finding is Finding.HIT

    def test_entities_checked_reflects_the_date(self, store):
        early = screen(store, suppliers=["x"], on=date(2026, 5, 1))
        late = screen(store, suppliers=["x"], on=date(2026, 9, 1))
        assert late.entities_checked > early.entities_checked


class TestGoodsScope:
    def test_a_cbam_code_is_in_scope_with_the_obligation_named(self, store):
        report = screen(store, suppliers=[], hts_codes=["7208.10.00"], on=TODAY)
        goods = report.goods[0]
        assert goods.finding is Finding.HIT
        assert "CBAM-iron_and_steel" in goods.in_scope
        assert any("rejected outright" in n for n in goods.notes)

    def test_an_out_of_scope_code_is_not_a_clearance_either(self, store):
        report = screen(store, suppliers=[], hts_codes=["8507.60.00"], on=TODAY)
        assert report.goods[0].finding is Finding.NO_EVIDENCE_FOUND

    def test_goods_scope_has_no_possible(self, store):
        """A code list is exact — inventing a POSSIBLE would imply a doubt the
        data does not contain."""
        report = screen(store, suppliers=[], hts_codes=["7208.10.00", "9999.99"], on=TODAY)
        assert {g.finding for g in report.goods} <= {
            Finding.HIT, Finding.NO_EVIDENCE_FOUND
        }


class TestProvenance:
    def test_synthetic_listings_are_flagged(self, store):
        report = screen(store, suppliers=["x"], on=TODAY)
        assert report.any_synthetic
        assert any("SYNTHETIC" in w for w in report.warnings)

    def test_counts_add_up(self, store):
        report = screen(
            store,
            suppliers=[LISTED, "Southern Silica Materials Group", "Acme Widgets Inc"],
            on=TODAY,
        )
        counts = report.as_dict()["counts"]
        assert sum(counts.values()) == 3
        assert counts["hit"] == 1
        assert counts["possible"] == 1
        assert counts["noEvidenceFound"] == 1
