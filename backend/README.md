# MARSA AI — Backend

FastAPI + LangGraph service. **Phase A (data ingestion) is implemented**; the
router and API land in later phases.

## Quick start

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                    # 45 tests, fully offline
ruff check src tests

marsa-ingest fixtures     # synthetic corpora — no network needed
marsa-ingest status       # what's on disk, and is it real?
```

## Phase A — ingestion

| Command | Source | Auth | Notes |
|---|---|---|---|
| `marsa-ingest probe-cross` | rulings.cbp.gov | none | **Run first.** Verifies the undocumented API contract |
| `marsa-ingest cross` | rulings.cbp.gov | none | ~3,000 ruling subset |
| `marsa-ingest comtrade` | comtradeapi.un.org | none | 288-request grid, 500 records max each |
| `marsa-ingest dataco --download` | Kaggle | **required** | Cannot be fetched anonymously |
| `marsa-ingest worldbank` | api.worldbank.org | none | LPI indicators; `--cppi-csv` for ports |
| `marsa-ingest fixtures` | — | none | Seeded synthetic corpora |
| `marsa-ingest export-report` | — | none | Publishes manifests to the frontend `/data` page |

Output lands in `data/processed/*.jsonl` with a `*.manifest.json` beside each
one recording origin, record count, upstream request count, content hash, and
the subsetting rationale.

### Two contract details worth knowing

**Comtrade takes `reportercode`, lowercase `c`** — while every other parameter
is camelCase. This was read out of the official `comtradeapicall` package
source (v1.3.2, `PreviewGet.getPreviewData`), not from prose docs. Sending
`reporterCode` is silently ignored and the API returns data for *all*
reporters: a wrong answer that looks like a working one. There is a test
asserting the wire parameter.

**CROSS has no published API.** The client accepts several key spellings and
raises `UpstreamShapeError` naming the keys actually returned rather than
writing an empty corpus. Run `probe-cross` before a long scrape; if the shape
has changed, the fix is confined to `_SEARCH_KEYS` / `_RULING_KEYS` in
`cross.py`.

### Politeness

Every ingestor shares one `Fetcher`: token-bucket rate limiting (CROSS 2 rps,
Comtrade 1 rps), exponential backoff with jitter, `Retry-After` honoured, an
honest `User-Agent`, and an on-disk response cache that makes a long scrape
resumable — a run that dies at request 2,400 of 3,000 resumes for free.

4xx is deliberately **not** retried: it means our request is malformed, and
retrying five times just wastes a public service's capacity reaching the same
answer.

## Fixtures

`marsa-ingest fixtures` generates seeded corpora that validate against the same
schemas as the live ingestors, so Phases B–F can be built and tested offline.

Two rules, enforced rather than documented:

1. Every record carries `origin=SYNTHETIC`, surfaced in the manifest, in
   `marsa-ingest status`, and in the frontend `/data` page.
2. **No benchmark figure in `RESULTS.md` may be derived from them.** Fixtures
   prove the pipeline runs; they cannot prove the router works.

The DataCo generator embeds a genuinely learnable late-delivery signal (shipping
mode, scheduled window, congested corridor) with realistic label noise, so Phase
D's LogReg vs XGBoost vs LightGBM comparison exercises real model behaviour
rather than fitting noise. Tests assert both that the signal exists and that it
is *not* perfectly separable.

## Layout

```
src/marsa/
  config.py                  # pydantic-settings, data paths, rate limits
  logging.py                 # structured JSON logs
  ingestion/
    schemas.py               # normalised Pydantic v2 models + Provenance
    fetcher.py               # rate limit, retry, cache, error classification
    cross.py                 # CBP CROSS rulings
    comtrade.py              # UN Comtrade preview tier
    dataco.py                # DataCo CSV / Kaggle
    worldbank.py             # LPI 2.0 + CPPI
    fixtures.py              # seeded synthetic corpora
    cli.py                   # typer CLI
tests/                       # 45 tests, respx-mocked, no network
```

## Still to come

| Phase | Contents |
|---|---|
| B | `indexing/` — parent-child chunking, MiniLM → pgvector, BM25, cross-encoder reranker |
| C | `graph/` — NetworkX supplier/product/port/country graph + pickle |
| D | `ml/` — late-delivery classifier, port-congestion tiering |
| E | `router/` + `api/` — LangGraph router, FastAPI, SSE, `/health`, `/metrics` |
| F | `eval/` — 60-query labelled set, RAGAS, cost/latency benchmark → `RESULTS.md` |
