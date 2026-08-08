"""Phase I CLI.

    marsa-screen suppliers "Sunrise Textile Mfg" "Acme Ltd"
    marsa-screen suppliers --file suppliers.txt --hts 7208.10.00
"""

from __future__ import annotations

import json
import pathlib
from datetime import date as _date

import typer
from rich.console import Console
from rich.table import Table

from marsa.config import settings
from marsa.logging import configure
from marsa.regulatory.store import InstrumentStore
from marsa.screening.engine import Finding, screen

app = typer.Typer(add_completion=False, help="MARSA AI — supplier screening")
console = Console()

# Module-level singletons: ruff flags a typer call evaluated in a default.
_NAMES = typer.Argument(None, help="Supplier names")
_FILE = typer.Option(None, help="File with one supplier per line")
_HTS = typer.Option(None, help="HTS codes to check against goods scopes")
_ON = typer.Option(None, help="Screen as-of this date, YYYY-MM-DD")
_JSON = typer.Option(False, "--json", help="Machine-readable output")
_OUT = typer.Option(None, help="Where to write the frontend payload")

_STYLE = {
    Finding.HIT: "red",
    Finding.POSSIBLE: "yellow",
    # Deliberately not green. A green tick reads as a clearance, which is
    # exactly the claim this tool must never make.
    Finding.NO_EVIDENCE_FOUND: "dim",
}

_LABEL = {
    Finding.HIT: "HIT",
    Finding.POSSIBLE: "POSSIBLE",
    Finding.NO_EVIDENCE_FOUND: "no evidence found",
}


@app.callback()
def main() -> None:
    """Supplier screening."""


@app.command()
def suppliers(
    names: list[str] = _NAMES,
    file: pathlib.Path = _FILE,
    hts: list[str] = _HTS,
    on: str = _ON,
    json_out: bool = _JSON,
) -> None:
    """Screen suppliers against entity listings and codes against goods scopes."""
    configure(level="WARNING", human=True)

    wanted = list(names or [])
    if file:
        wanted += [ln.strip() for ln in file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not wanted:
        console.print("[red]No suppliers given.[/] Pass names or --file.")
        raise typer.Exit(code=2)

    store = InstrumentStore.load(settings.data_dir / "regulatory" / "instruments.json")
    report = screen(
        store,
        suppliers=wanted,
        hts_codes=list(hts or []),
        on=_date.fromisoformat(on) if on else None,
    )

    if json_out:
        console.print_json(json.dumps(report.as_dict()))
        raise typer.Exit(code=1 if report.hits else 0)

    table = Table(title=f"Screening — {report.on.isoformat()}", header_style="bold")
    table.add_column("Supplier", min_width=28)
    table.add_column("Finding")
    table.add_column("Best match")
    table.add_column("Score", justify="right")
    for result in report.suppliers:
        finding = result.finding
        table.add_row(
            result.supplier,
            f"[{_STYLE[finding]}]{_LABEL[finding]}[/]",
            result.matches[0].listed_name if result.matches else "—",
            f"{result.best_score:.0%}" if result.matches else "—",
        )
    console.print(table)

    for result in report.suppliers:
        for note in result.notes:
            if result.finding is not Finding.NO_EVIDENCE_FOUND:
                console.print(f"[dim]  · {result.supplier}: {note}[/]")

    if report.goods:
        goods = Table(title="Goods scope", header_style="bold")
        goods.add_column("HTS")
        goods.add_column("Finding")
        goods.add_column("Instruments")
        for item in report.goods:
            goods.add_row(
                item.hts,
                f"[{_STYLE[item.finding]}]{_LABEL[item.finding]}[/]",
                ", ".join(item.in_scope) or "—",
            )
        console.print(goods)
        for item in report.goods:
            for note in item.notes:
                console.print(f"[dim]  · {note}[/]")

    for warning in report.warnings:
        console.print(f"[yellow]![/] {warning}")
    console.print(f"\n[dim]{report.disclaimer}[/]")

    raise typer.Exit(code=1 if report.hits else 0)


@app.command("export-report")
def export_report(
    out: pathlib.Path = _OUT,
    on: str = _ON,
) -> None:
    """Publish a worked screening for the /screening page."""
    configure(level="WARNING", human=True)
    target = out or (
        pathlib.Path(__file__).resolve().parents[4] / "frontend/src/data/screening.json"
    )
    store = InstrumentStore.load(settings.data_dir / "regulatory" / "instruments.json")

    # A demo list chosen to exercise all three findings, including the near-miss
    # that must NOT match — a screening page showing only hits teaches nothing
    # about where the tool is uncertain.
    demo = [
        "Sunrise Textile Manufacturing Co., Ltd.",
        "SUNRISE TEXTILE MFG",
        "Northern Silica Materials",
        "Southern Silica Materials Group",
        "Jebel Ali Freight Forwarding LLC",
    ]
    report = screen(
        store,
        suppliers=demo,
        hts_codes=["7208.10.00", "8507.60.00"],
        on=_date.fromisoformat(on) if on else None,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8")
    console.print(f"[green]OK[/] screening report -> {target}")
