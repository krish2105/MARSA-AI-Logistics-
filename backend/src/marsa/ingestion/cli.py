"""Phase A ingestion CLI.

    marsa-ingest probe-cross          # verify the CROSS contract before scraping
    marsa-ingest cross                # scrape the ruling subset
    marsa-ingest comtrade             # sweep the trade-flow grid
    marsa-ingest dataco --download    # Kaggle (needs credentials)
    marsa-ingest worldbank            # LPI indicators (+ CPPI from local CSV)
    marsa-ingest fixtures             # synthetic corpora, no network needed
    marsa-ingest status               # what's on disk, and is it real?
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from marsa.config import settings
from marsa.ingestion import fixtures as fx
from marsa.ingestion.comtrade import ComtradeClient, grid_size
from marsa.ingestion.cross import DEFAULT_TERMS, CrossClient
from marsa.ingestion.dataco import DataCoLoadError, download_via_kaggle, find_csv, iter_orders
from marsa.ingestion.fetcher import Fetcher
from marsa.ingestion.schemas import Origin, SourceKind, SourceManifest, write_jsonl
from marsa.ingestion.worldbank import WorldBankClient, load_cppi_csv
from marsa.logging import configure, get_logger

app = typer.Typer(add_completion=False, help="MARSA AI — Phase A data ingestion")
console = Console()
log = get_logger(__name__)

OUTPUTS = {
    SourceKind.CROSS: "cross_rulings.jsonl",
    SourceKind.COMTRADE: "comtrade_flows.jsonl",
    SourceKind.DATACO: "dataco_orders.jsonl",
    SourceKind.WORLDBANK: "worldbank_lpi.jsonl",
}
PORTS_OUTPUT = "worldbank_ports.jsonl"


def _setup(verbose: bool) -> None:
    configure(level="DEBUG" if verbose else settings.log_level, human=True)


def _persist(
    records: Iterable[BaseModel],
    *,
    source: SourceKind,
    origin: Origin,
    filename: str,
    started: datetime,
    parameters: dict[str, Any],
    subset_rationale: str | None = None,
    stats: dict[str, int] | None = None,
    warnings: list[str] | None = None,
) -> SourceManifest:
    """Write a corpus plus its provenance manifest."""
    out_path = settings.processed_dir / filename
    count, digest = write_jsonl(records, out_path)

    manifest = SourceManifest(
        source=source,
        origin=origin,
        record_count=count,
        output_path=str(out_path.relative_to(settings.data_dir.parent)),
        started_at=started,
        completed_at=datetime.now(UTC),
        request_count=(stats or {}).get("requests", 0),
        cache_hits=(stats or {}).get("cache_hits", 0),
        parameters=parameters,
        content_sha256=digest,
        subset_rationale=subset_rationale,
        warnings=warnings or [],
    )
    manifest_path = out_path.with_suffix(".manifest.json")
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    style = "yellow" if origin is Origin.SYNTHETIC else "green"
    console.print(
        f"[{style}]✓[/] {source.value}: [bold]{count:,}[/] records → {out_path.name}"
        + ("  [yellow](SYNTHETIC)[/]" if origin is Origin.SYNTHETIC else "")
    )
    return manifest


def _make_fetcher(rps: float, *, no_cache: bool) -> Fetcher:
    return Fetcher(
        requests_per_second=rps,
        cache_dir=settings.cache_dir,
        timeout=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
        use_cache=not no_cache,
    )


def _guard_network(action: Callable[[], Any]) -> Any:
    """Run a network action, turning egress blocks into an actionable message."""
    try:
        return action()
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        if any(k in text for k in ("connect", "proxy", "403", "resolve", "timed out", "ssl")):
            console.print(
                "[red]✗ Network request failed.[/] The host may be unreachable from this "
                "environment (corporate proxy or egress policy).\n"
                "  Run this command where outbound HTTPS to the source is permitted, "
                "or use [bold]marsa-ingest fixtures[/] to work offline."
            )
            raise typer.Exit(code=2) from exc
        raise


# ─────────────────────────────────────────────────────────────────────────────


@app.command("probe-cross")
def probe_cross(
    term: str = typer.Option("lithium-ion battery", help="Search term to probe with"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Verify the CROSS API contract before committing to a long scrape.

    CROSS has no published API. Run this first: it prints the response keys so a
    shape change is a one-line fix in cross.py rather than a silent empty corpus.
    """
    _setup(verbose)
    with _make_fetcher(settings.cross_requests_per_second, no_cache=True) as fetcher:
        result = _guard_network(lambda: CrossClient(fetcher).probe(term))
    console.print_json(json.dumps(result, default=str))


