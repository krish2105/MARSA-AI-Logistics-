"""Fold the Phase D models back into the Phase C graph.

This is spec item 12 — "both feed into the Graph Path's risk-propagation
answers" — and it is the step that makes the ML layer worth building rather
than a detached notebook.

Two enrichments, and each replaces something weaker:

1. **Congestion severity on port nodes.** Before this, a traversal seeded on a
   port injected a flat severity of 1.0 regardless of whether that port was
   Singapore or Los Angeles. Now the seed severity *is* the congestion score,
   so "if Jebel Ali congestion worsens" and "if Los Angeles congestion worsens"
   no longer produce identically-shaped answers.

2. **Model-predicted risk on `ships_from` edges.** The graph already carried an
   *observed* late rate per corridor, which is unusable where a corridor has
   three shipments — the observed rate is 0.0 or 1.0 and neither means
   anything. The model's predicted rate borrows strength from shipping mode,
   region and category, so sparse corridors get a sane number instead of noise.

Both are written as *additional* attributes. The observed rate stays on the
edge next to the predicted one, so the two can be compared rather than one
quietly overwriting the other.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from marsa.graph.schema import EdgeKind, NodeKind, node_id
from marsa.logging import get_logger

log = get_logger(__name__)

#: A corridor needs at least this many shipments before its observed late rate
#: is worth trusting over the model's prediction. Below it, the observed rate is
#: a coin flip dressed up as a measurement.
MIN_SHIPMENTS_FOR_OBSERVED = 30


def enrich_ports(graph: nx.MultiDiGraph, congestion: list[dict[str, Any]]) -> int:
    """Attach congestion score and tier to port nodes."""
    updated = 0
    for entry in congestion:
        nid = node_id(NodeKind.PORT, entry["unlocode"])
        if not graph.has_node(nid):
            continue
        graph.nodes[nid]["congestion_score"] = entry["score"]
        graph.nodes[nid]["congestion_tier"] = entry["tier"]
        updated += 1

    log.info("ports enriched with congestion", extra={"ports": updated})
    return updated


def enrich_corridors(
    graph: nx.MultiDiGraph, corridor_risk: dict[tuple[str, ...], float]
) -> int:
    """Attach model-predicted late risk to `ships_from` edges.

    `corridor_risk` is keyed by (order_country, category_name) as produced by
    `train.predict_corridor_risk` — the *raw* DataCo country name, which must be
    mapped through the same ISO3 bridge the graph was built with, or nothing
    matches.
    """
    from marsa.graph.bridges import country_to_iso3

    updated = 0
    for key, risk in corridor_risk.items():
        if len(key) != 2:
            continue
        country_name, category = key
        iso3 = country_to_iso3(country_name)
        if not iso3 or not category:
            continue

        src = node_id(NodeKind.COUNTRY, iso3)
        dst = node_id(NodeKind.CATEGORY, str(category))
        if not graph.has_edge(src, dst, EdgeKind.SHIPS_FROM.value):
            continue

        data = graph.edges[src, dst, EdgeKind.SHIPS_FROM.value]
        data["predicted_late_rate"] = round(float(risk), 4)

        # Prefer the observed rate only where there is enough of it to mean
        # something; otherwise defer to the model. `risk_source` records which
        # won, so the choice is inspectable rather than buried in arithmetic.
        shipments = int(data.get("shipments") or 0)
        if shipments >= MIN_SHIPMENTS_FOR_OBSERVED and data.get("late_rate") is not None:
            data["effective_late_rate"] = data["late_rate"]
            data["risk_source"] = "observed"
        else:
            data["effective_late_rate"] = round(float(risk), 4)
            data["risk_source"] = "model"
        updated += 1

    log.info("corridors enriched with predicted risk", extra={"edges": updated})
    return updated


def seed_severity_for(graph: nx.MultiDiGraph, node: str, *, default: float = 1.0) -> float:
    """Severity to inject when traversal is seeded on `node`.

    A congested port is a stronger shock than a well-run one. Scaled to a floor
    of 0.35 so even a quiet port still produces a readable answer — the question
    "what if this gets worse" presupposes a disruption regardless of today's
    score.
    """
    data = graph.nodes.get(node, {})
    score = data.get("congestion_score")
    if score is None:
        return default
    return max(0.35, min(1.0, 0.35 + 0.65 * float(score)))


def enrichment_summary(graph: nx.MultiDiGraph) -> dict[str, Any]:
    """What the enrichment actually changed — for the manifest and the UI."""
    ports = [
        d for _, d in graph.nodes(data=True)
        if d.get("kind") == NodeKind.PORT.value and "congestion_score" in d
    ]
    edges = [
        d for _, _, d in graph.edges(data=True)
        if d.get("kind") == EdgeKind.SHIPS_FROM.value and "predicted_late_rate" in d
    ]
    by_source: dict[str, int] = {}
    for edge in edges:
        source = str(edge.get("risk_source", "unknown"))
        by_source[source] = by_source.get(source, 0) + 1

    return {
        "portsScored": len(ports),
        "corridorsScored": len(edges),
        "riskSource": by_source,
        "tiers": _tier_counts(ports),
    }


def _tier_counts(ports: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for port in ports:
        tier = str(port.get("congestion_tier", "unknown"))
        counts[tier] = counts.get(tier, 0) + 1
    return counts
