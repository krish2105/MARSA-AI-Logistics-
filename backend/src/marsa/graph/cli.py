"""Phase C graph CLI.

    marsa-graph build              # assemble the supply graph from all corpora
    marsa-graph query "..."        # resolve → traverse → narrate
    marsa-graph stats              # composition, connectivity, inferred share
    marsa-graph node <id>          # inspect one node and its edges
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from marsa.config import settings
from marsa.graph.build import build_graph, compute_stats, dominant_origin
from marsa.graph.narrate import narrate
from marsa.graph.resolve import EntityResolver
from marsa.graph.schema import BRIDGE_RULES, NodeKind, node_id
from marsa.graph.store import load_graph, load_manifest, save_graph
from marsa.graph.traverse import DEFAULT_HOPS, alternative_routes, k_hop_subgraph
from marsa.ingestion.schemas import (
    ComtradeFlow,
    CountryLogistics,
    CrossRuling,
    DataCoOrder,
    PortPerformance,
    read_jsonl,
)
from marsa.logging import configure, get_logger

app = typer.Typer(add_completion=False, help="MARSA AI — Phase C supply graph")
console = Console()
log = get_logger(__name__)

CORPORA = {
    "cross_rulings.jsonl": CrossRuling,
    "comtrade_flows.jsonl": ComtradeFlow,
    "dataco_orders.jsonl": DataCoOrder,
    "worldbank_lpi.jsonl": CountryLogistics,
    "worldbank_ports.jsonl": PortPerformance,
}


def _load(filename: str, model: type):
    path = settings.processed_dir / filename
    if not path.exists():
        console.print(f"[yellow]○[/] {filename} not found — skipping")
        return []
    records = list(read_jsonl(path, model))
    console.print(f"[dim]  loaded {len(records):,} from {filename}[/]")
    return records


@app.command()
def build(
    max_orders: int = typer.Option(
        None, help="Cap DataCo orders (the graph saturates well before the full set)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Assemble the supply graph from every available corpus."""
    configure(level="DEBUG" if verbose else settings.log_level, human=True)

    console.print("[dim]Loading corpora…[/]")
    rulings = _load("cross_rulings.jsonl", CrossRuling)
    flows = _load("comtrade_flows.jsonl", ComtradeFlow)
    orders = _load("dataco_orders.jsonl", DataCoOrder)
    countries = _load("worldbank_lpi.jsonl", CountryLogistics)
    ports = _load("worldbank_ports.jsonl", PortPerformance)

    if not any([rulings, flows, orders, countries, ports]):
        console.print(
            "[red]✗ No corpora found.[/] Run [bold]marsa-ingest fixtures[/] first."
        )
        raise typer.Exit(code=2)

    if max_orders:
        orders = orders[:max_orders]

    graph, report = build_graph(
        rulings=rulings, flows=flows, orders=orders, countries=countries, ports=ports
    )

    # Phase D enrichment, when its artefacts exist. Optional by design: the
    # graph must remain buildable before any model has been trained, so a
    # missing model degrades to the observed late rates rather than failing.
    report["enrichment"] = _enrich(graph, orders)

    origin = dominant_origin(rulings, flows, orders, countries, ports)
    stats = compute_stats(graph, origin=origin)

    graph_path, manifest_path = save_graph(
        graph, stats, data_dir=settings.data_dir, build_report=report
    )

    console.print(
        f"\n[green]✓[/] graph built: [bold]{stats.nodes:,}[/] nodes, "
        f"[bold]{stats.edges:,}[/] edges → {graph_path.name}"
    )
    console.print(
        f"[dim]  {stats.components} component(s), largest {stats.largest_component:,} nodes, "
        f"{stats.isolated_nodes} isolated[/]"
    )

    share = stats.inferred_edges / stats.edges if stats.edges else 0
    style = "yellow" if share > 0.3 else "dim"
    console.print(
        f"[{style}]  {stats.inferred_edges:,} of {stats.edges:,} edges ({share:.0%}) "
        f"are bridging assumptions, not recorded facts[/]"
    )

    if origin == "synthetic":
        console.print(
            "\n[yellow]⚠ Built from synthetic corpora.[/] Structure is exercised; "
            "no finding from it describes real trade."
        )


def _enrich(graph, orders: list) -> dict:
    """Fold Phase D congestion scores and predicted corridor risk into the graph."""
    from marsa.ml.artifacts import load_congestion, load_model
    from marsa.ml.enrich import enrich_corridors, enrich_ports, enrichment_summary

    congestion = load_congestion(settings.data_dir)
    if congestion:
        enrich_ports(graph, congestion)
        console.print(f"[dim]  enriched {len(congestion)} ports with congestion tiers[/]")
    else:
        console.print("[dim]  no congestion scores — run `marsa-ml congestion`[/]")

    try:
        _name, model = load_model(settings.data_dir)
    except FileNotFoundError:
        console.print("[dim]  no risk model — run `marsa-ml train`[/]")
        return enrichment_summary(graph)

    if orders:
        from marsa.ml.train import predict_corridor_risk

        updated = enrich_corridors(graph, predict_corridor_risk(model, orders))
        console.print(f"[dim]  enriched {updated} corridors with predicted risk[/]")

    return enrichment_summary(graph)


