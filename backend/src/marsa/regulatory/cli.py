"""Phase G CLI.

    marsa-reg probe    # decision gate G1 — is the instrument data really there?
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from marsa.logging import configure
from marsa.regulatory.probe import REQUIRED, run_gate

app = typer.Typer(add_completion=False, help="MARSA AI — regulatory knowledge layer")
console = Console()

_VERDICT_STYLE = {
    "COMPLETE": "green", "PARTIAL": "yellow",
    "INSUFFICIENT": "red", "UNREACHABLE": "red",
    "PASS": "green", "MARGINAL": "yellow", "STOP": "red", "NOT_EVALUATED": "yellow",
}


@app.callback()
def main() -> None:
    """Regulatory knowledge layer.

    Present so `probe` stays a subcommand: Typer promotes a lone command to the
    app itself, which would make `marsa-reg probe` an error today and a
    breaking change once Phase G adds `ingest` and `asof`.
    """


@app.command()
def probe(
    no_cache: bool = typer.Option(False, "--no-cache", help="Ignore the HTTP cache"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Run decision gate G1 against the four public regulatory sources.

    Answers one question before Phase G is worth building: do these sources
    expose an instrument that can be pinned to a date, with a scope and a
    citable URL? Fetches a small sample; ingests nothing.
    """
    configure(level="WARNING", human=True)
    result = run_gate(use_cache=not no_cache)

    if json_out:
        console.print_json(json.dumps(result.as_dict()))
        raise typer.Exit(code=0 if result.verdict == "PASS" else 1)

    table = Table(title="G1 — regulatory source probe", header_style="bold")
    table.add_column("Source", min_width=22)
    for column in REQUIRED:
        table.add_column(column, justify="center")
    table.add_column("Verdict", justify="right")

    for p in result.probes:
        cells = ["✓" if p.fields.get(k) else "—" for k in REQUIRED]
        table.add_row(
            p.name, *cells,
            f"[{_VERDICT_STYLE.get(p.verdict, 'white')}]{p.verdict}[/]",
        )
    console.print(table)

    for p in result.probes:
        if p.error:
            console.print(f"\n[red]✗ {p.name}[/] — {p.error}")
        for note in p.notes:
            console.print(f"[dim]  · {p.name}: {note}[/]")

    style = _VERDICT_STYLE.get(result.verdict, "white")
    console.print(
        f"\n[bold {style}]G1: {result.verdict}[/] — field coverage "
        f"{result.coverage:.0%} (pass ≥{result.pass_at:.0%}, stop <{result.stop_below:.0%}), "
        f"{len(result.reachable)}/{len(result.probes)} sources reachable"
    )
    console.print(f"[bold]Action:[/] {result.action}")

    raise typer.Exit(code=0 if result.verdict == "PASS" else 1)


if __name__ == "__main__":
    app()
