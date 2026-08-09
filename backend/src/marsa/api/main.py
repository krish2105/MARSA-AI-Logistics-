"""FastAPI gateway.

Endpoints
    POST /query          — run the router, return the full audit record
    POST /query/stream   — same, streamed as SSE for the live Route Badge
    GET  /health         — readiness plus what the router can actually reach
    GET  /metrics        — Prometheus exposition
    GET  /cost           — running estimated-cost counter

Security baseline (spec section 7): CORS locked to a configured allowlist,
per-IP rate limiting via slowapi, and Pydantic v2 validation on every input.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse

from marsa.api.schemas import (
    ClassifyRequest,
    ClassifyResponse,
    CostSummary,
    HealthResponse,
    QueryRequest,
    QueryResponse,
)
from marsa.config import settings
from marsa.logging import configure, get_logger
from marsa.router.graph import Router
from marsa.router.llm import describe_llm_status

log = get_logger(__name__)

VERSION = "0.5.0"

# ─── Metrics ─────────────────────────────────────────────────────────────────
QUERIES = Counter("marsa_queries_total", "Queries routed", ["path", "query_class"])
LATENCY = Histogram(
    "marsa_query_latency_seconds",
    "End-to-end query latency",
    ["path"],
    # Bucketed around the spec's targets: the fast path claims <2s, the agentic
    # path is expected to be several seconds. Default buckets would put both in
    # the same bin and hide exactly the difference this project measures.
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0),
)
COST = Counter("marsa_estimated_cost_usd_total", "Estimated LLM cost", ["path"])
ERRORS = Counter("marsa_errors_total", "Errors by kind", ["kind"])

# ─── Running cost ledger ─────────────────────────────────────────────────────
_ledger: dict[str, dict[str, float]] = defaultdict(
    lambda: {"queries": 0.0, "costUsd": 0.0, "latencyMsTotal": 0.0}
)

limiter = Limiter(key_func=get_remote_address)
_router: Router | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the router once at startup.

    Index and graph load lazily inside the router, but compiling the StateGraph
    per request would add avoidable latency to the path whose whole claim is
    that it is fast.
    """
    global _router
    configure(level=settings.log_level, human=settings.environment == "development")
    log.info("starting gateway", extra={"version": VERSION, "env": settings.environment})
    _router = Router("auto")
    status = describe_llm_status()
    if not status["available"]:
        log.warning("no llm provider configured", extra={"note": status["note"]})
    yield
    log.info("gateway stopped")