@app.command()
def query(
    text: str = typer.Argument(..., help="A relationship/network question"),
    hops: int = typer.Option(
        DEFAULT_HOPS, help="Traversal depth (3 reaches the shipment layer)"
    ),
    max_nodes: int = typer.Option(400, help="Hard cap on subgraph size"),
    show_json: bool = typer.Option(False, "--json", help="Emit the trace as JSON"),
) -> None:
    """Resolve entities, traverse, and narrate the result."""
    configure(level="WARNING", human=True)

    try:
        graph = load_graph(settings.data_dir)
    except FileNotFoundError as exc:
        console.print(f"[red]✗ {exc}[/]")
        raise typer.Exit(code=2) from exc

    resolver = EntityResolver(graph)
    entities = resolver.resolve(text)

    if not entities:
        console.print(
            "[yellow]No entity resolved.[/] The graph path cannot answer this — "
            "nothing named in the question exists in the graph."
        )
        raise typer.Exit(code=0)

    table = Table(title="Resolved entities", header_style="bold")
    table.add_column("Node")
    table.add_column("Kind")
    table.add_column("Matched on")
    table.add_column("Method")
    table.add_column("Conf.", justify="right")
    for entity in entities:
        table.add_row(
            entity.label, entity.kind, entity.matched_on, entity.method,
            f"{entity.confidence:.2f}",
        )
    console.print(table)

    result = k_hop_subgraph(
        graph, [e.node_id for e in entities], hops=hops, max_nodes=max_nodes
    )

    console.print()
    console.print(narrate(result, question=text))

    if show_json:
        console.print_json(
            json.dumps(
                {
                    "seeds": result.seeds,
                    "nodes": result.node_count,
                    "edges": result.edge_count,
                    "truncated": result.truncated,
                    "top_exposure": [
                        {"node": n, "exposure": round(s, 4)}
                        for n, s in result.ranked(limit=10)
                    ],
                }
            )
        )


@app.command()
def stats() -> None:
    """Composition, connectivity and how much rests on assumptions."""
    manifest = load_manifest(settings.data_dir)
    if manifest is None:
        console.print("[yellow]No graph built yet. Run [bold]marsa-graph build[/].[/]")
        raise typer.Exit(code=0)

    s = manifest["stats"]

    summary = Table(title="Phase C — supply graph", header_style="bold")
    summary.add_column("Property")
    summary.add_column("Value", justify="right")
    for key, label in [
        ("nodes", "nodes"), ("edges", "edges"), ("components", "components"),
        ("largestComponent", "largest component"), ("isolatedNodes", "isolated nodes"),
        ("density", "density"), ("origin", "origin"),
    ]:
        summary.add_row(label, f"{s[key]:,}" if isinstance(s[key], int) else str(s[key]))
    console.print(summary)

    nodes = Table(title="Nodes by kind", header_style="bold")
    nodes.add_column("Kind")
    nodes.add_column("Count", justify="right")
    for kind, count in sorted(s["nodesByKind"].items(), key=lambda kv: -kv[1]):
        nodes.add_row(kind, f"{count:,}")
    console.print(nodes)

    edges = Table(title="Edges by kind", header_style="bold")
    edges.add_column("Kind")
    edges.add_column("Count", justify="right")
    edges.add_column("Inferred?")
    inferred_kinds = {"routes_through", "ships_from", "ships_to"}
    for kind, count in sorted(s["edgesByKind"].items(), key=lambda kv: -kv[1]):
        edges.add_row(kind, f"{count:,}", "yes" if kind in inferred_kinds else "")
    console.print(edges)

    if s["inferredEdges"]:
        console.print(
            f"\n[yellow]{s['inferredEdges']:,} of {s['edges']:,} edges "
            f"({s['inferredShare']:.0%}) are bridging assumptions:[/]"
        )
        for basis, count in sorted(s["inferredByBasis"].items(), key=lambda kv: -kv[1]):
            rule = BRIDGE_RULES.get(basis)
            console.print(f"  [bold]{basis}[/] — {count:,} edges")
            if rule:
                console.print(f"    [dim]{rule.caveat}[/]")

    if s.get("origin") == "synthetic":
        console.print(
            "\n[yellow]⚠ Built from synthetic corpora — no finding describes real trade.[/]"
        )


@app.command()
def node(
    identifier: str = typer.Argument(..., help="Node ID, e.g. port:AEJEA"),
) -> None:
    """Inspect one node: attributes, edges, routing alternatives."""
    configure(level="WARNING", human=True)
    graph = load_graph(settings.data_dir)

    nid = identifier if ":" in identifier else node_id(NodeKind.PORT, identifier)
    if not graph.has_node(nid):
        console.print(f"[red]✗ No node {nid}[/]")
        raise typer.Exit(code=2)

    console.print(f"\n[bold]{nid}[/]")
    console.print_json(json.dumps(dict(graph.nodes[nid]), default=str))

    table = Table(title="Edges", header_style="bold")
    table.add_column("Direction")
    table.add_column("Kind")
    table.add_column("Other node")
    table.add_column("Weight", justify="right")
    table.add_column("Inferred")

    for _, dst, data in graph.out_edges(nid, data=True):
        table.add_row("out →", data.get("kind", ""), dst,
                      f"{data.get('weight', 0):.3f}", "yes" if data.get("inferred") else "")
    for src, _, data in graph.in_edges(nid, data=True):
        table.add_row("← in", data.get("kind", ""), src,
                      f"{data.get('weight', 0):.3f}", "yes" if data.get("inferred") else "")
    console.print(table)

    routes = alternative_routes(graph, nid)
    if routes:
        console.print(f"Gateway ports reachable: [bold]{routes}[/]")


if __name__ == "__main__":
    app()
