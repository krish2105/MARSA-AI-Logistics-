"""Phase H — the duty engine, and decision gate G3.

G3 requires **100% exact match**. Arithmetic has no partial credit: a
calculator that is right 29 times out of 30 is a liability generator, not a
95%-accurate tool.

What these verify and what they do not, stated because the distinction is easy
to lose: the expected values below are computed by hand from the *fixture*
instruments, so they test that the engine implements the stated stacking rules
correctly. They do **not** validate that the rates are real. The algorithm is
under test; the data is synthetic and marked as such.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from marsa.duty.detect import detect
from marsa.duty.engine import USMCA_FLOOR_PERCENT, quote
from marsa.regulatory import fixtures as fx
from marsa.regulatory.store import InstrumentStore

AFTER_JUNE = date(2026, 9, 1)
BEFORE_JUNE = date(2026, 3, 1)

STEEL = "7326.90.86"      # MFN 2.9%, in 232 scope (73)
BATTERY = "8507.60.00"    # MFN 3.4%, not in 232 scope


@pytest.fixture
def store() -> InstrumentStore:
    s = InstrumentStore()
    s.extend(fx.generate_instruments())
    return s


def _pct(q):
    return q.total_percent


class TestGateG3ExactArithmetic:
    """The hand-checked set. Every case must match exactly."""

    def test_base_rate_alone(self, store):
        """Battery from Germany: MFN only, nothing else in scope."""
        q = quote(store, hts=BATTERY, origin="DE", customs_value=10_000, on=AFTER_JUNE)
        assert q.answered
        assert _pct(q) == Decimal("3.4")
        assert q.total_amount == Decimal("340.00")

    def test_stacked_232_and_301_for_china(self, store):
        """Steel from China after June: 2.9 MFN + 50 (232) + 25 (301) = 77.9%.

        The case the whole engine exists for — two overlays from different
        programmes stacking on a base, none of them replacing another.
        """
        q = quote(store, hts=STEEL, origin="CN", customs_value=40_000, on=AFTER_JUNE)
        assert q.answered, q.refusals
        assert [layer.programme.value for layer in q.layers] == [
            "mfn", "section_232", "section_301"
        ]
        assert _pct(q) == Decimal("77.9")
        assert q.total_amount == Decimal("31160.00")

    def test_the_superseded_rate_is_not_used(self, store):
        """Before June the 232 layer is 25%, after it is 50% — and the old
        instrument must not linger once replaced."""
        before = quote(store, hts=STEEL, origin="CN", customs_value=1_000, on=BEFORE_JUNE)
        after = quote(store, hts=STEEL, origin="CN", customs_value=1_000, on=AFTER_JUNE)
        assert _pct(before) == Decimal("52.9")   # 2.9 + 25 + 25
        assert _pct(after) == Decimal("77.9")    # 2.9 + 50 + 25
        ids = {layer.instrument_id for layer in after.layers}
        assert "FR-2026-02-232-STEEL" not in ids

    def test_total_duty_cap_applies_to_the_sum_not_a_layer(self, store):
        """Steel from Japan: layers sum to 52.9%, capped to 15% total.

        The contestable decision, pinned. Capping only the 232 component would
        give 2.9 + 15 = 17.9% instead.
        """
        q = quote(store, hts=STEEL, origin="JP", customs_value=100_000, on=AFTER_JUNE)
        assert _pct(q) == Decimal("15")
        assert q.total_amount == Decimal("15000.00")
        cap = [a for a in q.adjustments if a.kind == "total_duty_cap"]
        assert len(cap) == 1
        assert cap[0].before_percent == Decimal("52.9")

    def test_a_capped_origin_can_pay_less_than_an_uncapped_one(self, store):
        """The counter-intuitive consequence of a total cap, asserted so nobody
        'fixes' it later."""
        japan = quote(store, hts=STEEL, origin="JP", customs_value=100_000, on=AFTER_JUNE)
        china = quote(store, hts=STEEL, origin="CN", customs_value=100_000, on=AFTER_JUNE)
        assert japan.total_amount < china.total_amount

    def test_ieepa_stacks_for_a_listed_origin(self, store):
        """Battery from Vietnam: 3.4 MFN + 10 IEEPA. The IEEPA instrument has an
        inferred date, so the engine must refuse rather than price it."""
        q = quote(store, hts=BATTERY, origin="VN", customs_value=10_000, on=AFTER_JUNE)
        assert not q.answered
        assert any("inferred effective date" in r for r in q.refusals)

    def test_usmca_content_reduction_then_floor(self, store):
        """Steel from Mexico, 60% US content: 52.9% capped? No cap for MX, so
        52.9 × 0.40 = 21.16%, which clears the 15% floor."""
        q = quote(
            store, hts=STEEL, origin="MX", customs_value=100_000,
            on=AFTER_JUNE, us_content_percent=60,
        )
        assert _pct(q) == Decimal("21.16")
        assert [a.kind for a in q.adjustments] == ["usmca_content"]

    def test_usmca_floor_binds_when_content_is_high(self, store):
        """90% US content: 52.9 × 0.10 = 5.29%, below the floor, so 15%."""
        q = quote(
            store, hts=STEEL, origin="MX", customs_value=100_000,
            on=AFTER_JUNE, us_content_percent=90,
        )
        assert _pct(q) == USMCA_FLOOR_PERCENT
        assert [a.kind for a in q.adjustments] == ["usmca_content", "usmca_floor"]

    def test_money_is_exact_at_awkward_values(self, store):
        """Decimal throughout: a float total on a large entry drifts by cents,
        and cents are what a broker reconciles against."""
        q = quote(store, hts=BATTERY, origin="DE", customs_value="2439999.99", on=AFTER_JUNE)
        assert q.total_amount == Decimal("82960.00")

    def test_rate_is_not_polluted_by_float_error(self, store):
        """Decimal(2.9) is 2.899999…; Decimal('2.9') is not."""
        q = quote(store, hts=BATTERY, origin="DE", customs_value=100, on=AFTER_JUNE)
        assert str(_pct(q)) == "3.4"


class TestRefusal:
    """A gap is a refusal, not a zero. Understated duty is the expensive
    direction to be wrong in."""

    def test_missing_base_rate_refuses_rather_than_returning_the_overlays(self, store):
        q = quote(store, hts="9999.99.99", origin="CN", customs_value=1_000, on=AFTER_JUNE)
        assert not q.answered
        assert q.total_percent is None
        assert q.total_amount is None
        assert any("No MFN base rate" in r for r in q.refusals)

    def test_a_refusal_is_never_zero(self, store):
        """The failure mode this guards: a caller reading None as 0.0."""
        q = quote(store, hts="9999.99.99", origin="CN", customs_value=1_000, on=AFTER_JUNE)
        assert q.total_amount is not Decimal("0")
        assert q.as_dict()["totalAmount"] is None

    def test_contradiction_refuses_and_names_both(self):
        store = InstrumentStore()
        store.extend(fx.generate_instruments(with_contradiction=True))
        q = quote(store, hts=STEEL, origin="CN", customs_value=1_000, on=AFTER_JUNE)
        assert not q.answered
        assert any("neither supersedes" in r for r in q.refusals)

    def test_inferred_date_refuses(self, store):
        q = quote(store, hts=BATTERY, origin="BR", customs_value=1_000, on=AFTER_JUNE)
        assert not q.answered

    def test_a_date_before_anything_commenced_refuses(self, store):
        q = quote(store, hts=STEEL, origin="CN", customs_value=1_000, on=date(2020, 1, 1))
        assert not q.answered


class TestDetection:
    """Deterministic pre-filter. G4's guarantee rests on this, so it is
    conservative in both directions."""

    @pytest.mark.parametrize("query", [
        "What duty applies to HTS 7326.90.86 from China?",
        "Landed cost for 8507.60.00 worth $40,000 from Vietnam",
        "How much section 232 duty on a $10,000 steel shipment?",
        "What tariff applies to 7326.90.86?",
    ])
    def test_recognises_duty_questions(self, query):
        assert detect(query).is_duty_question, query

    @pytest.mark.parametrize("query", [
        "Which suppliers are exposed if Jebel Ali congestion worsens?",
        "What HTS code applies to lithium-ion power banks?",
        "How much will this cost?",
        "What was our trade value with China in 2026?",
    ])
    def test_leaves_other_questions_alone(self, query):
        assert not detect(query).is_duty_question, query

    def test_a_bare_year_is_not_an_hts_code(self):
        """'trade in 2026' must not route to a calculator."""
        assert detect("What was our trade value in 2026?").hts is None

    def test_a_code_alone_is_classification_not_duty(self):
        """Without duty language it is a classification question, and stealing
        it from the fast path costs an answer."""
        assert not detect("Tell me about 7326.90.86").is_duty_question

    def test_extracts_the_code_for_the_engine(self):
        assert detect("duty on 7326.90.86 from China").hts == "7326.90.86"


class TestExposureVeto:
    """Duty vocabulary does not make a question a duty calculation.

    Caught by running all four routes rather than by review: the multi-hop
    query in CI's own router smoke test was being stolen by the pre-filter,
    because it mentions a tariff and contains an HTS-shaped number.
    """

    @pytest.mark.parametrize("query", [
        "Which of our electronics shipments are exposed if the new tariff on "
        "HS 8541 takes effect next quarter?",
        "Which suppliers are affected by the section 232 tariff on 7326?",
        "How many of our shipments are at risk from the 301 duty?",
    ])
    def test_exposure_questions_are_not_duty_calculations(self, query):
        assert not detect(query).is_duty_question, query

    @pytest.mark.parametrize("query", [
        "Which of our electronics shipments are exposed if the new tariff on "
        "HS 8541 takes effect next quarter?",
        "Which suppliers are affected by the section 232 tariff on 7326?",
    ])
    def test_the_veto_is_what_rejects_them(self, query):
        """Separate from the case above on purpose. These carry an HTS code and
        duty language, so they *would* qualify — the veto is doing the work.
        'How many of our shipments are at risk from the 301 duty?' has neither
        a code nor a value and never qualifies, so no veto is recorded; asserting
        one there would test nothing."""
        assert any("vetoed" in r for r in detect(query).reasons), query

    def test_the_veto_does_not_swallow_real_duty_questions(self):
        assert detect("What duty applies to 7326.90.86 from China?").is_duty_question

    def test_ci_router_expectations_still_hold(self):
        """The three queries CI asserts routes for, pinned here so a detector
        change fails in unit tests rather than in the smoke test."""
        assert not detect("What HTS code applies to lithium-ion power banks?").is_duty_question
        assert not detect(
            "Which of our shipments are exposed if the tariff on HS 8541 takes effect?"
        ).is_duty_question
        assert not detect(
            "Which suppliers are exposed if Jebel Ali congestion worsens?"
        ).is_duty_question
