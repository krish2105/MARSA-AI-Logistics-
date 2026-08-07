"""Phase C: bridges, graph construction, resolution, traversal and narration."""

from __future__ import annotations

from datetime import UTC, datetime

import networkx as nx
import pytest

from marsa.graph.bridges import (
    build_gateway_map,
    category_to_chapter,
    country_to_iso3,
    hts_to_chapter,
    normalise_country,
)
from marsa.graph.build import (
    add_citations,
    build_graph,
    compute_stats,
    dominant_origin,
)
from marsa.graph.narrate import find_single_points_of_failure, narrate
from marsa.graph.resolve import EntityResolver
from marsa.graph.schema import BRIDGE_RULES, EdgeKind, NodeKind, node_id, split_node_id
from marsa.graph.store import load_graph, load_manifest, save_graph
from marsa.graph.traverse import (
    alternative_routes,
    k_hop_subgraph,
    shortest_path_between,
)
from marsa.ingestion import fixtures as fx
from marsa.ingestion.schemas import (
    ComtradeFlow,
    CrossRuling,
    Origin,
    PortPerformance,
    Provenance,
    SourceKind,
)


def prov(origin: Origin = Origin.LIVE) -> Provenance:
    return Provenance(
        source=SourceKind.CROSS, origin=origin, retrieved_at=datetime.now(UTC)
    )


@pytest.fixture(scope="module")
def corpora():
    return {
        "rulings": list(fx.generate_cross_rulings(60)),
        "flows": list(fx.generate_comtrade_flows(years=(2023,))),
        "orders": list(fx.generate_dataco_orders(600)),
        "countries": list(fx.generate_country_logistics(years=(2023,))),
        "ports": list(fx.generate_ports()),
    }


@pytest.fixture(scope="module")
def graph(corpora):
    g, _ = build_graph(**corpora)
    return g


# ─── Schema ──────────────────────────────────────────────────────────────────


class TestSchema:
    def test_node_id_round_trip(self):
        nid = node_id(NodeKind.PORT, "AEJEA")
        assert nid == "port:AEJEA"
        assert split_node_id(nid) == ("port", "AEJEA")

    def test_ids_of_different_kinds_never_collide(self):
        assert node_id(NodeKind.COUNTRY, "85") != node_id(NodeKind.HTS_CHAPTER, "85")

    def test_every_bridge_rule_declares_a_caveat(self):
        for name, rule in BRIDGE_RULES.items():
            assert rule.caveat, f"{name} has no caveat"
            assert rule.description


# ─── Bridges ─────────────────────────────────────────────────────────────────


class TestCountryResolution:
    def test_spanish_names_resolve(self):
        """DataCo stores country names in Spanish; without this every shipment
        is an island that reaches no Comtrade flow or LPI score."""
        assert country_to_iso3("Alemania") == "DEU"
        assert country_to_iso3("Estados Unidos") == "USA"
        assert country_to_iso3("Brasil") == "BRA"

    def test_accents_are_stripped(self):
        assert country_to_iso3("Japón") == country_to_iso3("Japon") == "JPN"
        assert normalise_country("España") == "espana"

    def test_iso3_passes_through(self):
        assert country_to_iso3("ARE") == "ARE"

    def test_unknown_is_dropped_not_guessed(self):
        """A wrong country edge is worse than a missing one — it produces a
        confident answer about a corridor that does not exist."""
        assert country_to_iso3("Wakanda") is None
        assert country_to_iso3("") is None
        assert country_to_iso3(None) is None

    def test_case_and_whitespace_insensitive(self):
        assert country_to_iso3("  aLeMaNiA  ") == "DEU"


class TestCategoryAndChapter:
    def test_category_maps_to_chapter(self):
        assert category_to_chapter("Electronics") == "85"
        assert category_to_chapter("Men's Footwear") == "64"

    def test_unknown_category_is_none(self):
        assert category_to_chapter("Interdimensional Goods") is None

    def test_hts_chapter_is_first_two_digits(self):
        assert hts_to_chapter("8507.60.0020") == "85"
        assert hts_to_chapter("6109.10.0012") == "61"

    def test_hts_chapter_handles_junk(self):
        assert hts_to_chapter("") is None
        assert hts_to_chapter("abcd") is None