app = FastAPI(
    title="MARSA AI",
    description="Adaptive-RAG trade & logistics copilot",
    version=VERSION,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS is an allowlist, never "*". The gateway is public and the browser is the
# only legitimate caller.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def get_router() -> Router:
    global _router
    if _router is None:  # pragma: no cover — only when lifespan did not run
        _router = Router("auto")
    return _router


def _record(audit: Any) -> None:
    QUERIES.labels(path=audit.path, query_class=audit.query_class).inc()
    LATENCY.labels(path=audit.path).observe(audit.latency_ms / 1000.0)
    COST.labels(path=audit.path).inc(audit.cost_usd)

    entry = _ledger[audit.path]
    entry["queries"] += 1
    entry["costUsd"] += audit.cost_usd
    entry["latencyMsTotal"] += audit.latency_ms

    log.info("query routed", extra=audit.as_dict())


# ─────────────────────────────────────────────────────────────────────────────


@app.post("/query", response_model=QueryResponse)
@limiter.limit(f"{settings.rate_limit_per_minute}/minute")
async def query(request: Request, payload: QueryRequest) -> Any:
    try:
        audit = get_router().run(payload.query)
    except Exception as exc:  # noqa: BLE001
        ERRORS.labels(kind=type(exc).__name__).inc()
        log.exception("query failed", extra={"query": payload.query[:200]})
        return JSONResponse(
            status_code=500,
            content={"detail": "Query failed", "error": type(exc).__name__},
        )

    _record(audit)
    return audit.as_dict()


@app.post("/query/stream")
@limiter.limit(f"{settings.rate_limit_per_minute}/minute")
async def query_stream(request: Request, payload: QueryRequest) -> EventSourceResponse:
    """SSE stream: `classified` → `step`* → `sources` → `answer` → `audit`.

    The classification lands first by design. Showing *why* a path was chosen
    before showing what it found is the demo — reversing it turns the router
    back into a chat box.
    """
    router = get_router()

    async def generate() -> AsyncIterator[dict[str, str]]:
        started = time.perf_counter()
        try:
            for event in router.stream(payload.query):
                if event["event"] == "audit":
                    audit_payload = event["data"]
                    _ledger_from_dict(audit_payload)
                yield {"event": event["event"], "data": json.dumps(event["data"])}
        except Exception as exc:  # noqa: BLE001
            ERRORS.labels(kind=type(exc).__name__).inc()
            log.exception("stream failed", extra={"query": payload.query[:200]})
            yield {
                "event": "error",
                "data": json.dumps({"detail": "Query failed", "error": type(exc).__name__}),
            }
        finally:
            log.info(
                "stream closed",
                extra={"elapsed_ms": round((time.perf_counter() - started) * 1000, 2)},
            )

    return EventSourceResponse(generate())


def _ledger_from_dict(payload: dict[str, Any]) -> None:
    path = str(payload.get("path", "unknown"))
    QUERIES.labels(path=path, query_class=str(payload.get("queryClass", "unknown"))).inc()
    LATENCY.labels(path=path).observe(float(payload.get("latencyMs", 0)) / 1000.0)
    COST.labels(path=path).inc(float(payload.get("costUsd", 0)))

    entry = _ledger[path]
    entry["queries"] += 1
    entry["costUsd"] += float(payload.get("costUsd", 0))
    entry["latencyMsTotal"] += float(payload.get("latencyMs", 0))


# ─────────────────────────────────────────────────────────────────────────────
# Phase J — grounded classification with calibrated abstention
# ─────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _classify_retriever():
    """Built once, on first use.

    Deliberately not built in `lifespan`: a gateway whose other endpoints work
    should not fail to boot because the fast-path index is missing.
    """
    from marsa.indexing.cli import _build_retriever

    return _build_retriever("auto", "auto")


@app.post("/classify", response_model=ClassifyResponse)
@limiter.limit(f"{settings.rate_limit_per_minute}/minute")
async def classify(request: Request, payload: ClassifyRequest) -> Any:
    """Classify a commodity, or decline and say what is missing.

    The response is never an uncited classification: `Suggestion` cannot be
    constructed without a citation, so the only two shapes reaching the client
    are a cited answer and a refusal carrying its near misses.
    """
    from marsa.classify.abstain import DEFAULT_THRESHOLD, decide
    from marsa.classify.evidence import assess

    threshold = payload.threshold if payload.threshold is not None else DEFAULT_THRESHOLD

    try:
        rulings, _ = _classify_retriever().retrieve(payload.query, limit=5)
    except Exception as exc:  # noqa: BLE001
        ERRORS.labels(kind=type(exc).__name__).inc()
        log.exception("classification retrieval failed")
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The fast-path index is unavailable, so no "
                "classification can be grounded.",
                "error": type(exc).__name__,
            },
        )

    signals = assess(payload.query, rulings)
    outcome = decide(payload.query, rulings, threshold=threshold, signals=signals)

    body = outcome.as_dict()
    body["evidence"] = signals.as_dict()
    log.info(
        "classification decided",
        extra={
            "query": payload.query[:200],
            "outcome": body["outcome"],
            "confidence": body["confidence"],
            "threshold": threshold,
        },
    )
    return body


@app.get("/health", response_model=HealthResponse)
async def health() -> Any:
    """Readiness, and an honest account of what is actually loaded."""
    from marsa.router.paths import _corpora, _fast_retriever, _supply_graph

    resources = {
        "fastPathIndex": _fast_retriever() is not None,
        "supplyGraph": _supply_graph() is not None,
        "comtradeFlows": len(_corpora().get("flows", [])),
        "dataCoOrders": len(_corpora().get("orders", [])),
    }
    ready = resources["fastPathIndex"] or resources["supplyGraph"]

    return {
        "status": "ok" if ready else "degraded",
        "version": VERSION,
        "environment": settings.environment,
        "resources": resources,
        "llm": describe_llm_status(),
    }


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/cost", response_model=CostSummary)
async def cost() -> Any:
    """Running estimated-cost counter (spec section 7).

    Priced at published per-token rates even on the free tier, so the
    cost-aware routing claim is a number rather than an assertion.
    """
    by_path = {
        path: {
            "queries": entry["queries"],
            "costUsd": round(entry["costUsd"], 8),
            "avgLatencyMs": round(
                entry["latencyMsTotal"] / entry["queries"] if entry["queries"] else 0.0, 2
            ),
            "avgCostUsd": round(
                entry["costUsd"] / entry["queries"] if entry["queries"] else 0.0, 8
            ),
        }
        for path, entry in _ledger.items()
    }
    return {
        "totalQueries": int(sum(e["queries"] for e in _ledger.values())),
        "totalCostUsd": round(sum(e["costUsd"] for e in _ledger.values()), 8),
        "byPath": by_path,
        "note": (
            "Estimated at published per-token rates. Free-tier usage bills $0; "
            "these figures exist to make the cost-aware routing claim measurable."
        ),
    }


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": "MARSA AI",
        "version": VERSION,
        "endpoints": [
            "/query",
            "/query/stream",
            "/classify",
            "/health",
            "/metrics",
            "/cost",
            "/docs",
        ],
    }
