"""Phase F: labelled set, routing metrics, quality gating, benchmark, report."""

from __future__ import annotations

import pytest

from marsa.eval.benchmark import BenchmarkReport, PathRun, PathStats, run_benchmark
from marsa.eval.dataset import (
    LABELLED_QUERIES,
    Difficulty,
    by_class,
    by_difficulty,
    dataset_summary,
    validate_dataset,
)
from marsa.eval.quality import (
    NOT_MEASURED,
    JudgeScores,
    QualityReport,
    RagasJudge,
    score_retrieval,
)
from marsa.eval.report import (
    EvaluationResults,
    Gate,
    evaluate_gate,
    render_markdown,
    write_results,
)
from marsa.eval.routing import Prediction, RoutingReport, evaluate_routing
from marsa.router.classifier import HeuristicClassifier, QueryClass
from marsa.router.llm import LLMClient

# ─── Labelled set ────────────────────────────────────────────────────────────


class TestDataset:
    def test_sixty_queries_twenty_per_class(self):
        """The spec asks for ~60 queries, 20 per path type."""
        summary = dataset_summary()
        assert summary["total"] == 60
        assert set(summary["byClass"].values()) == {20}

    def test_validates_clean(self):
        assert validate_dataset() == []

    def test_no_duplicates(self):
        queries = [q.query.strip().lower() for q in LABELLED_QUERIES]
        assert len(queries) == len(set(queries))

    def test_every_query_has_a_rationale(self):
        """Labels are human judgements; the audit trail is why each was made."""
        for item in LABELLED_QUERIES:
            assert item.rationale.strip(), f"no rationale for {item.query!r}"

    def test_contains_ambiguous_cases(self):
        """A set of clear-cut queries cannot surface systematic misrouting."""
        assert len(by_difficulty(Difficulty.AMBIGUOUS)) >= 4

    def test_ambiguous_cases_explain_why_they_are_hard(self):
        for item in by_difficulty(Difficulty.AMBIGUOUS):
            assert len(item.rationale) > 60, f"{item.query!r} does not explain its ambiguity"

    def test_expected_path_maps_from_class(self):
        assert by_class(QueryClass.SIMPLE_FACTUAL)[0].expected_path == "fast"
        assert by_class(QueryClass.MULTI_HOP)[0].expected_path == "agentic"
        assert by_class(QueryClass.RELATIONSHIP)[0].expected_path == "graph"

    def test_validation_catches_imbalance(self, monkeypatch):
        import marsa.eval.dataset as ds

        monkeypatch.setattr(ds, "LABELLED_QUERIES", ds.FACTUAL)
        assert any("unbalanced" in p for p in ds.validate_dataset())


# ─── Routing metrics ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def routing_report():
    return evaluate_routing(HeuristicClassifier())


def _prediction(expected, predicted, *, confidence=0.8, difficulty=Difficulty.CLEAR):
    return Prediction(
        query="q", expected=expected, predicted=predicted,
        confidence=confidence, difficulty=difficulty, rationale="r",
    )


class TestRoutingMetrics:
    def test_accuracy_beats_the_majority_baseline(self, routing_report):
        assert routing_report.accuracy > routing_report.baseline_accuracy

    def test_baseline_is_one_third_on_a_balanced_set(self, routing_report):
        assert routing_report.baseline_accuracy == pytest.approx(1 / 3, abs=0.01)

    def test_confusion_rows_sum_to_support(self, routing_report):
        for actual, row in routing_report.confusion().items():
            assert sum(row.values()) == 20, f"{actual} does not sum to its support"

    def test_perfect_report_scores_one(self):
        report = RoutingReport(
            predictions=[_prediction(c, c) for c in QueryClass for _ in range(3)]
        )
        assert report.accuracy == 1.0
        assert report.bias()["errors"] == 0

    def test_per_class_metrics_are_bounded(self, routing_report):
        for metrics in routing_report.per_class().values():
            for key in ("precision", "recall", "f1"):
                assert 0.0 <= metrics[key] <= 1.0

    def test_bias_detects_leaning_cheap(self):
        """Multi-hop misrouted to fast is a cheaper destination than deserved."""
        report = RoutingReport(
            predictions=[
                _prediction(QueryClass.MULTI_HOP, QueryClass.SIMPLE_FACTUAL)
                for _ in range(5)
            ]
        )
        bias = report.bias()
        assert bias["toCheaper"] == 5
        assert bias["toMoreExpensive"] == 0
        assert "Leans cheap" in bias["note"]

    def test_bias_detects_leaning_expensive(self):
        """The spec flags over-routing to agentic as a plausible finding."""
        report = RoutingReport(
            predictions=[
                _prediction(QueryClass.SIMPLE_FACTUAL, QueryClass.MULTI_HOP)
                for _ in range(5)
            ]
        )
        assert "Leans expensive" in report.bias()["note"]

    def test_confidence_separation_is_reported(self, routing_report):
        calibration = routing_report.confidence_calibration()
        assert "separation" in calibration

    def test_confidence_carries_signal(self, routing_report):
        """The Route Badge shows confidence to users, so it should mean something."""
        assert routing_report.confidence_calibration()["separation"] > 0

    def test_worst_cases_are_sorted_by_confidence(self, routing_report):
        cases = routing_report.worst_cases()
        confidences = [c["confidence"] for c in cases]
        assert confidences == sorted(confidences, reverse=True)

    def test_report_records_whether_the_classifier_was_the_llm(self, routing_report):
        payload = routing_report.as_dict()
        assert payload["classifier"]["isLlm"] is False

    def test_empty_report_does_not_divide_by_zero(self):
        empty = RoutingReport()
        assert empty.accuracy == 0.0
        assert empty.baseline_accuracy == 0.0