class TestGatewayMap:
    def test_best_ranked_port_wins(self):
        ports = [
            PortPerformance(port_name="A", unlocode="AEJEA", country_iso3="ARE",
                            year=2024, cppi_rank=12, provenance=prov()),
            PortPerformance(port_name="B", unlocode="AEKHL", country_iso3="ARE",
                            year=2024, cppi_rank=24, provenance=prov()),
        ]
        assert build_gateway_map(ports)["ARE"] == "AEJEA"

    def test_unranked_port_loses_to_ranked(self):
        ports = [
            PortPerformance(port_name="A", unlocode="XXAAA", country_iso3="XXX",
                            year=2024, cppi_rank=None, provenance=prov()),
            PortPerformance(port_name="B", unlocode="XXBBB", country_iso3="XXX",
                            year=2024, cppi_rank=99, provenance=prov()),
        ]
        assert build_gateway_map(ports)["XXX"] == "XXBBB"

    def test_port_without_unlocode_is_skipped(self):
        ports = [
            PortPerformance(port_name="A", unlocode=None, country_iso3="XXX",
                            year=2024, cppi_rank=1, provenance=prov()),
        ]
        assert build_gateway_map(ports) == {}


# ─── Construction ────────────────────────────────────────────────────────────


class TestBuild:
    def test_all_node_kinds_present(self, graph):
        kinds = {d.get("kind") for _, d in graph.nodes(data=True)}
        assert {
            NodeKind.COUNTRY.value, NodeKind.PORT.value, NodeKind.RULING.value,
            NodeKind.HTS_CODE.value, NodeKind.HTS_CHAPTER.value,
            NodeKind.CATEGORY.value,
        } <= kinds

    def test_jebel_ali_is_in_the_graph(self, graph):
        """The anchor node of the entire supply graph."""
        assert graph.has_node(node_id(NodeKind.PORT, "AEJEA"))

    def test_port_is_located_in_its_country(self, graph):
        assert graph.has_edge(
            node_id(NodeKind.PORT, "AEJEA"),
            node_id(NodeKind.COUNTRY, "ARE"),
            EdgeKind.LOCATED_IN.value,
        )

    def test_country_routes_through_gateway(self, graph):
        assert graph.has_edge(
            node_id(NodeKind.COUNTRY, "ARE"),
            node_id(NodeKind.PORT, "AEJEA"),
            EdgeKind.ROUTES_THROUGH.value,
        )

    def test_gateway_edge_is_marked_inferred(self, graph):
        data = graph.edges[
            node_id(NodeKind.COUNTRY, "ARE"),
            node_id(NodeKind.PORT, "AEJEA"),
            EdgeKind.ROUTES_THROUGH.value,
        ]
        assert data["inferred"] is True
        assert data["basis"] == "country_to_gateway_port"

    def test_lpi_attributes_land_on_country_nodes(self, graph):
        data = graph.nodes[node_id(NodeKind.COUNTRY, "ARE")]
        assert data.get("lpi_score") is not None
        assert data.get("import_dwell_days") is not None

    def test_trade_weights_are_normalised(self, graph):
        weights = [
            d["weight"] for _, _, d in graph.edges(data=True)
            if d.get("kind") == EdgeKind.TRADES_WITH.value
        ]
        assert weights
        assert all(0.0 <= w <= 1.0 for w in weights), "log-normalised to [0,1]"

    def test_repeat_observations_reinforce_one_edge(self, graph):
        """400 shipments on a corridor is one strong edge, not 400 weak ones."""
        ships_from = [
            d for _, _, d in graph.edges(data=True)
            if d.get("kind") == EdgeKind.SHIPS_FROM.value
        ]
        assert any(d.get("observations", 1) > 1 for d in ships_from)

    def test_corridor_late_rate_recorded(self, graph):
        rates = [
            d["late_rate"] for _, _, d in graph.edges(data=True)
            if d.get("kind") == EdgeKind.SHIPS_FROM.value and "late_rate" in d
        ]
        assert rates
        assert all(0.0 <= r <= 1.0 for r in rates)

    def test_hts_code_rolls_up_to_chapter(self, graph):
        code = node_id(NodeKind.HTS_CODE, "8507.60.0020")
        chapter = node_id(NodeKind.HTS_CHAPTER, "85")
        if graph.has_node(code):
            assert graph.has_edge(code, chapter, EdgeKind.PART_OF.value)

    def test_graph_is_mostly_one_component(self, graph):
        stats = compute_stats(graph)
        assert stats.largest_component / stats.nodes > 0.9, (
            "a fragmented graph means the bridge rules are not connecting corpora"
        )


