# MARSA AI — Backend (placeholder)

The FastAPI + LangGraph service lands in a later phase. This directory exists so
the monorepo shape matches the master spec's build order from the first commit.

Planned contents, in build order:

| Phase | Contents |
|---|---|
| A | `ingestion/` — CBP CROSS scraper, UN Comtrade puller, DataCo + World Bank LPI loaders |
| B | `indexing/` — parent-child chunking, MiniLM embeddings → pgvector, BM25 index, cross-encoder reranker |
| C | `graph/` — NetworkX supplier/product/port/country graph builder + pickle persistence |
| D | `ml/` — late-delivery-risk classifier (LogReg vs XGBoost vs LightGBM), port-congestion tiering |
| E | `router/` — LangGraph complexity classifier + fast / agentic / graph path nodes |
| E | `api/` — FastAPI gateway, SSE streaming, `/health`, `/metrics`, audit log |
| F | `eval/` — 60-query labelled routing set, RAGAS harness, cost/latency benchmark → `RESULTS.md` |

Until then the frontend runs entirely against local mock fixtures, so Phase 1 is
demoable with no backend running.
