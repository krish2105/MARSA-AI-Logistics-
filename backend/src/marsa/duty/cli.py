"""Phase H CLI.

    marsa-duty quote --hts 7326.90.86 --origin CN --value 40000 --on 2026-09-01
    marsa-duty detect "What duty applies to 7326.90.86 from China?"
"""

from __future__ import annotations

from datetime import date as _date

import typer
from rich.console import Console

from marsa.config import settings
from marsa.duty.detect import detect as detect_duty
from marsa.duty.engine import quote as compute_quote
from marsa.duty.path import render
from marsa.logging import configure
from marsa.regulatory.store import InstrumentStore

app = typer.Typer(add_completion=False, help="MARSA AI — layered duty engine")
console = Console()


@app.callback()
def main() -> None:
    """Layered duty engine."""


@app.command()
def quote(
    hts: str = typer.Option(..., help="HTS code, e.g. 7326.90.86"),
    origin: str = typer.Option(..., help="Origin country, ISO-2"),
    value: float = typer.Option(10000.0, help="Customs value"),
    on: str = typer.Option(None, help="Entry date, YYYY-MM-DD (default: today)"),
    us_content: float = typer.Option(0.0, help="US content %, for USMCA origins"),
) -> None:
    """Compute the layered duty for one entry, or refuse and say why."""
    configure(level="WARNING", human=True)
    store = InstrumentStore.load(settings.data_dir / "regulatory" / "instruments.json")
    if not store.instruments:
        console.print("[yellow]No instruments. Run `marsa-reg fixtures` first.[/]")
        raise typer.Exit(code=1)

    result = compute_quote(
        store,
        hts=hts,
        origin=origin,
        customs_value=value,
        on=_date.fromisoformat(on) if on else _date.today(),
        us_content_percent=us_content,
    )
    console.print(render(result))
    if store.any_synthetic:
        console.print(
            "\n[yellow]Instruments are synthetic.[/] The arithmetic is exact; the "
            "rates are not real."
        )
    raise typer.Exit(code=0 if result.answered else 1)


@app.command()
def detect(query: str = typer.Argument(..., help="A query to classify")) -> None:
    """Show whether the deterministic pre-filter treats this as a duty question."""
    configure(level="WARNING", human=True)
    found = detect_duty(query)
    verdict = "[green]duty question[/]" if found.is_duty_question else "[dim]not a duty question[/]"
    console.print(f"{verdict}  (hts: {found.hts or '—'})")
    for reason in found.reasons:
        console.print(f"  · {reason}")