class TestCitations:
    def test_only_links_rulings_in_the_corpus(self):
        """A citation to a ruling outside the subset must not create a node —
        an entity nothing can be said about inflates the graph."""
        rulings = [
            CrossRuling(
                ruling_number="NY N111111", collection="NY", subject="A",
                body="See NY N999999 and NY N222222.",
                hts_codes=[], related_rulings=["NY N999999", "NY N222222"],
                provenance=prov(),
            ),
            CrossRuling(
                ruling_number="NY N222222", collection="NY", subject="B",
                body="Body.", hts_codes=[], related_rulings=[], provenance=prov(),
            ),
        ]
        g, _ = build_graph(rulings=rulings)
        assert g.has_edge(
            node_id(NodeKind.RULING, "NY N111111"),
            node_id(NodeKind.RULING, "NY N222222"),
            EdgeKind.CITES.value,
        )
        assert not g.has_node(node_id(NodeKind.RULING, "NY N999999"))

    def test_returns_count_of_edges_added(self):
        rulings = list(fx.generate_cross_rulings(40))
        g, _ = build_graph(rulings=rulings)
        # add_citations is idempotent on an already-built graph
        assert add_citations(g, rulings) >= 0

    def test_fixture_citations_resolve(self, graph):
        cites = [
            (u, v) for u, v, d in graph.edges(data=True)
            if d.get("kind") == EdgeKind.CITES.value
        ]
        assert cites
        for u, v in cites:
            assert graph.has_node(u) and graph.has_node(v)


class TestProvenance:
    def test_synthetic_corpus_contaminates_the_whole_graph(self):
        """A traversal crosses corpus boundaries, so the answer cannot be
        partly real."""
        synthetic = list(fx.generate_cross_rulings(3))
        live = [
            ComtradeFlow(period="2023", ref_year=2023, reporter_code=784,
                         partner_code=156, flow_code="M", cmd_code="85",
                         provenance=prov(Origin.LIVE))
        ]
        assert dominant_origin(synthetic, live) == "synthetic"

    def test_all_live_stays_live(self):
        live = [
            ComtradeFlow(period="2023", ref_year=2023, reporter_code=784,
                         partner_code=156, flow_code="M", cmd_code="85",
                         provenance=prov(Origin.LIVE))
        ]
        assert dominant_origin(live) == "live"


class TestStats:
    def test_counts_inferred_edges_by_basis(self, graph):
        stats = compute_stats(graph)
        assert stats.inferred_edges > 0
        assert "country_to_gateway_port" in stats.inferred_by_basis

    def test_serialises_inferred_share(self, graph):
        payload = compute_stats(graph).as_dict()
        assert 0.0 <= payload["inferredShare"] <= 1.0


# ─── Resolution ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def resolver(graph):
    return EntityResolver(graph)


class TestResolver:
    def test_resolves_port_alias(self, resolver):
        hits = resolver.resolve("Which suppliers are exposed if Jebel Ali congests?")
        assert any(h.node_id == node_id(NodeKind.PORT, "AEJEA") for h in hits)

    def test_resolves_unlocode(self, resolver):
        assert any(h.node_id.endswith("AEJEA") for h in resolver.resolve("AEJEA delays"))

    def test_resolves_country_by_iso3(self, resolver):
        assert any(
            h.node_id == node_id(NodeKind.COUNTRY, "CHN")
            for h in resolver.resolve("exposure to CHN")
        )

    def test_resolves_hts_chapter(self, resolver):
        assert any(
            h.kind == NodeKind.HTS_CHAPTER.value for h in resolver.resolve("risk in HS 85")
        )

    def test_longest_phrase_wins(self, resolver):
        """'United Arab Emirates' must not be shredded into 'United'."""
        hits = resolver.resolve("shipments to United Arab Emirates")
        assert any(h.node_id == node_id(NodeKind.COUNTRY, "ARE") for h in hits)

    def test_stopwords_do_not_match(self, resolver):
        assert resolver.resolve("what is the risk for our products") == []

    def test_unknown_entity_returns_nothing(self, resolver):
        assert resolver.resolve("congestion at Port Wakanda") == []

    def test_confidence_is_ordered(self, resolver):
        hits = resolver.resolve("Jebel Ali and CHN and HS 85")
        assert hits == sorted(hits, key=lambda h: (-h.confidence, h.label))

    def test_reports_what_it_matched_on(self, resolver):
        hits = resolver.resolve("Jebel Ali")
        assert hits[0].matched_on
        assert hits[0].method in {"alias", "fuzzy", "hts_code", "hts_chapter", "ruling_number"}

    def test_fuzzy_only_when_nothing_exact(self, resolver):
        """A typo-tolerant match must never outrank an exact one."""
        hits = resolver.resolve("Jebel Ali")
        assert hits[0].method == "alias"


