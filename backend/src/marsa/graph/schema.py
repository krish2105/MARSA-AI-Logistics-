"""Graph vocabulary: node kinds, edge kinds, and the ID scheme.

Node IDs are typed and human-readable (`port:AEJEA`, `country:ARE`,
`hts:8507.60.0020`). That is deliberate: entity resolution, traversal debugging
and the narrated output all read better when a node ID says what it is, and it
makes collisions between a country code and a product code impossible.

The `inferred` edge flag is the most important field here. This graph is built
by joining four corpora that were never designed to join, so some edges are
recorded fact and others are a *bridging assumption* this project is making.
Mixing those two silently would make every downstream answer unfalsifiable, so
every edge carries `inferred` and, when inferred, a `basis` naming the rule
that produced it. The narrator surfaces that; the manifest counts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NodeKind(StrEnum):
    COUNTRY = "country"
    PORT = "port"
    HTS_CHAPTER = "hts_chapter"
    HTS_CODE = "hts"
    RULING = "ruling"
    PRODUCT = "product"
    CATEGORY = "category"
    DEPARTMENT = "department"
    CUSTOMER = "customer"
    MARKET = "market"
    REGION = "region"


class EdgeKind(StrEnum):
    #: country → country, aggregated Comtrade bilateral flow.
    TRADES_WITH = "trades_with"
    #: country → port, the gateway a shipment is assumed to transit.
    ROUTES_THROUGH = "routes_through"
    #: port → country.
    LOCATED_IN = "located_in"
    #: ruling → hts, product → hts_chapter.
    CLASSIFIED_UNDER = "classified_under"
    #: ruling → ruling, from citations in the ruling text.
    CITES = "cites"
    #: hts → hts_chapter, category → department, region → market.
    PART_OF = "part_of"
    #: country → category, goods leaving an origin.
    SHIPS_FROM = "ships_from"
    #: category → country, goods arriving at a destination.
    SHIPS_TO = "ships_to"
    #: product → category.
    IN_CATEGORY = "in_category"
    #: customer → country.
    LOCATED_AT = "located_at"


#: Edge kinds whose direction is not meaningful for reachability. Traversal
#: treats the graph as undirected anyway (exposure flows both ways), but these
#: are the ones where even the stored direction is arbitrary.
SYMMETRIC_EDGES = frozenset({EdgeKind.TRADES_WITH})


def node_id(kind: NodeKind, key: str) -> str:
    """Build a typed node ID. Keys are normalised so lookups are stable."""
    return f"{kind.value}:{str(key).strip()}"


def split_node_id(nid: str) -> tuple[str, str]:
    kind, _, key = nid.partition(":")
    return kind, key


def node_kind_of(nid: str) -> str:
    return split_node_id(nid)[0]


@dataclass(frozen=True)
class BridgeRule:
    """A documented cross-corpus join.

    The four corpora share no keys, so connecting them requires assumptions.
    Each is declared here rather than buried in the builder, so the honest
    limitations section can be generated from code instead of remembered.
    """

    name: str
    description: str
    #: What would make this rule wrong, stated plainly.
    caveat: str


BRIDGE_RULES: dict[str, BridgeRule] = {
    "country_name_to_iso3": BridgeRule(
        name="country_name_to_iso3",
        description=(
            "DataCo stores country names in Spanish ('Alemania', 'Estados Unidos') "
            "with no ISO code. They are mapped to ISO3 via an explicit lookup so "
            "DataCo shipments can join Comtrade flows and World Bank LPI scores."
        ),
        caveat=(
            "Names outside the lookup are dropped rather than guessed, so a "
            "DataCo country this project has not seen contributes no edges."
        ),
    ),
    "category_to_hs_chapter": BridgeRule(
        name="category_to_hs_chapter",
        description=(
            "DataCo product categories carry no tariff code. They are mapped to "
            "plausible HS chapters ('Electronics' → 85, 'Men's Footwear' → 64) so "
            "shipment data connects to the CROSS ruling corpus."
        ),
        caveat=(
            "This is a coarse editorial mapping, not a customs classification. "
            "A real broker classifies per article, not per merchandising category, "
            "and would frequently disagree."
        ),
    ),
    "country_to_gateway_port": BridgeRule(
        name="country_to_gateway_port",
        description=(
            "DataCo has no port field. Each country is linked to its highest-ranked "
            "Container Port Performance Index port as the assumed gateway, giving "
            "shipments a routing path through the port network."
        ),
        caveat=(
            "Real shipments use many ports per country and often tranship through "
            "a third country. Treating one port as the gateway overstates the "
            "concentration of exposure at that port."
        ),
    ),
    "hts_code_to_chapter": BridgeRule(
        name="hts_code_to_chapter",
        description=(
            "The first two digits of an HTS code are its HS chapter. Ruling-level "
            "codes are rolled up so chapter-level questions traverse cleanly."
        ),
        caveat="None — this one is definitional, not an assumption.",
    ),
}


@dataclass
class GraphStats:
    """Summary written to the manifest and rendered in the UI."""

    nodes: int = 0
    edges: int = 0
    nodes_by_kind: dict[str, int] = field(default_factory=dict)
    edges_by_kind: dict[str, int] = field(default_factory=dict)
    inferred_edges: int = 0
    inferred_by_basis: dict[str, int] = field(default_factory=dict)
    components: int = 0
    largest_component: int = 0
    isolated_nodes: int = 0
    density: float = 0.0
    origin: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "nodesByKind": self.nodes_by_kind,
            "edgesByKind": self.edges_by_kind,
            "inferredEdges": self.inferred_edges,
            "inferredByBasis": self.inferred_by_basis,
            "components": self.components,
            "largestComponent": self.largest_component,
            "isolatedNodes": self.isolated_nodes,
            "density": round(self.density, 6),
            "origin": self.origin,
            "inferredShare": (
                round(self.inferred_edges / self.edges, 4) if self.edges else 0.0
            ),
        }
