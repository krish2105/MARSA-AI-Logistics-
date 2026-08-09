"""Phase J — grounded classification with calibrated abstention."""

from __future__ import annotations

import pytest

from marsa.classify.abstain import DEFAULT_THRESHOLD, decide
from marsa.classify.citations import Citation, InsufficientEvidence, Suggestion
from marsa.classify.curve import CurvePoint, is_correct, measure
from marsa.classify.evidence import (
    EvidenceSignals,
    arm_agreement,
    code_presence,
    confidence,
    lexical_anchoring,
    named_ruling_missing,
    provision_agreement,
)
from marsa.indexing.chunking import Chunk
from marsa.indexing.hybrid import RetrievedRuling
from marsa.indexing.store import ScoredChunk


def _ruling(number, codes, *, text="", arms=("dense",)):
    chunk = Chunk(
        chunk_id=f"{number}#0",
        parent_id=number,
        ruling_number=number,
        section="BODY",
        ordinal=0,
        text=text or f"Ruling {number}",
        hts_codes=tuple(codes),
    )
    return RetrievedRuling(
        parent_id=number,
        ruling_number=number,
        score=1.0,
        chunks=[ScoredChunk(chunk, 1.0, "fused", arms=tuple(arms))],
    )


class TestProvisionAgreement:
    def test_unanimous_set_scores_one(self):
        rulings = [_ruling(f"N{i}", ["8507.60.0020"]) for i in range(5)]
        share, modal = provision_agreement(rulings)
        assert share == pytest.approx(1.0)
        assert modal == "8507.60"

    def test_scattered_set_scores_low(self):
        codes = ["8507.60.00", "8504.40.00", "6109.10.00", "9403.60.00", "8415.10.00"]
        share, _ = provision_agreement([_ruling(f"N{i}", [c]) for i, c in enumerate(codes)])
        assert share == pytest.approx(0.2)

    def test_uncoded_rulings_are_silent_not_dissenting(self):
        rulings = [_ruling("A", ["8507.60.0020"]), _ruling("B", [])]
        share, modal = provision_agreement(rulings)
        # B carries no CBP code, so it neither supports nor opposes A.
        assert share == pytest.approx(1.0)
        assert modal == "8507.60"

    def test_one_ruling_cannot_manufacture_agreement_across_its_own_codes(self):
        multi = _ruling("A", ["8507.60.0020", "8504.40.9580"])
        share, _ = provision_agreement([multi])
        assert share == pytest.approx(0.5)

    def test_empty_set_scores_zero(self):
        assert provision_agreement([]) == (0.0, "")


class TestArmAgreement:
    def test_corroboration_requires_more_than_one_arm(self):
        both = [_ruling("A", ["8507.60.00"], arms=("dense", "sparse"))]
        one = [_ruling("B", ["8507.60.00"], arms=("dense",))]
        assert arm_agreement(both) == pytest.approx(1.0)
        assert arm_agreement(one) == 0.0

    def test_survives_reranking(self):
        """Regression: rerank overwrote `retriever`, zeroing this signal."""
        from marsa.indexing.rerank import build_reranker

        chunk = Chunk("c#0", "c", "N1", "BODY", 0, "text", ())
        fused = [ScoredChunk(chunk, 1.0, "dense+sparse", arms=("dense", "sparse"))]
        reranked = build_reranker("heuristic").rerank("query", fused, k=1)
        assert reranked[0].arms == ("dense", "sparse")

    def test_empty_set_scores_zero(self):
        assert arm_agreement([]) == 0.0


class TestLexicalAnchoring:
    def test_measures_against_the_top_ruling_only(self):
        top = _ruling("A", ["8507.60.00"], text="lithium-ion power bank battery")
        rest = _ruling("B", ["8507.60.00"], text="unrelated furniture")
        assert lexical_anchoring("power bank", [top, rest]) == pytest.approx(1.0)
        assert lexical_anchoring("power bank", [rest, top]) == 0.0

    def test_query_of_only_stopwords_scores_zero(self):
        assert lexical_anchoring("what is the duty rate", [_ruling("A", [])]) == 0.0


