"""Subgraph → text.

The graph path's final step before composition. A subgraph is not an answer;
this turns one into prose an LLM can compose from, or a human can read directly.

Two rules shape the output:

1. **Lead with the concentration finding, not the inventory.** "Nineteen
   suppliers route through Jebel Ali" is trivia. "Six of them have no
   alternative port" is the answer. The narration ranks by exposure and
   singles out single-point-of-failure nodes.

2. **Disclose the inferred edges inline.** When a conclusion depends on a
   bridging assumption — a category mapped to an HS chapter, a country mapped
   to one gateway port — the narration says so in the same breath. Burying that
   in a footnote would make the answer look better sourced than it is.
"""

from __future__ import annotations

from collections import Counter

import networkx as nx

from marsa.graph.schema import BRIDGE_RULES, EdgeKind, NodeKind, node_kind_of
from marsa.graph.traverse import TraversalResult, alternative_routes

KIND_NOUNS: dict[str, tuple[str, str]] = {
    NodeKind.COUNTRY.value: ("country", "countries"),
    NodeKind.PORT.value: ("port", "ports"),
    NodeKind.CATEGORY.value: ("product category", "product categories"),
    NodeKind.PRODUCT.value: ("product", "products"),
    NodeKind.RULING.value: ("ruling", "rulings"),
    NodeKind.HTS_CODE.value: ("tariff code", "tariff codes"),
    NodeKind.HTS_CHAPTER.value: ("HS chapter", "HS chapters"),
    NodeKind.DEPARTMENT.value: ("department", "departments"),
    NodeKind.MARKET.value: ("market", "markets"),
    NodeKind.REGION.value: ("region", "regions"),
}


def _label(graph: nx.MultiDiGraph, nid: str) -> str:
    data = graph.nodes.get(nid, {})
    return str(data.get("label") or nid.partition(":")[2] or nid)


def _plural(kind: str, count: int) -> str:
    singular, plural = KIND_NOUNS.get(kind, (kind, f"{kind}s"))
    return singular if count == 1 else plural


def describe_seeds(result: TraversalResult) -> str:
    graph = result.subgraph
    parts = []
    for seed in result.seeds:
        label = _label(graph, seed)
        data = graph.nodes.get(seed, {})
        kind = node_kind_of(seed)

        detail = ""
        if kind == NodeKind.PORT.value:
            bits = []
            if data.get("cppi_rank") is not None:
                bits.append(f"CPPI rank {data['cppi_rank']}")
            if data.get("avg_vessel_hours") is not None:
                bits.append(f"{data['avg_vessel_hours']:.1f}h average vessel time")
            if bits:
                detail = f" ({', '.join(bits)})"
        elif kind == NodeKind.COUNTRY.value:
            bits = []
            if data.get("lpi_score") is not None:
                bits.append(f"LPI {data['lpi_score']:.2f}")
            if data.get("import_dwell_days") is not None:
                bits.append(f"{data['import_dwell_days']:.1f}d import dwell")
            if bits:
                detail = f" ({', '.join(bits)})"

        parts.append(f"{label}{detail}")
    return ", ".join(parts)


def find_single_points_of_failure(
    result: TraversalResult, *, limit: int = 8
) -> list[tuple[str, float]]:
    """Countries in the subgraph with exactly one gateway port.

    This is the concentration finding. A country reachable through one port has
    no substitutable routing, so exposure there is structural rather than
    incidental.
    """
    out: list[tuple[str, float]] = []
    for nid in result.nodes_of_kind(NodeKind.COUNTRY):
        if nid in result.seeds:
            continue
        if alternative_routes(result.subgraph, nid) == 1:
            out.append((nid, result.exposure.get(nid, 0.0)))
    out.sort(key=lambda pair: -pair[1])
    return out[:limit]


