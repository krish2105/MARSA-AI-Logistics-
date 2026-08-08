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
    blockers: list[str] = field(default_factory=list)

    @property
    def may_publish(self) -> bool:
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
            "blockers": self.blockers,
        }


def evaluate_gate(
    routing: RoutingReport, quality: QualityReport, *, corpora_origin: str
) -> Gate:
    gate = Gate(
        classifier_is_llm=routing.classifier_is_llm,
        corpora_are_real=corpora_origin == "live",
        judge_available=quality.judge_available,
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

    return gate


@dataclass
class EvaluationResults:
    routing: RoutingReport
    quality: QualityReport
    benchmark: BenchmarkReport
    gate: Gate
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

    if not d["byPath"]:
        lines.append("_No per-path quality scores were produced by this run._")
        return "\n".join(lines)

    lines += [
        "| Path | Context precision | Context recall | Faithfulness | Answer relevance |",
        "|---|---|---|---|---|",
    ]
    for path, m in d["byPath"].items():
        lines.append(
            f"| `{path}` | {m['contextPrecision']:.3f} | {m['contextRecall']:.3f} | "
            f"{m['faithfulness']} | {m['answerRelevance']} |"
        )

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
