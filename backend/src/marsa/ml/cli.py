"""Phase D ML CLI.

    marsa-ml train                # LogReg vs XGBoost vs LightGBM, temporal split
    marsa-ml train --with-leakage # also quantify the leakage trap
    marsa-ml congestion           # port congestion tiers from LPI + CPPI
    marsa-ml card                 # print the model card
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from marsa.config import settings
from marsa.ingestion.schemas import (
    CountryLogistics,
    DataCoOrder,
    Origin,
    PortPerformance,
    read_jsonl,
)
from marsa.logging import configure, get_logger
from marsa.ml.artifacts import (
    load_model_card,
    save_congestion,
    save_model,
    write_model_card,
)
from marsa.ml.congestion import CongestionTier, score_ports
from marsa.ml.train import spread, summarise, train_and_compare

app = typer.Typer(add_completion=False, help="MARSA AI — Phase D risk models")
console = Console()
log = get_logger(__name__)

TIER_STYLE = {
    CongestionTier.HIGH.value: "risk-high",
    CongestionTier.ELEVATED.value: "yellow",
    CongestionTier.MODERATE.value: "cyan",
    CongestionTier.LOW.value: "green",
}


def _load(filename: str, model: type) -> list:
    path = settings.processed_dir / filename
    if not path.exists():
        console.print(
            f"[red]✗ {filename} not found.[/] Run [bold]marsa-ingest fixtures[/] first."
        )
        raise typer.Exit(code=2)
    return list(read_jsonl(path, model))


def _origin(records: list) -> str:
    if records and records[0].provenance.origin is Origin.SYNTHETIC:
        return "synthetic"
    return "live"


@app.command()
def train(
    with_leakage: bool = typer.Option(
        True, "--with-leakage/--no-leakage",
        help="Also train a deliberately leaky model to quantify the trap",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Train and compare the three candidate models on a temporal split."""
    configure(level="DEBUG" if verbose else "WARNING", human=True)

    orders = _load("dataco_orders.jsonl", DataCoOrder)
    origin = _origin(orders)
    console.print(f"[dim]Loaded {len(orders):,} orders ({origin})[/]\n")

    report, fitted = train_and_compare(
        orders, origin=origin, include_leakage_demo=with_leakage
    )

    # ── Split integrity ────────────────────────────────────────────────────
    split_table = Table(title="Temporal split", header_style="bold")
    split_table.add_column("Slice")
    split_table.add_column("Rows", justify="right")
    split_table.add_column("Date span")
    split_table.add_column("Late rate", justify="right")
    for slice_name in ("train", "valid", "test"):
        split_table.add_row(
            slice_name,
            f"{report.split_sizes[slice_name]:,}",
            report.split_boundaries[slice_name],
            f"{report.base_rates[slice_name]:.3f}",
        )
    console.print(split_table)

    # ── Comparison ─────────────────────────────────────────────────────────
    frame = summarise(report)
    # Six metric columns, not nine: rich truncates every cell to "0.8…" once the
    # table exceeds the terminal width, which defeats the point of a comparison.
    # Accuracy and vs-majority stay in the model card JSON.
    table = Table(title="Model comparison (held-out test)", header_style="bold")
    table.add_column("model", min_width=20, no_wrap=True)
    for column in ("PR-AUC", "lift", "ROC-AUC", "Brier", "F1", "train s"):
        table.add_column(column, justify="right", no_wrap=True)

    for _, row in frame.iterrows():
        table.add_row(
            row["model"],
            f"{row['pr_auc']:.4f}",
            f"{row['lift_over_baseline']:+.4f}",
            f"{row['roc_auc']:.4f}",
            f"{row['brier']:.4f}",
            f"{row['f1']:.4f}",
            f"{row['train_s']:.2f}",
        )
    console.print(table)

    baseline = report.base_rates["test"]
    console.print(
        f"[dim]PR-AUC no-skill baseline on test = base rate = {baseline:.4f}. "
        f"Majority-class accuracy = {max(baseline, 1 - baseline):.4f}.[/]"
    )

    best = report.best
    if best:
        console.print(
            f"\n[green]✓[/] best honest model: [bold]{best.name}[/] "
            f"(PR-AUC {best.metrics['pr_auc']:.4f}, "
            f"{best.metrics['pr_auc_lift']:+.4f} over baseline)"
        )
        save_model(fitted[best.name], best.name, data_dir=settings.data_dir)

    gap = spread(report)
    if gap < 0.02:
        console.print(
            f"[yellow]Spread between best and worst is {gap:.4f}.[/] Three very "
            "different learners landing this close means the signal is mostly a "
            "main effect — the boosted models are not earning their complexity here."
        )

    # ── The leakage trap, quantified ───────────────────────────────────────
    if report.leakage_demo:
        honest = best.metrics["roc_auc"] if best else 0.0
        leaked = report.leakage_demo.metrics["roc_auc"]
        console.print(
            f"\n[yellow]⚠ Leakage demonstration:[/] the same model given "
            f"`days_for_shipping_real` and `delivery_status` scores "
            f"[bold]{leaked:.4f}[/] ROC-AUC versus [bold]{honest:.4f}[/] honestly "
            f"— a {leaked - honest:+.4f} illusion. Those columns encode the "
            "label by construction and are unknown at order time."
        )

    write_model_card(report, data_dir=settings.data_dir)
    console.print(f"[green]✓[/] model card → {settings.data_dir}/models/model_card.json")

    if origin == "synthetic":
        console.print(
            "\n[yellow]⚠ Trained on synthetic data.[/] Every metric above "
            "describes the fixture generator, not real shipping."
        )


