# MARSA AI — Backend

FastAPI + LangGraph service. **Phases A–F are implemented** — ingestion,
fast-path index, supply graph, risk models, the LangGraph router behind a
FastAPI gateway, and the evaluation harness that grades all of it.

## Quick start

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                    # 348 tests
ruff check src tests
./scripts/ci-local.sh     # everything CI runs, with CI's parameters

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

---

## Phase B — the fast-path index

```bash
marsa-index build      # chunk → embed → pgvector + BM25
marsa-index query "What HTS code applies to lithium-ion power banks?"
marsa-index stats      # chunk counts by section, backend in use
```

### Pipeline

```
query
  ├─ dense  (embed → pgvector cosine top-20)
  └─ sparse (BM25 top-20)
        ↓  reciprocal rank fusion
        ↓  rerank (top-12)
        ↓  child → parent rollup
  whole rulings, with citations
```

### Parent-child chunking

A CROSS ruling has a fixed rhetorical shape — describe, issue, reason, hold.
Nearly all answer-bearing signal is in the last two sections; most of the token
count is in the first two. Embedding whole rulings therefore dilutes exactly the
text a classification question asks about.

So **children are embedded, parents are returned**: each child is one reasoning
or holding paragraph, and the fast path hands the LLM the whole ruling so the
citation is quotable rather than a fragment.

Section weight decides which *ruling* wins at rollup:

| Section | Weight | |
|---|---|---|
| `HOLDING` | 1.00 | the operative subheading |
| `LAW AND ANALYSIS` | 0.92 | the reasoning |
| `SUBJECT` | 0.80 | states product + origin in the shape users ask |
| `ISSUE` | 0.75 | the question, not the answer |
| `DESCRIPTION OF MERCHANDISE` | 0.62 | background |

### Why RRF and not weighted score blending

Dense cosine similarity lives in [-1, 1] and clusters around 0.2–0.6. BM25 is
unbounded and scales with corpus statistics and query length. Blending them
means normalising two distributions whose shapes vary per query — and min-max
normalisation is dominated by whichever list contains an outlier.

Reciprocal rank fusion (`k=60`, Cormack et al.) discards magnitudes and uses
only rank. No tuning, robust to either retriever misbehaving. The cost is real:
RRF throws away confidence, so a dense hit at 0.95 and one at 0.35 contribute
identically at equal rank. The rerank stage restores a calibrated ordering.

### Backends

| Component | Spec / intended | Offline fallback |
|---|---|---|
| Embeddings | `all-MiniLM-L6-v2`, 384d | hashed word+char n-grams, 384d |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | lexical heuristic |
| Vector store | Postgres + pgvector (HNSW, cosine) | numpy exact search |

Both embedders emit **384 dimensions** deliberately — the pgvector column, the
HNSW index and every stored row are identical across backends, so swapping the
fallback for real MiniLM is a re-embed, not a schema migration.

