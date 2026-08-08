"""The adaptive router, assembled as a LangGraph `StateGraph`.

    classify ──┬─ simple_factual      ──► fast_path   ─┐
               ├─ multi_hop_reasoning ──► agentic_path ─┼─► finalise ─► END
               └─ relationship_network ─► graph_path   ─┘

LangGraph earns its place here specifically for the conditional edge: the whole
project is an argument about *branching on query complexity*, and expressing
that branch as a first-class graph edge — rather than an `if` buried in a
handler — is what makes the routing decision inspectable, traceable and
testable in isolation.

`finalise` is shared rather than duplicated per path. It is where the single
audit record required by spec item 17 is assembled, so all three paths are
guaranteed to emit the same shape and Phase F can compare them without
reconciliation.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

from langgraph.graph import END, StateGraph

from marsa.duty.detect import detect as detect_duty
from marsa.duty.path import run_compute_path
from marsa.logging import get_logger
from marsa.router.classifier import Classification, QueryClass, build_classifier
from marsa.router.paths import run_agentic_path, run_fast_path, run_graph_path
from marsa.router.state import AuditRecord, RetrievalStep, RouterState, Source

log = get_logger(__name__)


def _classify_node(classifier) -> Callable[[RouterState], dict[str, Any]]:
    def node(state: RouterState) -> dict[str, Any]:
        started = time.perf_counter()
        classification = classifier.classify(state["query"])
        elapsed = (time.perf_counter() - started) * 1000

        log.info(
            "query classified",
            extra={
                "query_class": classification.query_class.value,
                "path": classification.path,
                "confidence": round(classification.confidence, 3),
                "is_llm": classification.is_llm,
                "latency_ms": round(elapsed, 2),
            },
        )
        classification.usage.latency_ms = classification.usage.latency_ms or elapsed

        # Gate G4: a duty question reaching an LLM produces confident, plausible,
        # wrong arithmetic — the highest-severity failure in this system. So the
        # decision is made deterministically and the classifier cannot override
        # it. The classifier is still *asked*, and its answer recorded, so the
        # routing thesis keeps a ground truth to be measured against: safety by
        # construction, measurement alongside.
        duty = detect_duty(state["query"])
        override = None
        if duty.is_duty_question:
            override = "compute"
            log.info(
                "compute route forced by pre-filter",
                extra={
                    "classifier_said": classification.path,
                    "agreed": classification.path == "compute",
                    "reasons": duty.reasons,
                },
            )

        return {
            "classification": classification,
            "path": override or classification.path,
            "duty_prefilter": duty.is_duty_question,
            "classifier_path": classification.path,
            "steps": [
                RetrievalStep(
                    "Classify",
                    (
                        f"duty question — routed to compute deterministically; "
                        f"classifier said {classification.query_class.value}"
                        if duty.is_duty_question
                        else f"{classification.query_class.value} "
                        f"({classification.confidence:.0%} confidence)"
                    ),
                    elapsed,
                )
            ],
        }

    return node


def _path_node(runner: Callable[[str], dict[str, Any]]) -> Callable[[RouterState], dict[str, Any]]:
    def node(state: RouterState) -> dict[str, Any]:
        result = runner(state["query"])
        return {
            "steps": [*state.get("steps", []), *result["steps"]],
            "sources": result["sources"],
            "answer": result["answer"],
            "retries": result.get("retries", 0),
            "evidence": result.get("evidence", []),
            "critic_verdict": result.get("warnings", []),
        }

    return node


def _finalise_node(state: RouterState) -> dict[str, Any]:
    """Assemble the one audit record every path shares (spec item 17)."""
    classification: Classification = state["classification"]
    steps: list[RetrievalStep] = state.get("steps", [])
    warnings = list(state.get("critic_verdict") or [])

    if not classification.is_llm:
        warnings.append("classifier_not_llm")

    audit = AuditRecord(
        query=state["query"],
        path=state.get("path", "unknown"),
        query_class=classification.query_class.value,
        confidence=classification.confidence,
        rationale=classification.rationale,
        classifier_method=classification.method,
        classifier_is_llm=classification.is_llm,
        classifier_latency_ms=classification.usage.latency_ms,
        steps=steps,
        sources=state.get("sources", []),
        answer=state.get("answer", ""),
        latency_ms=steps[-1].elapsed_ms if steps else 0.0,
        cost_usd=classification.usage.cost_usd,
        input_tokens=classification.usage.input_tokens,
        output_tokens=classification.usage.output_tokens,
        retries=state.get("retries", 0),
        fully_specified=classification.is_llm and "classifier_not_llm" not in warnings,
        warnings=warnings,
    )
    return {"audit": audit}


def _route(state: RouterState) -> str:
    if state.get("duty_prefilter"):
        return "compute_path"
    return {
        QueryClass.SIMPLE_FACTUAL: "fast_path",
        QueryClass.MULTI_HOP: "agentic_path",
        QueryClass.RELATIONSHIP: "graph_path",
    }[state["classification"].query_class]


def build_router(classifier_backend: str = "auto"):
    """Compile the routing StateGraph."""
    classifier = build_classifier(classifier_backend)

    builder = StateGraph(RouterState)
    builder.add_node("classify", _classify_node(classifier))
    builder.add_node("fast_path", _path_node(run_fast_path))
    builder.add_node("agentic_path", _path_node(run_agentic_path))
    builder.add_node("graph_path", _path_node(run_graph_path))
    builder.add_node("compute_path", _path_node(run_compute_path))
    builder.add_node("finalise", _finalise_node)

    builder.set_entry_point("classify")
    builder.add_conditional_edges(
        "classify",
        _route,
        {
            "fast_path": "fast_path",
            "agentic_path": "agentic_path",
            "graph_path": "graph_path",
            "compute_path": "compute_path",
        },
    )
    for path in ("fast_path", "agentic_path", "graph_path", "compute_path"):
        builder.add_edge(path, "finalise")
    builder.add_edge("finalise", END)

    return builder.compile()


class Router:
    """Convenience wrapper over the compiled graph."""

    def __init__(self, classifier_backend: str = "auto") -> None:
        self.backend = classifier_backend
        self._graph = build_router(classifier_backend)

    def run(self, query: str) -> AuditRecord:
        started = time.perf_counter()
        final = self._graph.invoke({"query": query, "steps": [], "sources": []})
        audit: AuditRecord = final["audit"]
        # Wall-clock, not the last step's lap — the step clock restarts inside
        # each path node and would otherwise under-report total latency.
        audit.latency_ms = (time.perf_counter() - started) * 1000
        return audit

    def stream(self, query: str) -> Iterator[dict[str, Any]]:
        """Yield events as nodes complete, for the SSE route visualisation.

        The Route Badge must appear *before* the retrieval steps — that
        sequencing is the demo — so the classification event is emitted the
        moment the classify node returns rather than being batched with the
        final answer.
        """
        started = time.perf_counter()
        emitted = 0
        audit: AuditRecord | None = None

        for update in self._graph.stream(
            {"query": query, "steps": [], "sources": []}, stream_mode="updates"
        ):
            for node_name, payload in update.items():
                if node_name == "classify":
                    classification: Classification = payload["classification"]
                    yield {"event": "classified", "data": classification.as_dict()}

                elif node_name == "finalise":
                    audit = payload["audit"]

                else:
                    # A path node returns the cumulative step list; emit only
                    # the ones the client has not seen.
                    for step in payload.get("steps", [])[emitted:]:
                        yield {"event": "step", "data": step.as_dict()}
                    emitted = len(payload.get("steps", []))

                    if payload.get("sources"):
                        yield {
                            "event": "sources",
                            "data": {"sources": [s.as_dict() for s in payload["sources"]]},
                        }

        if audit is not None:
            audit.latency_ms = (time.perf_counter() - started) * 1000
            yield {"event": "answer", "data": {"answer": audit.answer}}
            yield {"event": "audit", "data": audit.as_dict()}


def source_summary(sources: list[Source]) -> str:
    return ", ".join(s.ref for s in sources[:5]) or "none"