# ─── Quality metrics ─────────────────────────────────────────────────────────


class TestRetrievalScoring:
    def test_perfect_retrieval(self):
        scores = score_retrieval(["NY N1", "NY N2"], {"NY N1", "NY N2"})
        assert scores.context_precision == 1.0
        assert scores.context_recall == 1.0

    def test_precision_penalises_extra_results(self):
        scores = score_retrieval(["NY N1", "NY N2", "NY N3"], {"NY N1"})
        assert scores.context_precision == pytest.approx(1 / 3)
        assert scores.context_recall == 1.0

    def test_recall_penalises_misses(self):
        scores = score_retrieval(["NY N1"], {"NY N1", "NY N2"})
        assert scores.context_recall == 0.5

    def test_comparison_ignores_case_and_spacing(self):
        """Three paths format refs three different ways."""
        assert score_retrieval(["ny n302241"], {"NY N302241"}).context_recall == 1.0

    def test_empty_retrieval_scores_zero_without_crashing(self):
        scores = score_retrieval([], {"NY N1"})
        assert scores.context_precision == 0.0
        assert scores.context_recall == 0.0


class TestJudgeGating:
    def test_unavailable_judge_reports_not_measured_never_zero(self):
        """A missing metric that reads as 0.0 is worse than no metric."""
        scores = RagasJudge(LLMClient(providers=[])).score("q", "a", "c")
        payload = scores.as_dict()
        assert payload["faithfulness"] == NOT_MEASURED
        assert payload["answerRelevance"] == NOT_MEASURED
        assert payload["judged"] is False
        assert "not zero" in payload["reasonUnavailable"]

    def test_judge_reports_unavailability_reason(self):
        scores = RagasJudge(LLMClient(providers=[])).score("q", "a", "c")
        assert "No LLM provider" in scores.reason_unavailable

    def test_empty_answer_is_not_judged(self):
        judge = RagasJudge(LLMClient(providers=["groq"]))
        assert judge.score("q", "   ", "c").judged is False

    def test_summary_marks_unjudged_paths(self):
        report = QualityReport(
            per_path_retrieval={"fast": [score_retrieval(["a"], {"a"})]},
            per_path_judge={"fast": [JudgeScores(judged=False)]},
        )
        assert report.summary()["fast"]["faithfulness"] == NOT_MEASURED


# ─── Benchmark ───────────────────────────────────────────────────────────────


class TestBenchmark:
    @pytest.fixture(scope="class")
    @classmethod
    def report(cls):
        return run_benchmark(limit=4)

    def test_runs_every_query_down_every_path(self, report):
        """Routing each query only to its own path is not a comparison."""
        assert set(report.stats) == {"fast", "agentic", "graph"}
        for stats in report.stats.values():
            assert stats.runs

    def test_cold_start_is_separated_from_warm(self, report):
        for stats in report.stats.values():
            cold = [r for r in stats.runs if r.cold]
            assert len(cold) == 1, "exactly one run per path should be marked cold"
            assert len(stats.warm) == len(stats.runs) - 1

    def test_comparison_is_sorted_by_median(self, report):
        medians = [row["medianLatencyMs"] for row in report.comparison()]
        assert medians == sorted(medians)

    def test_reports_zero_cost_as_a_blocker_not_a_result(self, report):
        findings = " ".join(report.findings())
        assert "not measured by this run" in findings

    def test_findings_do_not_assert_unsupported_causes(self, report):
        """An explanation the run cannot support is worse than none."""
        findings = " ".join(report.findings())
        if "NOT the fastest" in findings:
            assert "artefact" in findings or "not established" in findings

    def test_empty_stats_do_not_crash(self):
        assert BenchmarkReport().findings() == []

    def test_path_stats_handles_no_warm_runs(self):
        stats = PathStats(path="fast", runs=[PathRun("fast", "q", 1.0, 0, 0, 0, cold=True)])
        assert stats.as_dict()["n"] == 0


