"""k-hop traversal, subgraph extraction and risk propagation.

Traversal treats the graph as **undirected**. That is a modelling decision, not
laziness: exposure propagates against the direction of goods. If Jebel Ali
congests, the disruption travels backwards up the supply chain to the suppliers
who ship through it, even though the cargo travels forwards. A directed
traversal from the port would reach almost nothing.

Risk propagation is damped diffusion rather than raw graph distance. A node's
exposure is the seed's severity attenuated by hop count and by edge weight
along the best path, so a supplier with one strong dependency on a congested
port ranks above one with three weak alternatives — which is the actual answer
to "who is exposed".
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from marsa.graph.schema import EdgeKind, NodeKind, node_kind_of

#: Exposure retained per hop. 0.55 means a 2-hop neighbour carries ~30% of the
#: seed's severity — steep enough that distant nodes do not crowd the answer,
#: shallow enough that the second ring still appears at all.
HOP_DECAY = 0.55

#: Edge kinds that carry disruption. A `cites` edge between two rulings is a
#: legal relationship, not a physical one, so congestion does not flow along it.
RISK_BEARING_EDGES = frozenset(
    {
        EdgeKind.ROUTES_THROUGH.value,
        EdgeKind.LOCATED_IN.value,
        EdgeKind.TRADES_WITH.value,
        EdgeKind.SHIPS_FROM.value,
        EdgeKind.SHIPS_TO.value,
        EdgeKind.IN_CATEGORY.value,
    }
)


@dataclass
class TraversalResult:
    seeds: list[str]
    subgraph: nx.MultiDiGraph
    #: node_id → hops from the nearest seed.
    distances: dict[str, int] = field(default_factory=dict)
    #: node_id → propagated exposure in [0, 1].
    exposure: dict[str, float] = field(default_factory=dict)
    truncated: bool = False

    @property
    def node_count(self) -> int:
        return self.subgraph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self.subgraph.number_of_edges()

    def nodes_of_kind(self, kind: NodeKind | str) -> list[str]:
        want = kind.value if isinstance(kind, NodeKind) else kind
        return [n for n in self.subgraph.nodes if node_kind_of(n) == want]

    def ranked(
        self, kind: NodeKind | str | None = None, *, limit: int = 10
    ) -> list[tuple[str, float]]:
        """Nodes by exposure, highest first, optionally filtered by kind."""
        pool = self.nodes_of_kind(kind) if kind is not None else list(self.subgraph.nodes)
        scored = [(n, self.exposure.get(n, 0.0)) for n in pool if n not in self.seeds]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:limit]


#: Default traversal depth.
#:
#: Three, not two, because the graph's layers are port → country → category →
#: product. A supplier-exposure question seeded on a port structurally *cannot*
#: be answered at two hops — it reaches countries and stops, one layer short of
#: the goods the question is about. Three reaches ~34 nodes on this corpus,
#: comfortably inside the node cap.
DEFAULT_HOPS = 3


def k_hop_subgraph(
    graph: nx.MultiDiGraph,
    seeds: list[str],
    *,
    hops: int = DEFAULT_HOPS,
    max_nodes: int = 400,
    edge_kinds: frozenset[str] | None = None,
) -> TraversalResult:
    """Breadth-first expansion from `seeds`, treated as undirected.

    `max_nodes` is a hard stop. A hub node — a popular HS chapter, a major
    trading country — can pull in most of the graph at 2 hops, which makes the
    narrated answer useless. Truncation is recorded so the caller can say the
    view was capped rather than presenting a partial subgraph as complete.
    """
    present = [s for s in seeds if graph.has_node(s)]
    result = TraversalResult(seeds=present, subgraph=nx.MultiDiGraph())

    if not present:
        return result

    distances: dict[str, int] = {s: 0 for s in present}
    queue: deque[str] = deque(present)
    visited = set(present)

    while queue:
        node = queue.popleft()
        depth = distances[node]
        if depth >= hops:
            continue

        for neighbour, _key, data in _incident(graph, node):
            if edge_kinds and data.get("kind") not in edge_kinds:
                continue
            if neighbour in visited:
                continue
            if len(visited) >= max_nodes:
                result.truncated = True
                queue.clear()
                break
            visited.add(neighbour)
            distances[neighbour] = depth + 1
            queue.append(neighbour)

    # Induce over the visited set, keeping every edge kind between them so the
    # narration can describe *how* nodes relate, not just that they do.
    sub = nx.MultiDiGraph()
    for node in visited:
        sub.add_node(node, **graph.nodes[node])
    for u, v, key, data in graph.edges(keys=True, data=True):
        if u in visited and v in visited:
            sub.add_edge(u, v, key=key, **data)

    result.subgraph = sub
    result.distances = distances
    result.exposure = propagate_risk(sub, present, distances)
    return result


def _incident(graph: nx.MultiDiGraph, node: str):
    """Every edge touching `node`, in both directions."""
    for _, neighbour, key, data in graph.out_edges(node, keys=True, data=True):
        yield neighbour, key, data
    for neighbour, _, key, data in graph.in_edges(node, keys=True, data=True):
        yield neighbour, key, data


def propagate_risk(
    subgraph: nx.MultiDiGraph,
    seeds: list[str],
    distances: dict[str, int],
    *,
    decay: float = HOP_DECAY,
    seed_severity: float = 1.0,
) -> dict[str, float]:
    """Damped diffusion of `seed_severity` outward from the seeds.

    A node's exposure is the best (highest) value over all paths from any seed,
    where each hop multiplies by `decay` and by that edge's normalised weight.
    Best-path rather than summed: exposure is about the strongest dependency,
    and summing would rank a node with many trivial links above one with a
    single critical one.

    Edges outside `RISK_BEARING_EDGES` do not conduct — a citation between two
    rulings is a legal relationship, not a physical route.
    """
    exposure: dict[str, float] = {s: seed_severity for s in seeds}
    frontier: deque[str] = deque(seeds)

    while frontier:
        node = frontier.popleft()
        current = exposure[node]

        for neighbour, _key, data in _incident(subgraph, node):
            if data.get("kind") not in RISK_BEARING_EDGES:
                continue
            # Only flow outward, never back toward the seed.
            if distances.get(neighbour, 0) <= distances.get(node, 0):
                continue

            strength = _edge_strength(data)
            candidate = current * decay * strength
            if candidate > exposure.get(neighbour, 0.0) + 1e-9:
                exposure[neighbour] = min(1.0, candidate)
                frontier.append(neighbour)

    return exposure


def _edge_strength(data: dict[str, Any]) -> float:
    """Conductance of one edge, in [0.2, 1.0].

    An inferred edge conducts at 80%: it is a bridging assumption rather than an
    observation, and exposure that reaches a node only through assumptions
    should rank below exposure supported by recorded data.
    """
    kind = data.get("kind")

    if kind == EdgeKind.TRADES_WITH.value:
        # Already normalised to [0, 1] at build time.
        strength = float(data.get("weight", 0.5))
    elif kind in (EdgeKind.SHIPS_FROM.value, EdgeKind.SHIPS_TO.value):
        # A corridor that already fails often conducts disruption more readily.
        late = float(data.get("late_rate", 0.0) or 0.0)
        strength = 0.55 + 0.45 * late
    else:
        strength = 0.85

    if data.get("inferred"):
        strength *= 0.8

    return max(0.2, min(1.0, strength))


def alternative_routes(graph: nx.MultiDiGraph, node: str) -> int:
    """How many distinct ports a country can reach.

    This is the concentration measure the graph path reports: a supplier with
    exactly one routing option is exposed in a way one with three is not.
    """
    ports = {
        neighbour
        for neighbour, _key, data in _incident(graph, node)
        if node_kind_of(neighbour) == NodeKind.PORT.value
        and data.get("kind") == EdgeKind.ROUTES_THROUGH.value
    }
    return len(ports)


def shortest_path_between(graph: nx.MultiDiGraph, src: str, dst: str) -> list[str] | None:
    """Undirected shortest path, for explaining *why* two nodes are connected."""
    if not graph.has_node(src) or not graph.has_node(dst):
        return None
    try:
        return nx.shortest_path(graph.to_undirected(as_view=True), src, dst)
    except nx.NetworkXNoPath:
        return None
