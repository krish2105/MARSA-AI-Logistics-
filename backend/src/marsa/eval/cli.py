"""Phase F evaluation CLI.

    marsa-eval run          # full harness → RESULTS.md
    marsa-eval routing      # routing accuracy only (fast)
    marsa-eval benchmark    # cost/latency only
    marsa-eval dataset      # inspect and validate the labelled set
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from marsa.config import settings
from marsa.eval.benchmark import run_benchmark
from marsa.eval.dataset import LABELLED_QUERIES, dataset_summary, validate_dataset
from marsa.eval.quality import QualityReport, RagasJudge, score_retrieval
from marsa.eval.report import EvaluationResults, evaluate_gate, write_results
from marsa.eval.routing import evaluate_routing
from marsa.logging import configure, get_logger
from marsa.router.classifier import build_classifier

app = typer.Typer(add_completion=False, help="MARSA AI — Phase F evaluation harness")
console = Console()
log = get_logger(__name__)


def _corpora_origin() -> str:
    """Live only if every ingested corpus says so."""
    from marsa.ingestion.schemas import Origin, SourceManifest

    manifests = list(settings.processed_dir.glob("*.manifest.json"))
    if not manifests:
        return "missing"
    for path in manifests:
        manifest = SourceManifest.model_validate_json(path.read_text(encoding="utf-8"))
        if manifest.origin is Origin.SYNTHETIC:
            return "synthetic"
    return "live"


def _print_routing(report) -> None:
    d = report.as_dict()

    console.print(
        f"\n[bold]Routing accuracy: {d['accuracy']:.1%}[/] "
        f"[dim](baseline {d['baselineAccuracy']:.1%}, lift {d['liftOverBaseline']:+.1%})[/]"
    )
    if not d["classifier"]["isLlm"]:
        console.print(
            "[yellow]⚠ Measuring the heuristic fallback, not the few-shot LLM "
            "classifier. These are not comparable.[/]"
        )

    table = Table(title="Per class", header_style="bold")
    table.add_column("Class", min_width=22)
    for column in ("Precision", "Recall", "F1", "n"):
        table.add_column(column, justify="right")
    for name, m in d["perClass"].items():
        table.add_row(
            name, f"{m['precision']:.3f}", f"{m['recall']:.3f}",
            f"{m['f1']:.3f}", str(m["support"]),
        )
    console.print(table)

    matrix = Table(title="Confusion (actual ↓ / predicted →)", header_style="bold")
    matrix.add_column("", min_width=22)
    for name in d["confusion"]:
        matrix.add_column(name[:12], justify="right")
    for actual, row in d["confusion"].items():
        matrix.add_row(actual, *[str(row[p]) for p in d["confusion"]])
    console.print(matrix)

    console.print(f"\n[bold]Error direction:[/] {d['bias']['note']}")
    conf = d["confidence"]
    console.print(
        f"[dim]Confidence separation {conf['separation']:+.3f} "
        f"({conf['meanConfidenceCorrect']:.3f} correct vs "
        f"{conf['meanConfidenceWrong']:.3f} wrong)[/]"
    )


@app.command()
def dataset() -> None:
    """Inspect and validate the labelled routing set."""
    summary = dataset_summary()
    console.print(f"[bold]{summary['total']}[/] labelled queries")
    console.print(f"  by class:      {summary['byClass']}")
    console.print(f"  by difficulty: {summary['byDifficulty']}")

    problems = validate_dataset()
    if problems:
        console.print("\n[red]Validation problems:[/]")
        for problem in problems:
            console.print(f"  ✗ {problem}")
        raise typer.Exit(code=1)
    console.print("\n[green]✓[/] test set is balanced, deduplicated and fully rationalised")


@app.command()
def routing(
    backend: str = typer.Option("auto", help="auto | llm | heuristic"),
) -> None:
    """Measure routing accuracy only."""
    configure(level="WARNING", human=True)
    _print_routing(evaluate_routing(build_classifier(backend)))


@app.command()
def benchmark(
    repeats: int = typer.Option(1, help="Passes over the query set"),
    limit: int = typer.Option(None, help="Cap queries, for a quick run"),
) -> None:
    """Measure per-path cost and latency."""
    configure(level="WARNING", human=True)
    console.print("[dim]Forcing every query down every path…[/]")
    report = run_benchmark(repeats=repeats, limit=limit)
    d = report.as_dict()

    table = Table(title="Per-path cost and latency", header_style="bold")
    table.add_column("Path", min_width=8)
    for column in ("Median", "Mean", "p95", "Cold", "Cost/query", "Sources", "Chars"):
        table.add_column(column, justify="right")
    for row in d["byPath"]:
        table.add_row(
            row["path"],
            f"{row['medianLatencyMs']:.1f}ms",
            f"{row['meanLatencyMs']:.1f}ms",
            f"{row['p95LatencyMs']:.1f}ms",
            f"{row['coldStartMs']:.0f}ms" if row.get("coldStartMs") else "—",
            f"${row['costPerQueryUsd']:.5f}",
            f"{row['meanSources']:.1f}",
            f"{row['meanAnswerChars']:.0f}",
        )
    console.print(table)

    for finding in d["findings"]:
        console.print(f"[yellow]•[/] {finding}")


@app.command()
def run(
    backend: str = typer.Option("auto", help="Classifier backend"),
    repeats: int = typer.Option(1, help="Benchmark passes"),
    limit: int = typer.Option(None, help="Cap benchmark queries"),
) -> None:
    """Run the full harness and write RESULTS.md."""
    configure(level="WARNING", human=True)

    problems = validate_dataset()
    if problems:
        console.print("[red]✗ The test set is invalid; refusing to evaluate:[/]")
        for problem in problems:
            console.print(f"  {problem}")
        raise typer.Exit(code=1)

    console.print("[dim]1/4 routing accuracy…[/]")
    routing_report = evaluate_routing(build_classifier(backend))
    _print_routing(routing_report)

    console.print("\n[dim]2/4 per-path cost and latency…[/]")
    benchmark_report = run_benchmark(repeats=repeats, limit=limit)

    console.print("[dim]3/4 answer quality…[/]")
    quality_report = _run_quality()

    console.print("[dim]4/4 abstention curve…[/]")
    abstention_report = _run_abstention()

    origin = _corpora_origin()
    gate = evaluate_gate(
        routing_report,
        quality_report,
        corpora_origin=origin,
        abstention=abstention_report,
    )

    results = EvaluationResults(
        routing=routing_report,
        quality=quality_report,
        benchmark=benchmark_report,
        gate=gate,
        abstention=abstention_report,
        corpora_origin=origin,
    )
    markdown_path, json_path = write_results(results, repo_root=settings.data_dir.parent)

    console.print()
    if gate.may_publish:
        console.print(f"[green]✓ FINAL[/] results → {markdown_path.name}")
    else:
        console.print(
            f"[yellow]⚠ PROVISIONAL[/] — {len(gate.blockers)} blocker(s) prevent "
            f"this run from settling the question:"
        )
        for blocker in gate.blockers:
            plain = blocker.replace("**", "")
            console.print(f"  [yellow]•[/] {plain.split('.')[0]}.")
        console.print(f"\n  Written anyway → {markdown_path.name} (stamped PROVISIONAL)")

    console.print(f"[dim]  machine-readable → {json_path}[/]")


def _cross_corpus_codes() -> dict[str, list[str]]:
    """Ruling number -> the HTS codes CBP assigned to it."""
    import json

    path = settings.processed_dir / "cross_rulings.jsonl"
    if not path.exists():
        return {}
    out: dict[str, list[str]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            out[row["ruling_number"]] = list(row.get("hts_codes") or [])
    return out


def _graph_relevance(query: str) -> set[str]:
    """Graph nodes the query names, read from the graph rather than the answer.

    Deriving this from the traversal's own output would make recall vacuous —
    a node that should have been reached but was not could never be counted
    against it. Resolving names against the stored graph keeps both precision
    and recall answerable.
    """
    try:
        from marsa.graph.store import load_graph
    except ImportError:
        return set()
    try:
        graph = load_graph(settings.data_dir)
    except Exception:  # noqa: BLE001 — no graph on disk is a normal state
        return set()

    from marsa.eval.relevance import COUNTRY_ALIASES

    low = query.lower()
    aliased = {f"country:{iso3}" for alias, iso3 in COUNTRY_ALIASES.items() if alias in low}

    # The graph path cites node *labels*, so the ground truth is expressed in
    # labels too rather than in the internal `kind:key` node ids.
    wanted: set[str] = set()
    for node, data in graph.nodes(data=True):
        label = str(data.get("label") or "")
        if not label:
            continue
        # Two characters or fewer matches almost anything, so a label only
        # counts as "named" when it is long enough to be a real name.
        spelled_out = len(label) > 2 and label.lower() in low
        if str(node) in aliased or spelled_out:
            wanted.add(label)

    return wanted


def _run_quality() -> QualityReport:
    """Score retrieval per path, and generation quality if a judge exists.

    Retrieval is scored only where relevance is established independently of
    the retriever — from CBP's own code assignments for the fast path, and from
    entity resolution against the stored graph for the graph path. The agentic
    path has no such source, so it is reported unmeasured rather than given a
    number derived from its own output. See `marsa.eval.relevance`.
    """
    from marsa.eval.benchmark import PATH_RUNNERS
    from marsa.eval.relevance import (
        FAST_PATH_RELEVANCE,
        coverage,
        names_a_port,
        relevant_rulings,
    )

    judge = RagasJudge()
    report = QualityReport(
        judge_available=judge.is_available,
        judge_note=(
            "LLM judge available; faithfulness and answer relevance measured."
            if judge.is_available
            else "No LLM judge configured — generation metrics are not measured, not zero."
        ),
        label_coverage=coverage(),
    )
    report.per_path_note["agentic"] = (
        "Not measured. The agentic path composes its own source descriptors "
        "(corridor and order keys) rather than citing documents with stable "
        "identifiers, so there is no relevance ground truth independent of the "
        "path itself. Reported as unmeasured rather than scored against its own "
        "output."
    )

    corpus = _cross_corpus_codes()

    for item in LABELLED_QUERIES:
        runner = PATH_RUNNERS.get(item.expected_path)
        if runner is None:
            continue
        try:
            result = runner(item.query)
        except Exception:  # noqa: BLE001
            continue

        refs = [s.ref for s in result.get("sources", [])]

        if item.expected_path == "fast":
            label = FAST_PATH_RELEVANCE.get(item.query)
            if label is None:
                continue
            if label.unanswerable:
                report.unanswerable_total += 1
                if refs:
                    report.unanswerable_with_sources += 1
                continue
            if not label.is_scored:
                continue
            relevant = relevant_rulings(label, corpus)
        elif item.expected_path == "graph":
            if names_a_port(item.query):
                report.port_queries += 1
                continue
            relevant = _graph_relevance(item.query)
            if not relevant:
                continue
        else:
            continue

        report.per_path_retrieval.setdefault(item.expected_path, []).append(
            score_retrieval(refs, relevant)
        )

        if judge.is_available:
            report.per_path_judge.setdefault(item.expected_path, []).append(
                judge.score(item.query, result.get("answer", ""), "\n".join(refs))
            )

    return report


if __name__ == "__main__":
    app()


def _run_abstention():
    """Measure the Phase J abstention curve, or None if the index is absent.

    A missing fast-path index is a normal state for a checkout that has not run
    `marsa-index build`. It leaves the curve unmeasured rather than failing the
    whole harness, and the gate reports `None` rather than a pass.
    """
    from marsa.classify.curve import measure

    try:
        from marsa.indexing.cli import _build_retriever

        retriever = _build_retriever("auto", "auto")
    except Exception:  # noqa: BLE001
        log.warning("no fast-path index; abstention curve not measured")
        return None

    def retrieve(text: str):
        rulings, _ = retriever.retrieve(text, limit=5)
        return rulings

    return measure(retrieve)