@app.command()
def congestion(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Score port congestion from LPI dwell times and CPPI performance."""
    configure(level="DEBUG" if verbose else "WARNING", human=True)

    ports = _load("worldbank_ports.jsonl", PortPerformance)
    countries = _load("worldbank_lpi.jsonl", CountryLogistics)

    scored = score_ports(ports, countries)

    table = Table(title="Port congestion", header_style="bold")
    table.add_column("Port")
    table.add_column("UN/LOCODE")
    table.add_column("Score", justify="right")
    table.add_column("Tier")
    table.add_column("Vessel h", justify="right")
    table.add_column("Dwell d", justify="right")

    for port in scored:
        style = TIER_STYLE.get(port.tier.value, "white")
        components = port.components
        table.add_row(
            port.port_name,
            port.unlocode,
            f"{port.score:.3f}",
            f"[{style}]{port.tier.value}[/]",
            f"{components['vessel_hours']:.2f}" if components["vessel_hours"] is not None else "—",
            f"{components['import_dwell_days']:.2f}"
            if components["import_dwell_days"] is not None
            else "—",
        )
    console.print(table)

    path = save_congestion(scored, data_dir=settings.data_dir)
    console.print(f"[green]✓[/] {len(scored)} ports scored → {path.name}")
    console.print(
        "[dim]Rule-based, not learned: there is no congestion label to train "
        "against, and CPPI rank is an output of the same measurements.[/]"
    )

    # The normalisation anchors are fixed to the real-world range on purpose, so
    # a score means the same thing across runs. The cost is that a port set
    # clustered at one end produces a degenerate tier split — say so rather than
    # re-tuning thresholds until the data looks interesting.
    counts: dict[str, int] = {}
    for port in scored:
        counts[port.tier.value] = counts.get(port.tier.value, 0) + 1
    dominant, dominant_n = max(counts.items(), key=lambda kv: kv[1])
    if scored and dominant_n / len(scored) > 0.8:
        console.print(
            f"[yellow]⚠ {dominant_n} of {len(scored)} ports fall in a single tier "
            f"('{dominant}').[/] The anchors are pinned to the real CPPI range "
            "(12–70 vessel hours), so a set of mostly well-run ports genuinely "
            "scores low. The tiering is not discriminating on this data."
        )


@app.command()
def card() -> None:
    """Print the model card."""
    payload = load_model_card(settings.data_dir)
    if payload is None:
        console.print("[yellow]No model card. Run [bold]marsa-ml train[/].[/]")
        raise typer.Exit(code=0)

    console.print(f"[bold]{payload['task']}[/]\n")
    console.print(f"[dim]Target:[/] {payload['target']}")
    console.print(f"[dim]Split:[/] {payload['validation']['split']}")
    console.print(f"[dim]Features:[/] {payload['features']['count']}\n")

    console.print("[bold]Excluded for leakage[/]")
    for column, reason in payload["excluded_for_leakage"].items():
        console.print(f"  [red]✗[/] {column} — [dim]{reason}[/]")

    console.print("\n[bold]Limitations[/]")
    for item in payload["limitations"]:
        console.print(f"  • {item}")


if __name__ == "__main__":
    app()
