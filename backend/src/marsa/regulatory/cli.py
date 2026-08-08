"""Phase G CLI.

    marsa-reg probe    # decision gate G1 — is the instrument data really there?
"""

from __future__ import annotations

import json
import pathlib

import typer
from rich.console import Console
from rich.table import Table

from marsa.config import settings
from marsa.ingestion.fetcher import Fetcher
from marsa.logging import configure
from marsa.regulatory import fixtures as fx
from marsa.regulatory.ingest import INGESTORS
from marsa.regulatory.probe import REQUIRED, run_gate
from marsa.regulatory.store import InstrumentStore

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



def _store_path() -> pathlib.Path:
    return settings.data_dir / "regulatory" / "instruments.json"


@app.command()
def fixtures(
    contradiction: bool = typer.Option(
        False, "--contradiction", help="Inject a pair that cannot both be right"
    ),
) -> None:
    """Generate synthetic instruments so Phase G runs without the sources.

    Every one carries origin=SYNTHETIC. No figure derived from them may be
    published — the same rule as Phase A, enforced by a test.
    """
    configure(level="WARNING", human=True)
    store = InstrumentStore()
    store.extend(fx.generate_instruments(with_contradiction=contradiction))
    store.save(_store_path())
    console.print(f"[green]OK[/] {len(store.instruments)} synthetic instruments written")
    console.print(
        "[yellow]These are synthetic.[/] They exercise supersession, stacking and "
        "caps; they cannot validate a duty figure."
    )


@app.command()
def ingest(
    source: str = typer.Option("all", help="all, or one source name"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Ingest real instruments. Needs outbound access to the four sources."""
    configure(level="WARNING", human=True)
    wanted = list(INGESTORS) if source == "all" else [source]
    unknown = [s for s in wanted if s not in INGESTORS]
    if unknown:
        console.print(f"[red]unknown source(s): {unknown}[/]")
        raise typer.Exit(code=2)

    store = InstrumentStore.load(_store_path())
    failures: list[str] = []
    with Fetcher(
        requests_per_second=1.0, cache_dir=settings.cache_dir, use_cache=not no_cache
    ) as fetcher:
        for name in wanted:
            try:
                found = INGESTORS[name](fetcher)
            except Exception as exc:  # noqa: BLE001 - a dead source is a datum
                failures.append(name)
                console.print(f"[red]x[/] {name} - {str(exc)[:150]}")
                continue
            store.extend(found)
            console.print(f"[green]OK[/] {name} - {len(found)} instruments")

    store.save(_store_path())
    if failures:
        console.print(
            f"\n[yellow]{len(failures)} source(s) failed.[/] The set is incomplete; "
            "`asof` answers from what survived, which is not the same thing."
        )
        raise typer.Exit(code=1)


@app.command()
def asof(
    on: str = typer.Argument(..., help="Date, YYYY-MM-DD"),
    hts: str = typer.Option(None, help="HTS code, e.g. 7326.90.86"),
    origin: str = typer.Option(None, help="Origin country, e.g. CN"),
) -> None:
    """What was in force on a date - the one question Phase G exists to answer."""
    configure(level="WARNING", human=True)
    from datetime import date as _date

    store = InstrumentStore.load(_store_path())
    if not store.instruments:
        console.print("[yellow]No instruments. Run `marsa-reg fixtures` or `ingest`.[/]")
        raise typer.Exit(code=1)

    report = store.asof(_date.fromisoformat(on), hts=hts, origin=origin)

    table = Table(title=f"In force on {on}", header_style="bold")
    for column in ("Instrument", "Kind", "Effect", "From", "To"):
        table.add_column(column)
    for i in report.effective:
        rate = (
            f"{i.effect.rate_percent:.1f}%" + (" (+)" if i.effect.additive else "")
            if i.effect.rate_percent is not None
            else i.effect.kind.value
        )
        table.add_row(
            i.id, i.kind.value, rate,
            i.effective_from.isoformat(),
            i.effective_to.isoformat() if i.effective_to else "-",
        )
    console.print(table)

    if report.superseded:
        console.print(f"[dim]superseded and excluded: {', '.join(report.superseded)}[/]")
    for c in report.contradictions:
        console.print(
            f"[red]contradiction:[/] {c.left} ({c.left_rate}%) vs "
            f"{c.right} ({c.right_rate}%) - neither was chosen. {c.reason}"
        )
    if report.inferred_dates:
        console.print(
            f"[yellow]inferred effective dates:[/] {', '.join(report.inferred_dates)} "
            "- publication is not commencement, so this window may be wrong."
        )
    if not report.is_trustworthy:
        console.print(
            "\n[yellow]This resolution cannot carry a published figure.[/] "
            "Resolve the contradictions or confirm the dates first."
        )


@app.command()
def staleness() -> None:
    """How old the instrument set is - every answer has to disclose this."""
    configure(level="WARNING", human=True)
    store = InstrumentStore.load(_store_path())
    d = store.staleness()
    for key, value in d.items():
        console.print(f"  {key:22s} {value}")
    if d["isStale"]:
        console.print(
            "\n[yellow]Stale.[/] Section 232 changed twice in 2026; a set this old "
            "may cite a superseded rate."
        )
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