@app.command()
def cross(
    max_rulings: int = typer.Option(None, help="Cap on rulings to fetch"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Scrape the CBP CROSS ruling subset (fast path corpus)."""
    _setup(verbose)
    started = datetime.now(UTC)
    limit = max_rulings or settings.cross_max_rulings

    with _make_fetcher(settings.cross_requests_per_second, no_cache=no_cache) as fetcher:
        client = CrossClient(fetcher)
        records = _guard_network(lambda: list(client.harvest(max_rulings=limit)))
        stats = fetcher.stats

    _persist(
        records,
        source=SourceKind.CROSS,
        origin=Origin.LIVE,
        filename=OUTPUTS[SourceKind.CROSS],
        started=started,
        parameters={"terms": list(DEFAULT_TERMS), "max_rulings": limit},
        subset_rationale=(
            "A working subset of the ~220,989 CROSS rulings, selected by search terms "
            "covering HS chapters relevant to a plausible Dubai re-export business "
            "(electronics, machinery, textiles, vehicles, furniture). Chosen so the "
            "fast-path corpus overlaps the Comtrade harvest and the two can genuinely "
            "cross-reference. Not a representative sample of the full database."
        ),
        stats=stats,
    )


@app.command()
def comtrade(
    no_cache: bool = typer.Option(False, "--no-cache"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Sweep the UN Comtrade reporter × partner × chapter × year grid."""
    _setup(verbose)
    started = datetime.now(UTC)
    planned = grid_size()
    console.print(f"[dim]Planned requests: {planned} (free preview tier, 500 records each)[/]")

    with _make_fetcher(settings.comtrade_requests_per_second, no_cache=no_cache) as fetcher:
        client = ComtradeClient(fetcher)
        records = _guard_network(lambda: list(client.harvest()))
        stats = fetcher.stats

    _persist(
        records,
        source=SourceKind.COMTRADE,
        origin=Origin.LIVE,
        filename=OUTPUTS[SourceKind.COMTRADE],
        started=started,
        parameters={
            "planned_requests": planned,
            "max_records_per_request": settings.comtrade_max_records,
        },
        subset_rationale=(
            "UN Comtrade's free preview tier caps each response at 500 records and is "
            "rate-limited per IP, so this is a sampled grid — UAE as reporter against "
            "8 major partners across 6 HS chapters and 3 years, both flow directions. "
            "It is not live real-time trade data and not a mirror of Comtrade."
        ),
        stats=stats,
    )


@app.command()
def dataco(
    download: bool = typer.Option(
        False, "--download", help="Fetch from Kaggle (needs credentials)"
    ),
    nrows: int = typer.Option(None, help="Limit rows, for a quick smoke test"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Load DataCo shipment records (agentic path + Phase D ML target)."""
    _setup(verbose)
    started = datetime.now(UTC)
    raw_dir = settings.raw_dir / "dataco"

    csv_path = find_csv(raw_dir)
    if csv_path is None and download:
        try:
            csv_path = _guard_network(lambda: download_via_kaggle(raw_dir))
        except DataCoLoadError as exc:
            console.print(f"[red]✗ {exc}[/]")
            raise typer.Exit(code=2) from exc

    if csv_path is None:
        console.print(
            f"[red]✗ No CSV found in {raw_dir}.[/]\n"
            "  DataCo is a Kaggle dataset and cannot be fetched anonymously. Either:\n"
            "    • set KAGGLE_USERNAME and KAGGLE_KEY in .env, then rerun with --download\n"
            f"    • or download DataCoSupplyChainDataset.csv manually into {raw_dir}/"
        )
        raise typer.Exit(code=2)

    records = list(iter_orders(csv_path, nrows=nrows))
    _persist(
        records,
        source=SourceKind.DATACO,
        origin=Origin.LIVE,
        filename=OUTPUTS[SourceKind.DATACO],
        started=started,
        parameters={"csv": csv_path.name, "nrows": nrows},
        subset_rationale=(
            "DataCo's Late_delivery_risk label reflects one company's historical "
            "operations. The Phase D model demonstrates the technique; it is not a "
            "generalisable prediction of delivery risk for other shippers."
        ),
    )


@app.command()
def worldbank(
    cppi_csv: Path = typer.Option(None, help="Local Container Port Performance Index CSV"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fetch LPI 2.0 indicators, and load CPPI from a local table if provided."""
    _setup(verbose)
    started = datetime.now(UTC)

    with _make_fetcher(2.0, no_cache=no_cache) as fetcher:
        client = WorldBankClient(fetcher)
        records = _guard_network(lambda: list(client.harvest_lpi()))
        stats = fetcher.stats

    _persist(
        records,
        source=SourceKind.WORLDBANK,
        origin=Origin.LIVE,
        filename=OUTPUTS[SourceKind.WORLDBANK],
        started=started,
        parameters={"indicator_api": "api.worldbank.org/v2"},
        subset_rationale=(
            "LPI indicators for the countries on the Dubai re-export corridor, not "
            "all reporting economies."
        ),
        stats=stats,
    )

    if cppi_csv:
        ports = list(load_cppi_csv(cppi_csv))
        _persist(
            ports,
            source=SourceKind.WORLDBANK,
            origin=Origin.LIVE,
            filename=PORTS_OUTPUT,
            started=started,
            parameters={"cppi_csv": str(cppi_csv)},
            subset_rationale="CPPI is published as a report annex, not an API.",
        )
    else:
        console.print(
            "[dim]No --cppi-csv given; skipping port table. CPPI ships as a report "
            "annex (openknowledge.worldbank.org), so it must be supplied locally.[/]"
        )


@app.command()
def fixtures(
    rulings: int = typer.Option(400, help="Synthetic CROSS rulings"),
    orders: int = typer.Option(5000, help="Synthetic DataCo orders"),
    seed: int = typer.Option(42, help="RNG seed — same seed, same corpus"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Generate synthetic corpora so Phases B–F can proceed offline.

    Records are schema-identical to the live ingestors but carry
    origin=SYNTHETIC. They must never produce a reported benchmark figure.
    """
    _setup(verbose)
    started = datetime.now(UTC)
    warn = ["SYNTHETIC DATA — not valid for any reported benchmark result."]

    _persist(
        fx.generate_cross_rulings(rulings, seed=seed),
        source=SourceKind.CROSS, origin=Origin.SYNTHETIC,
        filename=OUTPUTS[SourceKind.CROSS], started=started,
        parameters={"count": rulings, "seed": seed}, warnings=warn,
    )
    _persist(
        fx.generate_comtrade_flows(seed=seed),
        source=SourceKind.COMTRADE, origin=Origin.SYNTHETIC,
        filename=OUTPUTS[SourceKind.COMTRADE], started=started,
        parameters={"seed": seed}, warnings=warn,
    )
    _persist(
        fx.generate_dataco_orders(orders, seed=seed),
        source=SourceKind.DATACO, origin=Origin.SYNTHETIC,
        filename=OUTPUTS[SourceKind.DATACO], started=started,
        parameters={"count": orders, "seed": seed}, warnings=warn,
    )
    _persist(
        fx.generate_country_logistics(seed=seed),
        source=SourceKind.WORLDBANK, origin=Origin.SYNTHETIC,
        filename=OUTPUTS[SourceKind.WORLDBANK], started=started,
        parameters={"seed": seed}, warnings=warn,
    )
    _persist(
        fx.generate_ports(seed=seed),
        source=SourceKind.WORLDBANK, origin=Origin.SYNTHETIC,
        filename=PORTS_OUTPUT, started=started,
        parameters={"seed": seed}, warnings=warn,
    )

    console.print(
        "\n[yellow]These corpora are synthetic.[/] They unblock Phases B–F; "
        "they cannot validate the routing thesis."
    )


@app.command("export-report")
def export_report(
    out: Path = typer.Option(
        Path(__file__).resolve().parents[4] / "frontend/src/data/ingestion-report.json",
        help="Where to write the report the frontend renders",
    ),
) -> None:
    """Export the provenance manifests as a report the frontend can render.

    The manifests are the artefact that makes the project's honesty claims
    checkable. Leaving them in `data/` where nobody looks defeats the purpose,
    so this publishes them into the UI alongside the Route Badge.
    """
    corpora: list[dict[str, Any]] = []
    for path in sorted(settings.processed_dir.glob("*.manifest.json")):
        m = SourceManifest.model_validate_json(path.read_text(encoding="utf-8"))
        corpora.append(
            {
                "name": path.name.replace(".manifest.json", ""),
                "source": m.source.value,
                "origin": m.origin.value,
                "recordCount": m.record_count,
                "requestCount": m.request_count,
                "cacheHits": m.cache_hits,
                "completedAt": m.completed_at.isoformat(),
                "durationSeconds": round(m.duration_seconds, 3),
                "subsetRationale": m.subset_rationale,
                "warnings": m.warnings,
                "sha256": (m.content_sha256 or "")[:16],
                "parameters": m.parameters,
            }
        )

    # Fold in the Phase B index manifest when one exists, so /data shows the
    # whole data lineage — what was ingested *and* what was built from it —
    # rather than making the reader correlate two pages.
    index_manifest: dict[str, Any] | None = None
    index_path = settings.data_dir / "index" / "index.manifest.json"
    if index_path.exists():
        index_manifest = json.loads(index_path.read_text(encoding="utf-8"))

    graph_manifest: dict[str, Any] | None = None
    graph_path = settings.data_dir / "graph" / "graph.manifest.json"
    if graph_path.exists():
        graph_manifest = json.loads(graph_path.read_text(encoding="utf-8"))

    model_card: dict[str, Any] | None = None
    card_path = settings.data_dir / "models" / "model_card.json"
    if card_path.exists():
        model_card = json.loads(card_path.read_text(encoding="utf-8"))

    congestion: list[Any] = []
    congestion_path = settings.data_dir / "models" / "port_congestion.json"
    if congestion_path.exists():
        congestion = json.loads(congestion_path.read_text(encoding="utf-8"))

    # Phase F. Published with its gate attached, never without: the whole point
    # of the gate is that a reader cannot encounter the accuracy figure without
    # also encountering what it was measured over.
    evaluation: dict[str, Any] | None = None
    eval_path = settings.data_dir / "eval" / "results.json"
    if eval_path.exists():
        evaluation = json.loads(eval_path.read_text(encoding="utf-8"))

    report = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "anySynthetic": any(c["origin"] == Origin.SYNTHETIC.value for c in corpora),
        "totalRecords": sum(c["recordCount"] for c in corpora),
        "corpora": corpora,
        "index": index_manifest,
        "graph": graph_manifest,
        "modelCard": model_card,
        "congestion": congestion,
        "evaluation": evaluation,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    console.print(f"[green]✓[/] report written → {out}  ({len(corpora)} corpora)")


@app.command()
def status() -> None:
    """Show what's on disk and, crucially, whether it's real."""
    table = Table(title="Phase A — ingested corpora", header_style="bold")
    table.add_column("Corpus")
    table.add_column("Records", justify="right")
    table.add_column("Origin")
    table.add_column("Retrieved")
    table.add_column("Requests", justify="right")

    manifests = sorted(settings.processed_dir.glob("*.manifest.json"))
    if not manifests:
        console.print(
            "[yellow]No corpora ingested yet.[/]\n"
            "  Offline: [bold]marsa-ingest fixtures[/]\n"
            "  Live:    [bold]marsa-ingest cross | comtrade | dataco | worldbank[/]"
        )
        return

    for path in manifests:
        m = SourceManifest.model_validate_json(path.read_text(encoding="utf-8"))
        synthetic = m.origin is Origin.SYNTHETIC
        table.add_row(
            path.name.replace(".manifest.json", ""),
            f"{m.record_count:,}",
            "[yellow]SYNTHETIC[/]" if synthetic else "[green]live[/]",
            m.completed_at.strftime("%Y-%m-%d %H:%M"),
            str(m.request_count),
        )

    console.print(table)
    if any(
        SourceManifest.model_validate_json(p.read_text()).origin is Origin.SYNTHETIC
        for p in manifests
    ):
        console.print(
            "[yellow]⚠ At least one corpus is synthetic — no benchmark figure "
            "derived from it may be published.[/]"
        )


if __name__ == "__main__":
    app()