class TestCodePresence:
    def test_not_applicable_when_no_code_is_named(self):
        assert code_presence("power banks", [_ruling("A", ["8507.60.00"])]) is None

    def test_one_when_the_named_code_is_present(self):
        assert code_presence("8507.60.0020", [_ruling("A", ["8507.60.0020"])]) == 1.0

    def test_zero_when_the_named_code_is_absent(self):
        assert code_presence("8507.60.0020", [_ruling("A", ["6109.10.00"])]) == 0.0


class TestNamedRulingVeto:
    """A question about one document cannot be answered from five others."""

    def test_detects_a_missing_named_ruling(self):
        assert named_ruling_missing("What does HQ H289765 say?", [_ruling("N1", [])]) == (
            "H289765"
        )

    def test_silent_when_the_named_ruling_was_retrieved(self):
        assert named_ruling_missing("What does NY N302241 hold?", [_ruling("N302241", [])]) == ""

    def test_silent_when_no_ruling_is_named(self):
        assert named_ruling_missing("How are LEDs classified?", [_ruling("N1", [])]) == ""

    def test_veto_overrides_otherwise_strong_evidence(self):
        """Regression: this exact shape scored 0.688 before the veto existed."""
        signals = EvidenceSignals(
            provision_agreement=1.0,
            arm_agreement=1.0,
            lexical_anchoring=1.0,
            missing_ruling="H289765",
        )
        assert confidence(signals) == 0.0


class TestConfidence:
    def test_renormalises_when_a_signal_does_not_apply(self):
        """A query that quotes no code must stay on the same scale."""
        with_code = EvidenceSignals(1.0, 1.0, 1.0, code_presence=1.0)
        without = EvidenceSignals(1.0, 1.0, 1.0, code_presence=None)
        assert confidence(with_code) == pytest.approx(1.0)
        assert confidence(without) == pytest.approx(1.0)

    def test_missing_signal_is_not_scored_as_failure(self):
        without = EvidenceSignals(0.8, 0.8, 0.8, code_presence=None)
        as_zero = EvidenceSignals(0.8, 0.8, 0.8, code_presence=0.0)
        assert confidence(without) > confidence(as_zero)

    def test_bounded(self):
        assert confidence(EvidenceSignals()) == pytest.approx(0.0)
        assert confidence(EvidenceSignals(1.0, 1.0, 1.0, 1.0)) == pytest.approx(1.0)


class TestCitationsAreStructural:
    def test_a_suggestion_cannot_exist_without_a_citation(self):
        with pytest.raises(ValueError, match="must cite"):
            Suggestion(subheading="8507.60", confidence=0.9, citations=())

    def test_a_citation_must_name_a_ruling(self):
        with pytest.raises(ValueError, match="must name a ruling"):
            Citation(ruling_number="  ", assigned_codes=())

    def test_citation_resolves_to_the_public_cbp_url(self):
        citation = Citation("N302241", ("8507.60.0020",))
        assert citation.url == "https://rulings.cbp.gov/ruling/N302241"


