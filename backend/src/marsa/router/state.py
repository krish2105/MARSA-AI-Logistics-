"""Shared router state and the audit record.

Spec item 17: *every path writes to the same structured audit log — query,
chosen path, sources retrieved, confidence, latency, estimated cost.* One
record type for all three paths is what makes the three-way comparison in
Phase F possible; a per-path schema would need reconciling before it could be
compared, and reconciliation is where honest numbers go to die.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict

from marsa.router.classifier import Classification


@dataclass
class RetrievalStep:
    """One visible stage, streamed to the Route Badge as it completes."""

    label: str
    detail: str
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "detail": self.detail,
            "elapsedMs": round(self.elapsed_ms, 2),
        }


@dataclass
class Source:
    """A citable source. `ref` is whatever a human would quote."""

    ref: str
    kind: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "kind": self.kind, "detail": self.detail}


@dataclass
class AuditRecord:
    """The single structured record every path emits."""

    query_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    query: str = ""
    path: str = "unknown"
    query_class: str = "unknown"
    confidence: float = 0.0
    rationale: str = ""
    classifier_method: str = "unknown"
    classifier_is_llm: bool = False

    steps: list[RetrievalStep] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    answer: str = ""

    latency_ms: float = 0.0
    classifier_latency_ms: float = 0.0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    #: Retries the agentic path performed (0 for the other two).
    retries: int = 0
    #: False when any stage ran on a non-semantic or non-LLM fallback.
    fully_specified: bool = True
    warnings: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, Any]:
        return {
            "queryId": self.query_id,
            "query": self.query,
            "path": self.path,
            "queryClass": self.query_class,
            "confidence": round(self.confidence, 4),
            "rationale": self.rationale,
            "classifier": {
                "method": self.classifier_method,
                "isLlm": self.classifier_is_llm,
                "latencyMs": round(self.classifier_latency_ms, 2),
            },
            "steps": [s.as_dict() for s in self.steps],
            "sources": [s.as_dict() for s in self.sources],
            "answer": self.answer,
            "latencyMs": round(self.latency_ms, 2),
            "costUsd": round(self.cost_usd, 8),
            "tokens": {"input": self.input_tokens, "output": self.output_tokens},
            "retries": self.retries,
            "fullySpecified": self.fully_specified,
            "warnings": self.warnings,
            "startedAt": self.started_at.isoformat(),
        }


class RouterState(TypedDict, total=False):
    """LangGraph state. Plain dict so LangGraph can merge partial updates."""

    query: str
    classification: Classification
    path: str
    steps: list[RetrievalStep]
    sources: list[Source]
    answer: str
    audit: AuditRecord
    #: Agentic path only.
    subqueries: list[str]
    evidence: list[dict[str, Any]]
    critic_verdict: str
    retries: int


class Stopwatch:
    """Elapsed-time helper for step timing."""

    def __init__(self) -> None:
        self.start = time.perf_counter()

    def lap(self) -> float:
        return (time.perf_counter() - self.start) * 1000