The fallbacks exist because huggingface.co is unreachable in some environments
(including this project's build sandbox). The hashed embedder is a real
technique — signed feature hashing, L2-normalised — not a stub, so the whole
pipeline is exercised faithfully. What it is **not** is semantic: it cannot
match "power bank" to "portable battery charger". `is_semantic` is threaded
through the retrieval trace and index manifest so no quality figure can be
published from a fallback run by accident.

Install the real path with `pip install -e ".[ml]"` (pulls torch — kept optional
so a blocked-egress environment is not forced to install it).

### Three bugs worth knowing about

**`HOLDING` was being deleted from the index.** A uniform 25-word minimum, meant
to suppress noise fragments, silently dropped every holding — and a holding is
routinely two sentences ("The applicable subheading will be 8507.60.0020. The
rate of duty will be free."). That is 15 words and the single most important
sentence in the ruling. Minimums are now section-aware; chunk count doubled.

**psycopg cannot run multi-statement SQL as a prepared statement**, and
`VECTOR(n)` is a *type modifier* so it cannot be a bind parameter at all.
Schema statements are issued individually with the dimension coerced via `int()`.

**The "never lose a ruling" guard was a no-op** — the fallback path ran through
the same length filter it existed to bypass, so terse ruling bodies still
vanished. Caught by a test, not by review.

---

## Phase F — evaluation

```bash
marsa-eval dataset      # validate the labelled set (balance, dupes, rationales)
marsa-eval routing      # accuracy, confusion, error direction
marsa-eval benchmark    # every query down every path, timed
marsa-eval run          # everything → ../RESULTS.md
```

### The gate

`report.evaluate_gate()` decides whether a run may publish headline figures at
all. It requires the classifier to be the few-shot LLM, the corpora to be real,
and an LLM judge to exist. When any precondition fails the report is written
anyway — the pipeline works — but stamped `PROVISIONAL` with the blockers
rendered above the first number.

This exists because 78.3% reads identically whether it came from the specified
classifier or from a rule table, and only one of those is the project's result.
CI asserts the gate *holds*: a `FINAL` stamp from CI's synthetic corpora would
mean the gate had stopped working.

### Three measurement decisions

**Every query runs down every path.** Timing only the path the router picked
measures "how fast was the path we happened to choose", which is not a
comparison. Forcing all three gives the counterfactual.

**Cold and warm runs are separated.** The first call to any path pays to load an
index, a graph or a corpus. Folding that into the mean makes whichever path ran
first look slowest — a measurement artefact, not a property of the system.

**The judge is a different provider from the generator.** A model asked to grade
its own output grades it generously, so `RagasJudge` takes the *second*
configured provider. With no provider at all, faithfulness and answer relevance
report `not_measured` — never `0.0`, which would read as a bad score rather than
an absent one.

---

## Layout

```
src/marsa/
  config.py                  # pydantic-settings, data paths, rate limits
  logging.py                 # structured JSON logs
  ingestion/
    schemas.py               # normalised Pydantic v2 models + Provenance
    fetcher.py               # rate limit, retry, cache, error classification
    cross.py / comtrade.py / dataco.py / worldbank.py
    fixtures.py              # seeded synthetic corpora
    cli.py                   # marsa-ingest
  indexing/
    chunking.py              # parent-child splitter, section weights
    embeddings.py            # MiniLM + hashed n-gram fallback
    store.py                 # pgvector (HNSW) + numpy exact search
    sparse.py                # BM25 with an HTS-safe tokenizer
    rerank.py                # cross-encoder + lexical fallback
    hybrid.py                # RRF fusion, parent rollup, retrieval trace
    cli.py                   # marsa-index
  graph/
    schema.py                # node/edge kinds, typed IDs, bridge-rule registry
    bridges.py               # every cross-corpus join, in one auditable module
    build.py                 # assemble from all four corpora
    resolve.py               # query text → graph nodes
    traverse.py              # k-hop subgraph, damped risk propagation
    narrate.py               # subgraph → prose, with caveats inline
    store.py                 # pickle + manifest
    cli.py                   # marsa-graph
  ml/
    features.py              # order-time features + the leakage blocklist
    dataset.py               # temporal split
    models.py                # LogReg / XGBoost / LightGBM + metrics
    train.py                 # comparison harness, leakage demonstration
    congestion.py            # rule-based port congestion tiering
    enrich.py                # folds both models back into the graph
    artifacts.py             # model pickle + model card
    cli.py                   # marsa-ml
  router/
    llm.py                   # LiteLLM over Groq/Gemini/Cerebras + cost accounting
    classifier.py            # few-shot LLM classifier + heuristic fallback
    paths.py                 # the three path nodes
    state.py                 # shared state + the single audit record
    graph.py                 # LangGraph StateGraph + streaming
  api/
    main.py                  # FastAPI gateway, SSE, metrics, cost ledger
    schemas.py               # Pydantic v2 request/response
  eval/
    dataset.py               # 60 hand-labelled queries, each with a rationale
    routing.py               # accuracy, confusion, error direction, calibration
    quality.py               # RAGAS, partitioned by what needs an LLM judge
    benchmark.py             # every query down every path; cold/warm separated
    report.py                # RESULTS.md + the publication gate
    cli.py                   # marsa-eval
tests/                       # 348 tests; 18 hit real Postgres, rest offline
```