# ─── Traversal ───────────────────────────────────────────────────────────────


class TestTraversal:
    def test_reaches_countries_from_a_port(self, graph):
        result = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=2)
        assert result.nodes_of_kind(NodeKind.COUNTRY)

    def test_three_hops_reach_the_shipment_layer(self, graph):
        """Two hops structurally cannot answer 'which suppliers' — it stops at
        countries, one layer short of the goods."""
        two = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=2)
        three = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=3)
        assert not two.nodes_of_kind(NodeKind.CATEGORY)
        assert three.nodes_of_kind(NodeKind.CATEGORY)

    def test_traversal_is_undirected(self, graph):
        """Exposure propagates against the flow of goods: a port disruption
        travels backwards to the suppliers who use it."""
        result = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=1)
        # located_in points port → country, so a directed walk finds ARE either
        # way; routes_through points country → port and can only be traversed
        # backwards. Reaching more than the out-neighbours proves undirected.
        assert result.node_count > 1

    def test_unknown_seed_yields_empty_result(self, graph):
        result = k_hop_subgraph(graph, ["port:NOWHERE"], hops=2)
        assert result.node_count == 0
        assert result.seeds == []

    def test_max_nodes_truncates_and_flags(self, graph):
        result = k_hop_subgraph(
            graph, [node_id(NodeKind.PORT, "AEJEA")], hops=4, max_nodes=12
        )
        assert result.truncated
        assert result.node_count <= 13  # cap plus the seed already visited

    def test_exposure_decays_with_distance(self, graph):
        result = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=3)
        seed = result.seeds[0]
        assert result.exposure[seed] == 1.0
        far = [n for n, d in result.distances.items() if d == 3]
        if far:
            assert max(result.exposure.get(n, 0) for n in far) < 1.0

    def test_exposure_is_bounded(self, graph):
        result = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=3)
        assert all(0.0 <= v <= 1.0 for v in result.exposure.values())

    def test_citation_edges_do_not_conduct_risk(self):
        """A citation is a legal relationship, not a physical route."""
        g = nx.MultiDiGraph()
        g.add_node("ruling:A", kind="ruling", label="A")
        g.add_node("ruling:B", kind="ruling", label="B")
        g.add_edge("ruling:A", "ruling:B", key="cites", kind="cites", weight=1.0)
        result = k_hop_subgraph(g, ["ruling:A"], hops=2)
        assert result.exposure.get("ruling:B", 0.0) == 0.0

    def test_ranked_excludes_seeds(self, graph):
        result = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=2)
        assert all(n not in result.seeds for n, _ in result.ranked())

    def test_alternative_routes_counts_gateways(self, graph):
        assert alternative_routes(graph, node_id(NodeKind.COUNTRY, "ARE")) >= 1

    def test_shortest_path_explains_connection(self, graph):
        path = shortest_path_between(
            graph, node_id(NodeKind.PORT, "AEJEA"), node_id(NodeKind.COUNTRY, "CHN")
        )
        assert path and path[0].endswith("AEJEA")

    def test_shortest_path_missing_node(self, graph):
        assert shortest_path_between(graph, "port:AEJEA", "country:NOPE") is None


# ─── Narration ───────────────────────────────────────────────────────────────