def narrate(result: TraversalResult, *, question: str | None = None, top_n: int = 6) -> str:
    """Render a traversal as prose."""
    graph = result.subgraph

    if not result.seeds:
        return (
            "No entity in the question could be resolved to a node in the supply "
            "graph, so the graph path has nothing to traverse. This usually means "
            "the port, country or tariff code named is outside the ingested subset."
        )

    lines: list[str] = []

    if question:
        lines.append(f"Question: {question}")

    lines.append(
        f"Starting from {describe_seeds(result)}, a "
        f"{max(result.distances.values(), default=0)}-hop traversal reaches "
        f"{result.node_count} nodes across {result.edge_count} edges."
        + (" The view was capped at the node limit." if result.truncated else "")
    )

    # ── Composition of the neighbourhood ───────────────────────────────────
    kinds = Counter(node_kind_of(n) for n in graph.nodes if n not in result.seeds)
    if kinds:
        composition = ", ".join(
            f"{count} {_plural(kind, count)}"
            for kind, count in sorted(kinds.items(), key=lambda kv: -kv[1])
        )
        lines.append(f"The neighbourhood contains {composition}.")

    # ── The concentration finding, up front ────────────────────────────────
    spofs = find_single_points_of_failure(result)
    exposed_countries = result.ranked(NodeKind.COUNTRY, limit=top_n)

    if exposed_countries:
        listed = ", ".join(
            f"{_label(graph, nid)} ({score:.2f})" for nid, score in exposed_countries if score > 0
        )
        if listed:
            lines.append(f"Most exposed countries, by propagated risk: {listed}.")

    if spofs:
        names = ", ".join(_label(graph, nid) for nid, _ in spofs)
        verb = "has" if len(spofs) == 1 else "have"
        lines.append(
            f"Of those, {len(spofs)} {verb} only one gateway port in this graph and "
            f"therefore no substitutable routing: {names}. That is the concentration "
            "risk — the remainder have at least one alternative."
        )
    elif exposed_countries:
        lines.append(
            "Every country reached has more than one gateway port in this graph, "
            "so exposure is distributed rather than concentrated."
        )

    # ── Which goods flow through, and how reliably ─────────────────────────
    categories = result.ranked(NodeKind.CATEGORY, limit=top_n)
    if categories:
        described = []
        for nid, score in categories:
            late = _corridor_late_rate(graph, nid)
            suffix = f", {late:.0%} historical late rate" if late is not None else ""
            described.append(f"{_label(graph, nid)} ({score:.2f}{suffix})")
        lines.append(f"Affected product categories: {', '.join(described)}.")

    elif result.nodes_of_kind(NodeKind.PORT):
        # The question asked about goods but the traversal never reached any.
        # Say so rather than letting the omission read as "there are none".
        lines.append(
            "No product categories were reached at this depth, so this answer "
            "describes the port and country layer only. Shipment-level exposure "
            "sits one hop further out."
        )

    # ── Regulatory surface ─────────────────────────────────────────────────
    chapters = [_label(graph, n) for n in result.nodes_of_kind(NodeKind.HTS_CHAPTER)]
    rulings = result.nodes_of_kind(NodeKind.RULING)
    if chapters:
        line = f"Tariff exposure touches {', '.join(sorted(set(chapters))[:6])}"
        if rulings:
            line += f", covered by {len(rulings)} rulings in the corpus"
        lines.append(line + ".")

    # ── Provenance caveat, inline rather than footnoted ────────────────────
    caveat = _inferred_caveat(graph)
    if caveat:
        lines.append(caveat)

    return "\n\n".join(lines)


def _corridor_late_rate(graph: nx.MultiDiGraph, category_nid: str) -> float | None:
    """Shipment-weighted late rate across every corridor feeding a category."""
    total, late = 0, 0.0
    for _src, _dst, data in graph.in_edges(category_nid, data=True):
        if data.get("kind") != EdgeKind.SHIPS_FROM.value:
            continue
        shipments = int(data.get("shipments") or data.get("observations") or 0)
        rate = data.get("late_rate")
        if shipments and rate is not None:
            total += shipments
            late += float(rate) * shipments
    return (late / total) if total else None


def _inferred_caveat(graph: nx.MultiDiGraph) -> str | None:
    """Name the bridging assumptions this subgraph actually depends on."""
    bases = Counter(
        data.get("basis")
        for _, _, data in graph.edges(data=True)
        if data.get("inferred") and data.get("basis")
    )
    if not bases:
        return None

    total_edges = graph.number_of_edges()
    inferred = sum(bases.values())
    share = inferred / total_edges if total_edges else 0.0

    described = []
    for basis, count in bases.most_common(3):
        rule = BRIDGE_RULES.get(basis)
        if rule and "None —" not in rule.caveat:
            described.append(f"{basis} ({count} edges): {rule.caveat}")

    if not described:
        return None

    return (
        f"Provenance: {inferred} of {total_edges} edges here ({share:.0%}) are "
        "bridging assumptions rather than recorded facts. "
        + " ".join(described)
    )
