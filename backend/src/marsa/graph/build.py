"""Assemble the supply-chain graph from all four corpora.

    CROSS rulings  ──→ ruling, hts, hts_chapter          (classified_under, cites)
    UN Comtrade    ──→ country                            (trades_with)
    DataCo         ──→ product, category, department,     (ships_from, ships_to,
                       customer, market, region            in_category, part_of)
    World Bank     ──→ port, country attributes           (located_in, routes_through)

A `MultiDiGraph` is used rather than a simple `DiGraph` because two nodes can
legitimately be joined by different relationships — a country both *trades with*
and *routes through* its neighbours' infrastructure — and collapsing those into
one edge would lose the distinction the graph path reasons over.

Edge weights are normalised to roughly [0, 1] per kind so traversal can compare
a trade-flow edge against a shipment edge without one dominating purely because
it is denominated in dollars.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

import networkx as nx

from marsa.graph.bridges import (
    M49_TO_ISO3,
    build_gateway_map,
    category_to_chapter,
    country_to_iso3,
    hts_to_chapter,
)
from marsa.graph.schema import EdgeKind, GraphStats, NodeKind, node_id
from marsa.ingestion.schemas import (
    ComtradeFlow,
    CountryLogistics,
    CrossRuling,
    DataCoOrder,
    Origin,
    PortPerformance,
)
from marsa.logging import get_logger

log = get_logger(__name__)


def _add_node(graph: nx.MultiDiGraph, nid: str, kind: NodeKind, **attrs: Any) -> str:
    if graph.has_node(nid):
        # Merge rather than replace: a country arrives from Comtrade, DataCo and
        # the World Bank, each carrying different attributes.
        for key, value in attrs.items():
            if value is not None:
                graph.nodes[nid][key] = value
    else:
        graph.add_node(nid, kind=kind.value, **{k: v for k, v in attrs.items() if v is not None})
    return nid


def _add_edge(
    graph: nx.MultiDiGraph,
    src: str,
    dst: str,
    kind: EdgeKind,
    *,
    weight: float = 1.0,
    inferred: bool = False,
    basis: str | None = None,
    **attrs: Any,
) -> None:
    """Add or reinforce an edge.

    Repeat observations accumulate into `weight` and `observations` rather than
    creating parallel edges — 400 shipments on one corridor is one strong edge,
    not 400 weak ones.
    """
    key = kind.value
    if graph.has_edge(src, dst, key):
        data = graph.edges[src, dst, key]
        data["weight"] = data.get("weight", 0.0) + weight
        data["observations"] = data.get("observations", 1) + 1
        for k, v in attrs.items():
            if isinstance(v, (int, float)) and isinstance(data.get(k), (int, float)):
                data[k] += v
        return

    graph.add_edge(
        src,
        dst,
        key=key,
        kind=kind.value,
        weight=weight,
        observations=1,
        inferred=inferred,
        basis=basis,
        **attrs,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Per-corpus builders
# ─────────────────────────────────────────────────────────────────────────────


def add_rulings(graph: nx.MultiDiGraph, rulings: Iterable[CrossRuling]) -> None:
    """Rulings, the codes they classify under, and their citation network."""

    for ruling in rulings:
        rid = _add_node(
            graph,
            node_id(NodeKind.RULING, ruling.ruling_number),
            NodeKind.RULING,
            label=ruling.ruling_number,
            subject=ruling.subject,
            collection=ruling.collection,
            category=ruling.category,
            url=ruling.url,
        )
        for code in ruling.hts_codes:
            cid = _add_node(
                graph, node_id(NodeKind.HTS_CODE, code), NodeKind.HTS_CODE, label=code
            )
            _add_edge(graph, rid, cid, EdgeKind.CLASSIFIED_UNDER)

            chapter = hts_to_chapter(code)
            if chapter:
                chid = _add_node(
                    graph,
                    node_id(NodeKind.HTS_CHAPTER, chapter),
                    NodeKind.HTS_CHAPTER,
                    label=f"HS {chapter}",
                )
                _add_edge(
                    graph, cid, chid, EdgeKind.PART_OF,
                    inferred=True, basis="hts_code_to_chapter",
                )


def add_citations(graph: nx.MultiDiGraph, rulings: Iterable[CrossRuling]) -> int:
    """Second pass: ruling → ruling citations, within-corpus only.

    Runs separately from `add_rulings` so an edge is only created once every
    ruling node exists. Citations to rulings outside the subset are skipped
    rather than creating placeholder nodes — a node nothing can be said about
    inflates the graph and produces empty answers on traversal.
    """
    rulings = list(rulings)
    present = {r.ruling_number for r in rulings}
    added = 0
    for ruling in rulings:
        src = node_id(NodeKind.RULING, ruling.ruling_number)
        for cited in ruling.related_rulings:
            if cited not in present:
                continue  # do not invent a node for a ruling we do not hold
            _add_edge(graph, src, node_id(NodeKind.RULING, cited), EdgeKind.CITES)
            added += 1
    return added


def add_trade_flows(graph: nx.MultiDiGraph, flows: Iterable[ComtradeFlow]) -> None:
    """Country ↔ country trade, aggregated across chapters and years."""
    aggregated: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"value": 0.0, "chapters": Counter(), "years": set()}
    )

    for flow in flows:
        reporter = flow.reporter_iso or M49_TO_ISO3.get(flow.reporter_code)
        partner = flow.partner_iso or M49_TO_ISO3.get(flow.partner_code)
        if not reporter or not partner or reporter == partner:
            continue

        # Imports point partner → reporter; exports point reporter → partner.
        src, dst = (partner, reporter) if flow.flow_code == "M" else (reporter, partner)
        entry = aggregated[(src, dst)]
        entry["value"] += flow.primary_value or 0.0
        if flow.cmd_code:
            entry["chapters"][flow.cmd_code] += 1
        entry["years"].add(flow.ref_year)

    if not aggregated:
        return

    # Log-scale then normalise: trade values are heavy-tailed, and a linear
    # weight would make one corridor swamp every other edge in traversal.
    raw = {k: math.log1p(v["value"]) for k, v in aggregated.items()}
    peak = max(raw.values()) or 1.0

    for (src, dst), entry in aggregated.items():
        s = _add_node(graph, node_id(NodeKind.COUNTRY, src), NodeKind.COUNTRY, label=src)
        d = _add_node(graph, node_id(NodeKind.COUNTRY, dst), NodeKind.COUNTRY, label=dst)
        _add_edge(
            graph, s, d, EdgeKind.TRADES_WITH,
            weight=raw[(src, dst)] / peak,
            trade_value_usd=round(entry["value"], 2),
            top_chapters=[c for c, _ in entry["chapters"].most_common(3)],
            years=sorted(entry["years"]),
        )


def add_logistics(
    graph: nx.MultiDiGraph,
    countries: Iterable[CountryLogistics],
    ports: Iterable[PortPerformance],
) -> dict[str, str]:
    """Country LPI attributes, port nodes, and the inferred gateway edges."""
    # Keep the most recent year per country.
    latest: dict[str, CountryLogistics] = {}
    for record in countries:
        current = latest.get(record.country_iso3)
        if current is None or record.year > current.year:
            latest[record.country_iso3] = record

    for iso3, record in latest.items():
        _add_node(
            graph,
            node_id(NodeKind.COUNTRY, iso3),
            NodeKind.COUNTRY,
            label=record.country_name or iso3,
            lpi_score=record.lpi_score,
            import_dwell_days=record.import_dwell_days,
            export_dwell_days=record.export_dwell_days,
            timeliness_score=record.timeliness_score,
        )

    port_list = list(ports)
    for port in port_list:
        pid = _add_node(
            graph,
            node_id(NodeKind.PORT, port.unlocode or port.port_name),
            NodeKind.PORT,
            label=port.port_name,
            unlocode=port.unlocode,
            cppi_rank=port.cppi_rank,
            cppi_score=port.cppi_score,
            annual_teu=port.annual_teu,
            avg_vessel_hours=port.avg_vessel_hours,
        )
        if port.country_iso3:
            cid = _add_node(
                graph,
                node_id(NodeKind.COUNTRY, port.country_iso3),
                NodeKind.COUNTRY,
                label=port.country_iso3,
            )
            _add_edge(graph, pid, cid, EdgeKind.LOCATED_IN)

    gateways = build_gateway_map(port_list)
    for iso3, unlocode in gateways.items():
        _add_edge(
            graph,
            node_id(NodeKind.COUNTRY, iso3),
            node_id(NodeKind.PORT, unlocode),
            EdgeKind.ROUTES_THROUGH,
            inferred=True,
            basis="country_to_gateway_port",
        )
    return gateways


def add_shipments(graph: nx.MultiDiGraph, orders: Iterable[DataCoOrder]) -> dict[str, int]:
    """DataCo shipments: origin → category → destination, plus risk rollups."""
    counters = Counter()
    # Late-delivery rate per (origin, category) corridor, so the graph carries a
    # real risk signal before Phase D's model exists.
    corridor: dict[tuple[str, str], list[int]] = defaultdict(list)

    for order in orders:
        category = (order.category_name or "").strip()
        if not category:
            continue

        cat_id = _add_node(
            graph, node_id(NodeKind.CATEGORY, category), NodeKind.CATEGORY, label=category
        )

        if order.department_name:
            dep_id = _add_node(
                graph,
                node_id(NodeKind.DEPARTMENT, order.department_name),
                NodeKind.DEPARTMENT,
                label=order.department_name,
            )
            _add_edge(graph, cat_id, dep_id, EdgeKind.PART_OF)

        if order.product_name:
            prod_id = _add_node(
                graph,
                node_id(NodeKind.PRODUCT, order.product_name),
                NodeKind.PRODUCT,
                label=order.product_name,
            )
            _add_edge(graph, prod_id, cat_id, EdgeKind.IN_CATEGORY)

        origin_iso = country_to_iso3(order.order_country)
        if origin_iso:
            origin_id = _add_node(
                graph, node_id(NodeKind.COUNTRY, origin_iso), NodeKind.COUNTRY, label=origin_iso
            )
            _add_edge(
                graph, origin_id, cat_id, EdgeKind.SHIPS_FROM,
                weight=1.0, late=order.late_delivery_risk or 0,
                inferred=True, basis="country_name_to_iso3",
            )
            corridor[(origin_iso, category)].append(order.late_delivery_risk or 0)
            counters["origin_resolved"] += 1
        else:
            counters["origin_unresolved"] += 1

        dest_iso = country_to_iso3(order.customer_country)
        if dest_iso:
            dest_id = _add_node(
                graph, node_id(NodeKind.COUNTRY, dest_iso), NodeKind.COUNTRY, label=dest_iso
            )
            _add_edge(
                graph, cat_id, dest_id, EdgeKind.SHIPS_TO,
                weight=1.0, late=order.late_delivery_risk or 0,
                inferred=True, basis="country_name_to_iso3",
            )

        if order.market:
            market_id = _add_node(
                graph, node_id(NodeKind.MARKET, order.market), NodeKind.MARKET, label=order.market
            )
            if order.order_region:
                region_id = _add_node(
                    graph,
                    node_id(NodeKind.REGION, order.order_region),
                    NodeKind.REGION,
                    label=order.order_region,
                )
                _add_edge(graph, region_id, market_id, EdgeKind.PART_OF)
                if origin_iso:
                    _add_edge(graph, origin_id, region_id, EdgeKind.PART_OF)

        # Category → HS chapter, so shipments can reach the ruling corpus.
        chapter = category_to_chapter(category)
        if chapter:
            ch_id = _add_node(
                graph,
                node_id(NodeKind.HTS_CHAPTER, chapter),
                NodeKind.HTS_CHAPTER,
                label=f"HS {chapter}",
            )
            _add_edge(
                graph, cat_id, ch_id, EdgeKind.CLASSIFIED_UNDER,
                inferred=True, basis="category_to_hs_chapter",
            )
            counters["category_mapped"] += 1
        else:
            counters["category_unmapped"] += 1

    # Write the corridor late-rate onto the ships_from edges.
    for (origin_iso, category), labels in corridor.items():
        src = node_id(NodeKind.COUNTRY, origin_iso)
        dst = node_id(NodeKind.CATEGORY, category)
        if graph.has_edge(src, dst, EdgeKind.SHIPS_FROM.value):
            data = graph.edges[src, dst, EdgeKind.SHIPS_FROM.value]
            data["shipments"] = len(labels)
            data["late_rate"] = round(sum(labels) / len(labels), 4)

    return dict(counters)


# ─────────────────────────────────────────────────────────────────────────────


def build_graph(
    *,
    rulings: list[CrossRuling] | None = None,
    flows: list[ComtradeFlow] | None = None,
    orders: list[DataCoOrder] | None = None,
    countries: list[CountryLogistics] | None = None,
    ports: list[PortPerformance] | None = None,
) -> tuple[nx.MultiDiGraph, dict[str, Any]]:
    """Build the whole graph. Returns the graph and a build report."""
    graph = nx.MultiDiGraph()
    report: dict[str, Any] = {}

    if rulings:
        add_rulings(graph, rulings)
        report["citations"] = add_citations(graph, rulings)
        report["rulings"] = len(rulings)

    if flows:
        add_trade_flows(graph, flows)
        report["flows"] = len(flows)

    if countries or ports:
        gateways = add_logistics(graph, countries or [], ports or [])
        report["gateways"] = len(gateways)

    if orders:
        report["shipments"] = add_shipments(graph, orders)
        report["orders"] = len(orders)

    return graph, report


def compute_stats(graph: nx.MultiDiGraph, *, origin: str = "unknown") -> GraphStats:
    stats = GraphStats(nodes=graph.number_of_nodes(), edges=graph.number_of_edges())

    stats.nodes_by_kind = dict(
        Counter(data.get("kind", "unknown") for _, data in graph.nodes(data=True))
    )
    stats.edges_by_kind = dict(
        Counter(data.get("kind", "unknown") for _, _, data in graph.edges(data=True))
    )

    inferred = [d for _, _, d in graph.edges(data=True) if d.get("inferred")]
    stats.inferred_edges = len(inferred)
    stats.inferred_by_basis = dict(Counter(d.get("basis", "unknown") for d in inferred))

    undirected = graph.to_undirected(as_view=False)
    components = list(nx.connected_components(undirected)) if graph.number_of_nodes() else []
    stats.components = len(components)
    stats.largest_component = max((len(c) for c in components), default=0)
    stats.isolated_nodes = sum(1 for n in graph.nodes if graph.degree(n) == 0)
    stats.density = nx.density(graph) if graph.number_of_nodes() > 1 else 0.0
    stats.origin = origin
    return stats


def dominant_origin(*corpora: Iterable) -> str:
    """`synthetic` if any record in any corpus is synthetic, else `live`.

    Deliberately pessimistic: one synthetic corpus contaminates the whole graph
    for reporting purposes, because a traversal crosses corpus boundaries and
    the resulting answer cannot be partly real.
    """
    for corpus in corpora:
        for record in corpus:
            if getattr(record, "provenance", None) and record.provenance.origin is Origin.SYNTHETIC:
                return Origin.SYNTHETIC.value
            break  # one record is enough — a corpus has uniform provenance
    return Origin.LIVE.value