# ─── Publication gate ────────────────────────────────────────────────────────


def _reports(is_llm: bool, judge: bool):
    routing = RoutingReport(
        predictions=[_prediction(QueryClass.SIMPLE_FACTUAL, QueryClass.SIMPLE_FACTUAL)],
        classifier_is_llm=is_llm,
        classifier_method="llm_few_shot" if is_llm else "heuristic",
    )
    quality = QualityReport(judge_available=judge)
    return routing, quality


class TestPublicationGate:
    def test_heuristic_classifier_blocks_publication(self):
        routing, quality = _reports(is_llm=False, judge=True)
        gate = evaluate_gate(routing, quality, corpora_origin="live")
        assert gate.may_publish is False
        assert any("not the LLM" in b for b in gate.blockers)

    def test_synthetic_corpora_block_publication(self):
        routing, quality = _reports(is_llm=True, judge=True)
        gate = evaluate_gate(routing, quality, corpora_origin="synthetic")
        assert gate.may_publish is False
        assert any("synthetic" in b for b in gate.blockers)

    def test_missing_judge_is_noted_but_does_not_block(self):
        """Retrieval metrics are still real without a judge."""
        routing, quality = _reports(is_llm=True, judge=False)
        gate = evaluate_gate(routing, quality, corpora_origin="live")
        assert gate.may_publish is True
        assert any("judge" in b for b in gate.blockers)

    def test_all_preconditions_met_publishes(self):
        routing, quality = _reports(is_llm=True, judge=True)
        gate = evaluate_gate(routing, quality, corpora_origin="live")
        assert gate.may_publish is True
        assert gate.status == "FINAL"
        assert gate.blockers == []

    def test_status_string(self):
        assert Gate().status == "PROVISIONAL"


class TestReportRendering:
    def _results(self, gate: Gate, origin: str):
        routing, quality = _reports(is_llm=gate.classifier_is_llm, judge=gate.judge_available)
        return EvaluationResults(
            routing=routing, quality=quality, benchmark=BenchmarkReport(),
            gate=gate, corpora_origin=origin,
        )

    def test_provisional_report_leads_with_the_warning(self):
        gate = evaluate_gate(*_reports(False, False), corpora_origin="synthetic")
        markdown = render_markdown(self._results(gate, "synthetic"))
        assert "PROVISIONAL" in markdown.split("## Test set")[0]
        assert "do not test the thesis" in markdown

    def test_provisional_report_lists_every_blocker(self):
        gate = evaluate_gate(*_reports(False, False), corpora_origin="synthetic")
        markdown = render_markdown(self._results(gate, "synthetic"))
        assert "Why this run cannot settle the question" in markdown
        for blocker in gate.blockers:
            assert blocker.split(".")[0].replace("**", "") in markdown

    def test_routing_section_annotates_a_heuristic_run(self):
        gate = evaluate_gate(*_reports(False, True), corpora_origin="live")
        markdown = render_markdown(self._results(gate, "live"))
        assert "heuristic fallback" in markdown

    def test_final_report_has_no_warning_banner(self):
        gate = evaluate_gate(*_reports(True, True), corpora_origin="live")
        markdown = render_markdown(self._results(gate, "live"))
        assert "Status: FINAL" in markdown
        assert "PROVISIONAL" not in markdown

    def test_provisional_report_says_how_to_fix_it(self):
        gate = evaluate_gate(*_reports(False, False), corpora_origin="synthetic")
        markdown = render_markdown(self._results(gate, "synthetic"))
        assert "To produce a FINAL report" in markdown

    def test_limitations_are_always_present(self):
        gate = evaluate_gate(*_reports(True, True), corpora_origin="live")
        markdown = render_markdown(self._results(gate, "live"))
        assert "Honest limitations" in markdown
        assert "220,989" in markdown

    def test_writes_both_artefacts(self, tmp_path):
        gate = evaluate_gate(*_reports(False, False), corpora_origin="synthetic")
        markdown_path, json_path = write_results(
            self._results(gate, "synthetic"), repo_root=tmp_path
        )
        assert markdown_path.exists() and markdown_path.name == "RESULTS.md"
        assert json_path.exists()

        import json

        payload = json.loads(json_path.read_text())
        assert payload["gate"]["status"] == "PROVISIONAL"
