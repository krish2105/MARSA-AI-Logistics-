"""Phase J CLI.

    marsa-classify query "power banks"   # classify or decline, with citations
    marsa-classify curve                 # the abstention trade-off
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from marsa.classify.abstain import DEFAULT_THRESHOLD, decide
from marsa.classify.citations import Suggestion
from marsa.classify.curve import measure
from marsa.classify.evidence import assess, confidence
from marsa.logging import configure

app = typer.Typer(add_completion=False, help="MARSA AI — Phase J grounded classification")
console = Console()


def _retriever():
    from marsa.indexing.cli import _build_retriever

    retriever = _build_retriever("auto", "auto")

    def retrieve(text: str):
        rulings, _ = retriever.retrieve(text, limit=5)
        return rulings

    return retrieve


@app.command()
def query(
    text: str = typer.Argument(..., help="The classification question"),
    threshold: float = typer.Option(DEFAULT_THRESHOLD, help="Abstention threshold"),
) -> None:
    """Classify one query, or decline and show the near misses."""
    configure(level="WARNING", human=True)
    rulings = _retriever()(text)
    signals = assess(text, rulings)
    outcome = decide(text, rulings, threshold=threshold, signals=signals)

    table = Table(title="Evidence", header_style="bold")
    table.add_column("Signal", min_width=20)
    table.add_column("Value", justify="right")
    for name, value in signals.as_dict().items():
        if name in {"notes", "retrieved", "modalSubheading"}:
            continue
        table.add_row(name, "n/a" if value is None else f"{value:.3f}")
    table.add_row("[bold]confidence[/]", f"[bold]{confidence(signals):.3f}[/]")
    console.print(table)

    if isinstance(outcome, Suggestion):
        console.print(f"\n[green]✓ {outcome.subheading}[/]  {outcome.rationale}")
        for citation in outcome.citations:
            console.print(f"  [dim]{citation.ruling_number}[/] {citation.url}")
    else:
        console.print(f"\n[yellow]⊘ {outcome.message}[/]")
        for reason in outcome.reasons:
            console.print(f"  [yellow]•[/] {reason}")
        for citation in outcome.nearest:
            console.print(f"  [dim]{citation.ruling_number}[/] {citation.url}")


@app.command()
def curve(
    shipped: float = typer.Option(DEFAULT_THRESHOLD, help="Threshold to mark as shipped"),
) -> None:
    """Measure accuracy on answered queries against abstention rate."""
    configure(level="WARNING", human=True)
    console.print("[dim]Retrieving once per query, then sweeping the threshold…[/]")
    report = measure(_retriever(), shipped=shipped)

    table = Table(title="Abstention curve", header_style="bold")
    for column in ("τ", "Abstention", "Accuracy", "Answered", "Correct", "Unsupported"):
        table.add_column(column, justify="right")
    for point in report.points:
        marker = " ←" if abs(point.threshold - report.shipped_threshold) < 1e-9 else ""
        table.add_row(
            f"{point.threshold:.2f}{marker}",
            f"{point.abstention_rate:.1%}",
            f"{point.accuracy_on_answered:.1%}",
            str(point.answered),
            str(point.correct),
            f"{point.unsupported_answered}/{point.unsupported_total}",
        )
    console.print(table)

    verdict = "[green]✓ PASS[/]" if report.gate_passes else "[red]✗ FAIL[/]"
    console.print(
        f"\nGate — no confident answer on an unsupported query: {verdict} "
        f"at τ={report.shipped_threshold:.2f}"
    )


if __name__ == "__main__":
    app()
