"""Pydantic v2 request/response models.

Spec section 7 requires input validation on every endpoint. The constraints
below are the ones that actually matter for a public gateway: a bounded query
length (an unbounded string is a denial-of-service vector against the LLM
classifier, which is billed per token), and a closed enum for the path override
so a caller cannot dispatch to an arbitrary node name.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

MAX_QUERY_CHARS = 2000


class PathName(StrEnum):
    FAST = "fast"
    AGENTIC = "agentic"
    GRAPH = "graph"


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=MAX_QUERY_CHARS)
    #: Force a path, bypassing the classifier. Exists for the Phase F benchmark,
    #: which must be able to run the same query down all three paths to compare
    #: them — not for production callers.
    force_path: PathName | None = None

    @field_validator("query")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("query cannot be blank")
        return cleaned


class StepModel(BaseModel):
    label: str
    detail: str
    elapsedMs: float


class SourceModel(BaseModel):
    ref: str
    kind: str
    detail: str = ""


class ClassifierModel(BaseModel):
    method: str
    isLlm: bool
    latencyMs: float


class TokensModel(BaseModel):
    input: int
    output: int


class QueryResponse(BaseModel):
    """Mirrors AuditRecord exactly — one shape for API and audit log alike."""

    queryId: str
    query: str
    path: str
    queryClass: str
    confidence: float
    rationale: str
    classifier: ClassifierModel
    steps: list[StepModel]
    sources: list[SourceModel]
    answer: str
    latencyMs: float
    costUsd: float
    tokens: TokensModel
    retries: int
    fullySpecified: bool
    warnings: list[str]
    startedAt: str


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    resources: dict[str, Any]
    llm: dict[str, Any]


class CostSummary(BaseModel):
    """The running cost counter from spec section 7."""

    totalQueries: int
    totalCostUsd: float
    byPath: dict[str, dict[str, float]]
    note: str
