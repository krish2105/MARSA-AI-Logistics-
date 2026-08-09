"""RESULTS.md generation — and the gate that decides whether it may claim anything.

The spec asks for results published "exactly as measured". Doing that honestly
requires deciding, in code, whether a given run is *entitled* to publish
headline figures at all — because the most damaging failure available here is
not a wrong number, it is a correct number computed over the wrong thing and
presented as if it settled the question.

Three preconditions must hold before a run may publish:

1. **The classifier is the spec's few-shot LLM**, not the heuristic fallback.
   Routing accuracy is the project's central claim; measuring a rule table and
   reporting it as the classifier's accuracy would be straightforwardly false.
2. **The corpora are real**, not synthetic fixtures. A retrieval score over
   generated documents measures the generator.
3. **An LLM judge is available**, or the generation metrics are absent rather
   than zero.

When any precondition fails the report is still written — the pipeline works
and that is worth showing — but stamped PROVISIONAL, with the blockers listed
first and every headline figure annotated with what it actually describes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marsa.eval.benchmark import BenchmarkReport
from marsa.eval.dataset import dataset_summary
from marsa.eval.quality import QualityReport
from marsa.eval.routing import RoutingReport


@dataclass
class Gate:
    """Whether this run may publish headline results."""

    classifier_is_llm: bool = False
    corpora_are_real: bool = False
    judge_available: bool = False
    #: Phase J hard gate: no confident answer on a query the corpus cannot
    #: support. None when the abstention harness did not run.
    abstention_gate: bool | None = None
    blockers: list[str] = field(default_factory=list)

    @property
    def may_publish(self) -> bool:
        # The abstention gate blocks publication when it ran and failed. A run
        # that never measured it is not thereby entitled to publish, but nor is
        # it failing — it is silent, and the blockers list says so.
        if self.abstention_gate is False:
            return False
        return self.classifier_is_llm and self.corpora_are_real

    @property
    def status(self) -> str:
        return "FINAL" if self.may_publish else "PROVISIONAL"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mayPublish": self.may_publish,
            "classifierIsLlm": self.classifier_is_llm,
            "corporaAreReal": self.corpora_are_real,
            "judgeAvailable": self.judge_available,
            "abstentionGate": self.abstention_gate,
            "blockers": self.blockers,
        }


def evaluate_gate(
    routing: RoutingReport,
    quality: QualityReport,
    *,
    corpora_origin: str,
    abstention: Any | None = None,
) -> Gate:
    gate = Gate(
        classifier_is_llm=routing.classifier_is_llm,
        corpora_are_real=corpora_origin == "live",
        judge_available=quality.judge_available,
        abstention_gate=None if abstention is None else abstention.gate_passes,
    )

    if not gate.classifier_is_llm:
        gate.blockers.append(
            "**The classifier is not the LLM.** Routing accuracy below measures a "
            "deterministic rule table, not the few-shot LLM call the spec specifies. "
            "The two are not comparable and this figure must not be reported as the "
            "classifier's accuracy. Set `GROQ_API_KEY` or `GEMINI_API_KEY` and re-run."
        )
    if not gate.corpora_are_real:
        gate.blockers.append(
            "**The corpora are synthetic.** Retrieval and answer-quality figures "
            "describe the fixture generator, not CBP CROSS, UN Comtrade, DataCo or "
            "World Bank data. Run the Phase A ingestors where outbound access is "
            "permitted and re-run."
        )
    if not gate.judge_available:
        gate.blockers.append(
            "**No LLM judge is available.** RAGAS faithfulness and answer relevance "
            "are reported as `not_measured` rather than zero. Context precision and "
            "recall are deterministic and are measured."
        )
    if gate.abstention_gate is False:
        gate.blockers.append(
            "**The abstention gate failed.** At the shipped threshold the system "
            "answers at least one query the corpus cannot support. A confident "
            "answer with nothing behind it is the failure mode Phase J exists to "
            "remove, so this blocks publication on its own."
        )

    return gate


@dataclass
class EvaluationResults:
    routing: RoutingReport
    quality: QualityReport
    benchmark: BenchmarkReport
    gate: Gate
    abstention: Any | None = None
    corpora_origin: str = "unknown"
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, Any]:
        return {
            "generatedAt": self.generated_at.isoformat(),
            "gate": self.gate.as_dict(),
            "dataset": dataset_summary(),
            "routing": self.routing.as_dict(),
            "quality": self.quality.as_dict(),
            "benchmark": self.benchmark.as_dict(),
            "abstention": None if self.abstention is None else self.abstention.as_dict(),
            "corporaOrigin": self.corpora_origin,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Markdown rendering
# ─────────────────────────────────────────────────────────────────────────────


def _banner(gate: Gate) -> str:
    if gate.may_publish:
        return (
            "> **Status: FINAL.** The classifier is the specified few-shot LLM and the\n"
            "> corpora are real, so the figures below are the project's reported results.\n"
        )
    return (
        "> ## ⚠ PROVISIONAL — these numbers do not test the thesis\n"
        ">\n"
        "> This run exercised the full pipeline end to end, which is worth showing.\n"
        "> It does **not** answer the question this project exists to ask, for the\n"
        "> reasons listed immediately below. Every figure here is annotated with what\n"
        "> it actually describes.\n"
    )


def _routing_section(routing: RoutingReport, gate: Gate) -> str:
    d = routing.as_dict()
    lines = ["## Routing accuracy", ""]

    if not gate.classifier_is_llm:
        lines += [
            "> Measuring the **heuristic fallback**, not the few-shot LLM classifier.",
            "> This is a property of a rule table written by hand. It is reported for",
            "> completeness and is not the project's routing-accuracy result.",
            "",
        ]

    lines += [
        f"**{d['accuracy']:.1%}** over {d['n']} labelled queries "
        f"(majority-class baseline {d['baselineAccuracy']:.1%}, "
        f"lift {d['liftOverBaseline']:+.1%}).",
        "",
        "| Class | Precision | Recall | F1 | Support |",
        "|---|---|---|---|---|",
    ]
    for name, m in d["perClass"].items():
        lines.append(
            f"| `{name}` | {m['precision']:.3f} | {m['recall']:.3f} | "
            f"{m['f1']:.3f} | {m['support']} |"
        )

    lines += ["", "### Confusion matrix", "", "| Actual ↓ / Predicted → | " + " | ".join(
        f"`{c}`" for c in d["confusion"]
    ) + " |", "|---" * (len(d["confusion"]) + 1) + "|"]
    for actual, row in d["confusion"].items():
        cells = " | ".join(str(row[p]) for p in d["confusion"])
        lines.append(f"| `{actual}` | {cells} |")

    lines += ["", "### Accuracy by difficulty", "",
              "| Difficulty | Accuracy | n |", "|---|---|---|"]
    for level, m in d["byDifficulty"].items():
        lines.append(f"| {level} | {m['accuracy']:.1%} | {m['count']} |")

    bias = d["bias"]
    conf = d["confidence"]
    lines += [
        "",
        "### Error direction",
        "",
        bias["note"],
        "",
        f"Confidence separates signal from noise by {conf['separation']:+.3f} "
        f"(mean {conf['meanConfidenceCorrect']:.3f} when correct vs "
        f"{conf['meanConfidenceWrong']:.3f} when wrong).",
    ]

    if d["worstCases"]:
        lines += ["", "### Most confident mistakes", "",
                  "| Query | Expected | Predicted | Confidence |", "|---|---|---|---|"]
        for case in d["worstCases"][:5]:
            lines.append(
                f"| {case['query'][:70]} | `{case['expected']}` | "
                f"`{case['predicted']}` | {case['confidence']:.2f} |"
            )

    return "\n".join(lines)


def _benchmark_section(benchmark: BenchmarkReport, gate: Gate) -> str:
    d = benchmark.as_dict()
    lines = ["## Cost and latency per path", ""]

    if not gate.corpora_are_real:
        lines += [
            "> Latency is **real** — the paths genuinely do this much work. It is",
            "> measured over synthetic corpora at fixture scale, so it will not",
            "> transfer to a full-size corpus, and cost is $0 because no model ran.",
            "",
        ]

    lines += [
        f"Every one of {d['queriesRun']} queries was forced down all three paths, "
        "so each row is a genuine counterfactual rather than a measurement of "
        "whichever path the router happened to choose.",
        "",
        "| Path | Median | Mean | p95 | Cold start | Cost/query | Sources | Answer chars |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in d["byPath"]:
        cold = f"{row['coldStartMs']:.0f}ms" if row.get("coldStartMs") else "—"
        lines.append(
            f"| `{row['path']}` | {row['medianLatencyMs']:.1f}ms | "
            f"{row['meanLatencyMs']:.1f}ms | {row['p95LatencyMs']:.1f}ms | {cold} | "
            f"${row['costPerQueryUsd']:.5f} | {row['meanSources']:.1f} | "
            f"{row['meanAnswerChars']:.0f} |"
        )

    if d["findings"]:
        lines += ["", "### What the numbers show", ""]
        lines += [f"- {f}" for f in d["findings"]]

    return "\n".join(lines)


def _quality_section(quality: QualityReport, gate: Gate) -> str:
    d = quality.as_dict()
    lines = ["## Answer quality (RAGAS)", ""]

    if not gate.judge_available:
        lines += [
            "> Faithfulness and answer relevance require an LLM judge and are",
            "> reported as `not_measured`. **That is not zero.** Context precision",
            "> and recall are deterministic and are measured.",
            "",
        ]

    cov = d.get("labelCoverage") or {}
    if cov:
        lines += [
            f"Relevance is scored on **{cov.get('scored', 0)} of "
            f"{cov.get('total', 0)}** fast-path queries. A ruling counts as "
            "relevant when **CBP** assigned it a code under the provision the "
            "question asks about — the labels are read off the source "
            "authority, not off what the retriever returned. "
            f"{cov.get('unanswerable', 0)} queries have no answering document "
            "in the corpus and are reported separately below; "
            f"{cov.get('unlabelled', 0)} admit no defensible single provision "
            "and are excluded rather than labelled generously.",
            "",
        ]

    if not d["byPath"]:
        lines.append("_No per-path quality scores were produced by this run._")
    else:
        lines += [
            "| Path | Context precision | Context recall | Recall ceiling | "
            "Faithfulness | Answer relevance | n |",
            "|---|---|---|---|---|---|---|",
        ]
        for path, m in d["byPath"].items():
            lines.append(
                f"| `{path}` | {m['contextPrecision']:.3f} | {m['contextRecall']:.3f} | "
                f"{m.get('recallCeiling', 0):.3f} | {m['faithfulness']} | "
                f"{m['answerRelevance']} | {m['n']} |"
            )
        fast = d["byPath"].get("fast")
        if fast:
            lines += [
                "",
                f"**Read recall against the ceiling, not against 1.0.** The path "
                f"returns {fast.get('meanRetrieved', 0):.1f} sources against a mean "
                f"of {fast.get('meanRelevant', 0):.1f} relevant rulings, so the best "
                f"recall attainable at this *k* is "
                f"{fast.get('recallCeiling', 0):.3f}. Measured recall is "
                f"{fast['contextRecall']:.3f} — that is the number to judge, and it "
                "reflects a deliberate choice to show five citations a user can "
                "actually read rather than to maximise a recall figure.",
            ]

    for path, note in (d.get("unmeasuredPaths") or {}).items():
        lines += ["", f"**`{path}`** — {note}"]

    unans = d.get("unanswerable") or {}
    if unans.get("total"):
        total, answered = unans["total"], unans["answeredAnyway"]
        lines += [
            "",
            "### Questions the corpus cannot answer",
            "",
            f"{total} of the fast-path queries have no supporting document — two "
            "name rulings that were never ingested, three ask about provisions no "
            "ingested ruling classifies under. The correct behaviour is to say so.",
            "",
            f"**The fast path returned sources for {answered} of {total}.** It has "
            "no way to decline: retrieval always returns its top *k*, and *k* "
            "nearest neighbours exist in any non-empty index regardless of whether "
            "any of them bear on the question. This is the gap Phase J's "
            "abstention head is built to close, and these queries are its test set.",
        ]

    ports = d.get("portQueries") or 0
    if ports:
        lines += [
            "",
            "### The graph has no port layer",
            "",
            f"**{ports} of the 20 relationship queries name a port** — Jebel Ali, "
            "Singapore, Shanghai, Rotterdam, Nhava Sheva, Los Angeles, Busan, "
            "Tanger Med, Khalifa Port. The supply graph contains no port nodes at "
            "all: 38 countries keyed by ISO3, HTS codes, rulings, products and "
            "categories, and nothing else.",
            "",
            "The cause is upstream. Port performance comes from the Container Port "
            "Performance Index, which the World Bank publishes as a report annex "
            "rather than through an API, so `marsa-ingest worldbank` cannot fetch "
            "it and the ports corpus was never written. Every port question "
            "therefore resolves against countries or falls through to nothing.",
            "",
            "This is the single largest gap between what the graph path claims and "
            "what it can currently do, and no amount of retrieval tuning closes "
            "it — it needs the CPPI annex supplied as a local CSV via "
            "`marsa-ingest worldbank --cppi-csv`. These queries are excluded from "
            "the retrieval figures above rather than scored as failures, because "
            "what they measure is a missing corpus, not a bad traversal.",
        ]

    return "\n".join(lines)


def _abstention_section(abstention: Any) -> str:
    """Phase J — the trade-off, reported as a shape rather than a number."""
    d = abstention.as_dict()
    shipped = d["atShippedThreshold"]
    ds = d["dataset"]

    lines = [
        "## Abstention (Phase J)",
        "",
        "The question is not how often the system is right. It is how much "
        "accuracy a given willingness to decline buys, and whether it ever "
        "answers something it has no business answering.",
        "",
        f"Measured over {ds['scored']} queries with a target provision and "
        f"{ds['unanswerable']} the corpus cannot answer; {ds['excluded']} are "
        "excluded for having no defensible target. Correctness is judged "
        "against CBP's own code assignments, and on an unanswerable query the "
        "only correct behaviour is to decline.",
        "",
        "| τ | Abstention rate | Accuracy on answered | Answered | Unsupported answered |",
        "|---|---|---|---|---|",
    ]
    for point in d["curve"]:
        mark = " ←" if point["threshold"] == d["shippedThreshold"] else ""
        lines.append(
            f"| {point['threshold']:.2f}{mark} | {point['abstentionRate']:.1%} | "
            f"{point['accuracyOnAnswered']:.1%} | {point['answered']} | "
            f"{point['unsupportedAnswered']}/{point['unsupportedTotal']} |"
        )

    verdict = "**PASS**" if d["gatePasses"] else "**FAIL**"
    lines += [
        "",
        "### Gate — no confident answer on a query the corpus cannot support",
        "",
        f"{verdict} at the shipped threshold τ={d['shippedThreshold']:.2f}.",
    ]
    if shipped:
        lines += [
            "",
            f"At that point the system declines {shipped['abstentionRate']:.1%} of "
            f"the set and is right {shipped['accuracyOnAnswered']:.1%} of the time "
            f"on what it does answer ({shipped['correct']} of "
            f"{shipped['answered']}).",
        ]

    lines += [
        "",
        "Read the left end of the table as the system before Phase J: it answers "
        "nearly everything, including every query with no answering document, and "
        "accuracy on what it answers is correspondingly poor. The threshold buys "
        "accuracy by declining, and the table is what that costs.",
        "",
        "Two honest caveats. The weights behind the confidence score are fixed "
        "round numbers, not fitted — fitting them on these same queries would "
        "report the fit rather than the behaviour. And thirteen scoreable queries "
        "is a small set: the shape of this curve is the finding, and no single "
        "cell in it should be quoted on its own.",
    ]
    return "\n".join(lines)

def render_markdown(results: EvaluationResults) -> str:
    gate = results.gate
    summary = dataset_summary()

    parts = [
        "# RESULTS",
        "",
        f"_Generated {results.generated_at.strftime('%Y-%m-%d %H:%M UTC')} · "
        f"status **{gate.status}**_",
        "",
        _banner(gate),
        "",
    ]

    if gate.blockers:
        parts += ["## Why this run cannot settle the question", ""]
        parts += [f"{i}. {b}" for i, b in enumerate(gate.blockers, 1)]
        parts += [""]

    parts += [
        "## Test set",
        "",
        f"{summary['total']} hand-labelled queries, "
        + ", ".join(f"{n} `{c}`" for c, n in summary["byClass"].items())
        + ".",
        "",
        "Difficulty: "
        + ", ".join(f"{n} {d}" for d, n in summary["byDifficulty"].items())
        + ". The ambiguous cases are deliberate — a set of clear-cut queries "
        "cannot surface the systematic misrouting this section exists to report.",
        "",
        _routing_section(results.routing, gate),
        "",
        _benchmark_section(results.benchmark, gate),
        "",
        _quality_section(results.quality, gate),
        "",
    ]

    if results.abstention is not None:
        parts += [_abstention_section(results.abstention), ""]

    parts += [
        "## Honest limitations",
        "",
        "- The complexity classifier is a single prompt (or, in this run, a rule "
        "table) rather than a trained model. Its accuracy is reported as measured.",
        "- The CROSS corpus is a subset, not the full ~220,989 rulings.",
        "- UN Comtrade's free preview tier is rate-limited and record-capped; the "
        "trade data is a sample, not a mirror.",
        "- DataCo's `Late_delivery_risk` reflects one company's operations. The "
        "Phase D model demonstrates the technique, not a general prediction.",
        "- The supply graph joins four corpora that share no keys. Every bridging "
        "assumption is marked `inferred` and listed on the `/data` page.",
        "- The graph has no port layer. CPPI is a report annex rather than an API, "
        "so it was never ingested, and the relationship queries that name a port "
        "cannot be served at all. This is a missing corpus, not a weak traversal.",
        "- Retrieval relevance is labelled in two steps: a hand judgement of which "
        "tariff provision each question asks about, then a mechanical lookup of "
        "which rulings CBP assigned to it. The first step is a reading of the "
        "question and is recorded with a rationale per query in "
        "`marsa/eval/relevance.py`; disagree with a line, not with the metric.",
        "- Context recall is bounded by *k*. The ceiling is reported next to the "
        "measured value so the two are not confused.",
        "",
    ]

    if not gate.may_publish:
        parts += [
            "---",
            "",
            "**To produce a FINAL report:** configure an LLM provider "
            "(`GROQ_API_KEY` / `GEMINI_API_KEY`), run the Phase A ingestors against "
            "the live sources, rebuild the index and graph, then re-run "
            "`marsa-eval run`. No code changes are required.",
            "",
        ]

    return "\n".join(parts)


def write_results(results: EvaluationResults, *, repo_root: Path) -> tuple[Path, Path]:
    """Write RESULTS.md and the machine-readable sidecar.

    RESULTS.md belongs at the repo root — it is a published document. The
    sidecar belongs under the configured data directory, and is resolved from
    `settings.data_dir` rather than rebuilt as `repo_root / "data"`. Those two
    agree only while the data directory is literally named `data`; set DATA_DIR
    anywhere else and the reconstructed form writes the sidecar outside it,
    where the next reader looks in the configured location and finds nothing.
    """
    from marsa.config import settings

    markdown_path = repo_root / "RESULTS.md"
    markdown_path.write_text(render_markdown(results), encoding="utf-8")

    json_path = settings.data_dir / "eval" / "results.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(results.as_dict(), indent=2) + "\n", encoding="utf-8")

    return markdown_path, json_path