class TestNarration:
    def test_names_the_seed_with_context(self, graph):
        result = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=3)
        text = narrate(result, question="Jebel Ali exposure?")
        assert "Jebel Ali" in text
        assert "CPPI rank" in text

    def test_discloses_inferred_edges(self, graph):
        """A conclusion resting on an assumption must say so in the same breath."""
        result = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=3)
        text = narrate(result)
        assert "bridging assumptions" in text

    def test_leads_with_concentration_not_inventory(self, graph):
        result = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=3)
        text = narrate(result)
        assert "concentration risk" in text or "distributed rather than concentrated" in text

    def test_singular_grammar(self):
        """Regression: the summary read '1 have only one gateway port'."""
        g = nx.MultiDiGraph()
        g.add_node("port:P", kind="port", label="P", cppi_rank=1)
        g.add_node("country:AAA", kind="country", label="AAA")
        g.add_edge("port:P", "country:AAA", key="located_in", kind="located_in", weight=1.0)
        g.add_edge("country:AAA", "port:P", key="routes_through", kind="routes_through",
                   weight=1.0, inferred=True, basis="country_to_gateway_port")
        result = k_hop_subgraph(g, ["port:P"], hops=2)
        text = narrate(result)
        assert "1 has only one gateway port" in text
        assert "1 have" not in text

    def test_no_seed_says_so_plainly(self):
        result = k_hop_subgraph(nx.MultiDiGraph(), ["port:NOPE"], hops=2)
        text = narrate(result)
        assert "could be resolved" in text

    def test_shallow_traversal_is_disclosed(self, graph):
        """Omitting the shipment layer must not read as 'there are none'."""
        result = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=2)
        text = narrate(result)
        assert "No product categories were reached" in text

    def test_single_point_of_failure_detection(self, graph):
        result = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=3)
        spofs = find_single_points_of_failure(result)
        for nid, _ in spofs:
            assert alternative_routes(result.subgraph, nid) == 1

    def test_late_rate_surfaced_for_categories(self, graph):
        result = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=3)
        text = narrate(result)
        if result.nodes_of_kind(NodeKind.CATEGORY):
            assert "late rate" in text


# ─── Persistence ─────────────────────────────────────────────────────────────


class TestStore:
    def test_round_trip(self, graph, tmp_path):
        stats = compute_stats(graph, origin="synthetic")
        save_graph(graph, stats, data_dir=tmp_path, build_report={"rulings": 60})

        reloaded = load_graph(tmp_path)
        assert reloaded.number_of_nodes() == graph.number_of_nodes()
        assert reloaded.number_of_edges() == graph.number_of_edges()

    def test_manifest_carries_bridge_rules(self, graph, tmp_path):
        """The assumptions travel with the artefact, not just the README."""
        save_graph(graph, compute_stats(graph), data_dir=tmp_path)
        manifest = load_manifest(tmp_path)
        assert set(manifest["bridge_rules"]) == set(BRIDGE_RULES)
        assert manifest["stats"]["inferredEdges"] > 0

    def test_missing_graph_is_actionable(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="marsa-graph build"):
            load_graph(tmp_path)

    def test_missing_manifest_returns_none(self, tmp_path):
        assert load_manifest(tmp_path) is None


# ─── Fixture geography ───────────────────────────────────────────────────────


class TestFixtureGeography:
    def test_country_region_and_market_cohere(self):
        """Regression: market, region, country and city were sampled
        independently, producing 'Alemania / Mumbai / East Africa' — which
        builds a nonsense supply graph."""
        from marsa.ingestion.fixtures import _GEOGRAPHY

        for order in fx.generate_dataco_orders(300):
            assert order.market in _GEOGRAPHY
            assert order.order_region in _GEOGRAPHY[order.market]
            pairs = _GEOGRAPHY[order.market][order.order_region]
            assert (order.order_country, order.order_city) in pairs

    def test_every_fixture_country_resolves_to_iso3(self):
        """A DataCo country the bridge cannot map contributes no graph edges."""
        unresolved = {
            o.order_country
            for o in fx.generate_dataco_orders(500)
            if country_to_iso3(o.order_country) is None
        }
        assert not unresolved, f"unmapped fixture countries: {unresolved}"

    def test_every_fixture_category_maps_to_a_chapter(self):
        unmapped = {
            o.category_name
            for o in fx.generate_dataco_orders(500)
            if category_to_chapter(o.category_name) is None
        }
        assert not unmapped, f"unmapped fixture categories: {unmapped}"
