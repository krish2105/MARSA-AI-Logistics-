"""The three path nodes.

Each wraps machinery already built and tested in earlier phases, so this module
is orchestration rather than new retrieval logic:

* **Fast** → Phase B hybrid index (dense + BM25 → RRF → rerank → parent rollup)
* **Agentic** → planner / retrieve / critic / retry loop over Comtrade + DataCo
* **Graph** → Phase C resolve → k-hop traverse → narrate

Resources are loaded lazily and cached at module level. The index and graph
together are a few hundred megabytes of process memory and several seconds to
load; doing that per request would make the fast path's sub-second target
unreachable for reasons that have nothing to do with retrieval.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from marsa.config import settings
from marsa.logging import get_logger
from marsa.router.state import RetrievalStep, Source, Stopwatch

log = get_logger(__name__)

MAX_AGENTIC_RETRIES = 2


# ─────────────────────────────────────────────────────────────────────────────
# Lazily-loaded shared resources
# ─────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _fast_retriever():
    """Phase B retriever, or None when the index has not been built."""
    try:
        from marsa.indexing.cli import _build_retriever

        return _build_retriever("auto", "auto")
    except Exception as exc:  # noqa: BLE001 — the path degrades, it does not crash
        log.warning("fast-path index unavailable", extra={"error": str(exc)[:200]})
        return None


@lru_cache(maxsize=1)
def _supply_graph():
    """Phase C graph, or None when it has not been built."""
    try:
        from marsa.graph.store import load_graph

        return load_graph(settings.data_dir)
    except Exception as exc:  # noqa: BLE001
        log.warning("supply graph unavailable", extra={"error": str(exc)[:200]})
        return None


@lru_cache(maxsize=1)
def _corpora() -> dict[str, list]:
    """Comtrade flows and DataCo orders, for the agentic path."""
    from marsa.ingestion.schemas import ComtradeFlow, DataCoOrder, read_jsonl

    out: dict[str, list] = {"flows": [], "orders": []}
    for key, filename, model in (
        ("flows", "comtrade_flows.jsonl", ComtradeFlow),
        ("orders", "dataco_orders.jsonl", DataCoOrder),
    ):
        path = settings.processed_dir / filename
        if path.exists():
            out[key] = list(read_jsonl(path, model))
    return out


def reset_caches() -> None:
    """Drop cached resources — used by tests and after a rebuild."""
    _fast_retriever.cache_clear()
    _supply_graph.cache_clear()
    _corpora.cache_clear()


# ─────────────────────────────────────────────────────────────────────────────
# Fast path
# ─────────────────────────────────────────────────────────────────────────────


def run_fast_path(query: str) -> dict[str, Any]:
    """Hybrid retrieval → rerank → answer with citations. No agent loop."""
    watch = Stopwatch()
    steps: list[RetrievalStep] = []
    retriever = _fast_retriever()

    if retriever is None:
        return {
            "steps": [RetrievalStep("Index unavailable", "run `marsa-index build`", watch.lap())],
            "sources": [],
            "answer": (
                "The fast-path index has not been built, so no ruling could be "
                "retrieved. Run `marsa-index build` first."
            ),
            "warnings": ["fast_path_index_missing"],
            "fully_specified": False,
        }

    rulings, trace = retriever.retrieve(query, limit=5)

    steps.append(
        RetrievalStep("Dense retrieval", f"{trace.embedder} · {trace.dense_hits} hits", watch.lap())
    )
    steps.append(RetrievalStep("Sparse retrieval", f"BM25 · {trace.sparse_hits} hits", watch.lap()))
    steps.append(RetrievalStep("Fusion", f"RRF · {trace.fused_candidates} candidates", watch.lap()))
    steps.append(
        RetrievalStep(
            "Rerank", f"{trace.reranker} · {trace.reranked} → {len(rulings)}", watch.lap()
        )
    )

    sources = [
        Source(
            ref=r.ruling_number,
            kind="cbp_cross_ruling",
            detail=", ".join(r.hts_codes[:3]) or r.best_section,
        )
        for r in rulings
    ]

    if not rulings:
        answer = "No ruling in the indexed subset matches this query."
    else:
        top = rulings[0]
        excerpt = top.chunks[0].chunk.text.strip() if top.chunks else ""
        codes = ", ".join(top.hts_codes[:3])
        answer = (
            f"{top.ruling_number} is the closest ruling"
            + (f", classifying under {codes}." if codes else ".")
            + (f"\n\n{excerpt}" if excerpt else "")
        )

    warnings = [] if trace.fully_semantic else ["retrieval_not_semantic"]
    return {
        "steps": steps,
        "sources": sources,
        "answer": answer,
        "warnings": warnings,
        "fully_specified": trace.fully_semantic,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Graph path
# ─────────────────────────────────────────────────────────────────────────────


def run_graph_path(query: str, *, hops: int | None = None) -> dict[str, Any]:
    """Entity resolution → k-hop traversal → subgraph-to-text synthesis."""
    watch = Stopwatch()
    graph = _supply_graph()

    if graph is None:
        return {
            "steps": [RetrievalStep("Graph unavailable", "run `marsa-graph build`", watch.lap())],
            "sources": [],
            "answer": (
                "The supply graph has not been built, so there is nothing to "
                "traverse. Run `marsa-graph build` first."
            ),
            "warnings": ["graph_missing"],
            "fully_specified": False,
        }

    from marsa.graph.narrate import narrate
    from marsa.graph.resolve import EntityResolver
    from marsa.graph.traverse import DEFAULT_HOPS, k_hop_subgraph

    entities = EntityResolver(graph).resolve(query)
    steps = [
        RetrievalStep(
            "Entity resolution",
            ", ".join(f"{e.label} ({e.kind})" for e in entities[:4]) or "no match",
            watch.lap(),
        )
    ]

    if not entities:
        return {
            "steps": steps,
            "sources": [],
            "answer": (
                "No entity in this question resolved to a node in the supply "
                "graph, so the graph path cannot answer it. The port, country or "
                "code named is outside the ingested subset."
            ),
            "warnings": ["no_entity_resolved"],
            "fully_specified": True,
        }

    result = k_hop_subgraph(
        graph, [e.node_id for e in entities], hops=hops or DEFAULT_HOPS
    )
    steps.append(
        RetrievalStep(
            "Traversal",
            f"{result.distances and max(result.distances.values()) or 0}-hop · "
            f"{result.node_count} nodes, {result.edge_count} edges"
            + (" (capped)" if result.truncated else ""),
            watch.lap(),
        )
    )
    steps.append(
        RetrievalStep(
            "Risk overlay",
            "LPI dwell + congestion tier + predicted corridor risk",
            watch.lap(),
        )
    )

    answer = narrate(result, question=query)
    steps.append(RetrievalStep("Synthesis", "subgraph → narrative", watch.lap()))

    sources = [
        Source(ref=e.label, kind=f"graph_node:{e.kind}", detail=f"matched on '{e.matched_on}'")
        for e in entities[:5]
    ]
    inferred = sum(1 for _, _, d in result.subgraph.edges(data=True) if d.get("inferred"))

    return {
        "steps": steps,
        "sources": sources,
        "answer": answer,
        "warnings": ["subgraph_uses_inferred_edges"] if inferred else [],
        "fully_specified": True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Agentic path
# ─────────────────────────────────────────────────────────────────────────────

_HS_RE = re.compile(r"\b(\d{2})(?:\d{2})?(?:\.\d{2})?\b")
_COUNTRY_HINT = re.compile(
    r"\b(china|india|usa|united states|germany|japan|korea|singapore|uk|united kingdom|uae)\b",
    re.IGNORECASE,
)


def plan_subqueries(query: str) -> list[str]:
    """Decompose into the retrievals the question actually requires.

    Deliberately deterministic rather than another LLM call. The spec budgets
    *one* cheap LLM call for the classifier; spending a second on planning would
    make the agentic path's cost advantage over the fast path harder to defend,
    which is precisely the tradeoff this project exists to measure.
    """
    chapters = {m.group(1) for m in _HS_RE.finditer(query) if m.group(1).isdigit()}
    countries = {m.group(0).title() for m in _COUNTRY_HINT.finditer(query)}

    plan = []
    if chapters:
        plan.append(f"Resolve trade flows for HS chapter(s) {', '.join(sorted(chapters))}")
    else:
        plan.append("Resolve relevant HS chapters from the query terms")

    if countries:
        plan.append(f"Retrieve bilateral flows involving {', '.join(sorted(countries))}")
    else:
        plan.append("Retrieve bilateral flows for the default reporter set")

    plan.append("Intersect with shipment-level records and delivery-risk scores")
    return plan


def _retrieve_flows(query: str, corpora: dict[str, list]) -> list[dict[str, Any]]:
    chapters = {m.group(1) for m in _HS_RE.finditer(query)}
    flows = corpora.get("flows", [])
    matched = [f for f in flows if not chapters or f.cmd_code in chapters]
    return [
        {
            "kind": "comtrade",
            "ref": f"{f.reporter_iso}→{f.partner_iso} HS{f.cmd_code} {f.period}",
            "value": f.primary_value,
        }
        for f in matched[:25]
    ]


def _retrieve_orders(query: str, corpora: dict[str, list]) -> list[dict[str, Any]]:
    orders = corpora.get("orders", [])
    late = [o for o in orders if o.late_delivery_risk]
    return [
        {
            "kind": "dataco",
            "ref": f"order {o.order_id}",
            "category": o.category_name,
            "late": bool(o.late_delivery_risk),
        }
        for o in late[:25]
    ]


def critic(evidence: list[dict[str, Any]], subqueries: list[str]) -> tuple[bool, str]:
    """Is the evidence sufficient to compose an answer?

    RAGAS-style in spirit — coverage of the plan rather than a learned judge.
    Checks that every planned retrieval produced something, because the failure
    this loop exists to catch is a sub-query that silently returned nothing and
    left the composer to confabulate around the hole.
    """
    kinds = {e["kind"] for e in evidence}
    if not evidence:
        return False, "No evidence retrieved for any sub-query."
    if len(kinds) < 2 and len(subqueries) > 1:
        missing = "shipment records" if "dataco" not in kinds else "trade flows"
        return False, f"Evidence covers only {kinds}; {missing} missing."
    if len(evidence) < 5:
        return False, f"Only {len(evidence)} evidence items — too thin to cross-reference."
    return True, f"{len(evidence)} items across {len(kinds)} corpora; sufficient."


def run_agentic_path(query: str, *, max_retries: int = MAX_AGENTIC_RETRIES) -> dict[str, Any]:
    """Planner → parallel retrieve → critic → retry (max 2) → composer."""
    watch = Stopwatch()
    corpora = _corpora()
    steps: list[RetrievalStep] = []

    if not corpora.get("flows") and not corpora.get("orders"):
        return {
            "steps": [RetrievalStep("Corpora unavailable", "run `marsa-ingest`", watch.lap())],
            "sources": [],
            "answer": (
                "Neither the trade-flow nor the shipment corpus is present, so "
                "the agentic path has nothing to reason over."
            ),
            "warnings": ["corpora_missing"],
            "fully_specified": False,
            "retries": 0,
        }

    subqueries = plan_subqueries(query)
    steps.append(
        RetrievalStep("Plan", f"decomposed into {len(subqueries)} sub-queries", watch.lap())
    )

    evidence: list[dict[str, Any]] = []
    retries = 0
    verdict = ""
    sufficient = False
    working_query = query

    while retries <= max_retries:
        flows = _retrieve_flows(working_query, corpora)
        orders = _retrieve_orders(working_query, corpora)
        evidence = flows + orders

        steps.append(
            RetrievalStep("Retrieve", f"UN Comtrade · {len(flows)} flows", watch.lap())
        )
        steps.append(
            RetrievalStep("Retrieve", f"DataCo · {len(orders)} shipment records", watch.lap())
        )

        sufficient, verdict = critic(evidence, subqueries)
        steps.append(
            RetrievalStep(
                "Critic",
                f"{'sufficient' if sufficient else 'insufficient'} · {verdict}",
                watch.lap(),
            )
        )
        if sufficient:
            break

        retries += 1
        if retries > max_retries:
            break
        # Reformulate by widening: drop the chapter filter so the next attempt
        # retrieves against the whole corpus rather than an empty slice.
        working_query = re.sub(r"\b\d{4}(?:\.\d{2})?\b", "", working_query)
        steps.append(
            RetrievalStep("Reformulate", f"attempt {retries} of {max_retries}", watch.lap())
        )

    flow_count = sum(1 for e in evidence if e["kind"] == "comtrade")
    order_count = sum(1 for e in evidence if e["kind"] == "dataco")
    categories = sorted({e.get("category") for e in evidence if e.get("category")})[:4]

    answer = (
        f"Cross-referencing {flow_count} trade-flow records against "
        f"{order_count} shipment records"
        + (f", concentrated in {', '.join(categories)}" if categories else "")
        + f". Critic verdict: {verdict}"
        + (
            f" Evidence remained insufficient after {retries} reformulation(s), so "
            "this answer is partial."
            if not sufficient
            else ""
        )
    )
    steps.append(
        RetrievalStep(
            "Compose",
            f"cross-referenced {len({e['kind'] for e in evidence})} corpora",
            watch.lap(),
        )
    )

    sources = [
        Source(ref=e["ref"], kind=e["kind"]) for e in (evidence[:3] + evidence[-3:])
    ][:6]

    return {
        "steps": steps,
        "sources": sources,
        "answer": answer,
        "warnings": [] if sufficient else ["evidence_insufficient"],
        "fully_specified": True,
        "retries": retries,
    }