class TestDecide:
    def test_classifies_a_unanimous_well_anchored_set(self):
        rulings = [
            _ruling(
                f"N{i}",
                ["8507.60.0020"],
                text="lithium-ion power bank",
                arms=("dense", "sparse"),
            )
            for i in range(5)
        ]
        outcome = decide("lithium-ion power bank", rulings)
        assert isinstance(outcome, Suggestion)
        assert outcome.subheading == "8507.60"
        assert outcome.citations

    def test_declines_on_a_scattered_set(self):
        codes = ["8507.60.00", "8504.40.00", "6109.10.00", "9403.60.00", "8415.10.00"]
        rulings = [_ruling(f"N{i}", [c]) for i, c in enumerate(codes)]
        assert isinstance(decide("something", rulings), InsufficientEvidence)

    def test_declines_on_empty_retrieval(self):
        outcome = decide("anything", [])
        assert isinstance(outcome, InsufficientEvidence)
        assert outcome.confidence == 0.0

    def test_a_refusal_still_hands_back_the_near_misses(self):
        codes = ["8507.60.00", "8504.40.00", "6109.10.00", "9403.60.00", "8415.10.00"]
        rulings = [_ruling(f"N{i}", [c]) for i, c in enumerate(codes)]
        outcome = decide("something", rulings)
        assert isinstance(outcome, InsufficientEvidence)
        assert outcome.nearest, "a refusal that returns nothing is worse than one that does not"
        assert outcome.reasons

    def test_only_rulings_under_the_suggested_subheading_may_be_cited(self):
        rulings = [
            _ruling("A", ["8507.60.0020"], text="power bank", arms=("dense", "sparse")),
            _ruling("B", ["8507.60.0030"], text="power bank", arms=("dense", "sparse")),
            _ruling("C", ["9403.60.0000"], text="power bank", arms=("dense", "sparse")),
        ]
        outcome = decide("power bank", rulings, threshold=0.1)
        assert isinstance(outcome, Suggestion)
        cited = {c.ruling_number for c in outcome.citations}
        assert "C" not in cited, "a ruling that merely ranked well is not support"

    def test_raising_the_threshold_never_turns_a_refusal_into_an_answer(self):
        rulings = [
            _ruling(f"N{i}", ["8507.60.0020"], text="power bank", arms=("dense", "sparse"))
            for i in range(5)
        ]
        answered = [
            isinstance(decide("power bank", rulings, threshold=t / 20), Suggestion)
            for t in range(21)
        ]
        # Once it starts refusing it must not start answering again.
        assert answered == sorted(answered, reverse=True)


class TestCurve:
    def _fake_retrieve(self, query):
        return [
            _ruling(f"N{i}", ["8507.60.0020"], text=query, arms=("dense", "sparse"))
            for i in range(5)
        ]

    def test_correctness_is_prefix_matched_at_the_asked_granularity(self):
        assert is_correct("6109.10", ("61",))
        assert is_correct("8507.60", ("8507.60",))
        assert not is_correct("6203.43", ("61",))

    def test_abstention_rate_and_accuracy_are_complementary_counts(self):
        point = CurvePoint(threshold=0.5, answered=6, correct=3, abstained=4)
        assert point.total == 10
        assert point.abstention_rate == pytest.approx(0.4)
        assert point.accuracy_on_answered == pytest.approx(0.5)

    def test_accuracy_is_zero_not_undefined_when_nothing_is_answered(self):
        assert CurvePoint(threshold=1.0, abstained=10).accuracy_on_answered == 0.0

    def test_sweep_covers_every_threshold_and_excludes_unlabelled_queries(self):
        from marsa.eval.relevance import coverage

        report = measure(self._fake_retrieve)
        assert len(report.points) == 21
        assert report.excluded == coverage()["unlabelled"]
        assert report.unanswerable == coverage()["unanswerable"]

    def test_answering_an_unsupported_query_is_never_scored_correct(self):
        report = measure(self._fake_retrieve, shipped=0.0)
        point = report.at(0.0)
        assert point is not None
        assert point.unsupported_answered > 0, "this fixture answers everything"
        # Every unsupported answer must count against the gate and never for accuracy.
        assert point.correct <= point.answered - point.unsupported_answered

    def test_gate_fails_when_an_unsupported_query_is_answered(self):
        assert not measure(self._fake_retrieve, shipped=0.0).gate_passes

    def test_gate_passes_once_the_threshold_outruns_the_evidence(self):
        """`decide` answers when confidence >= tau, so tau=1.0 alone is not a
        guarantee of abstention — a fixture scoring exactly 1.0 still answers.
        A set that no arm corroborates cannot reach 1.0, and then it does."""

        def weak_retrieve(query):
            return [_ruling(f"N{i}", ["8507.60.0020"], text=query, arms=("dense",))
                    for i in range(5)]

        assert measure(weak_retrieve, shipped=1.0).gate_passes
        assert not measure(weak_retrieve, shipped=0.0).gate_passes


class TestShippedThreshold:
    def test_default_is_within_range(self):
        assert 0.0 < DEFAULT_THRESHOLD < 1.0
