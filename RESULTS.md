# RESULTS

_Generated 2026-08-09 00:27 UTC · status **PROVISIONAL**_

> ## ⚠ PROVISIONAL — these numbers do not test the thesis
>
> This run exercised the full pipeline end to end, which is worth showing.
> It does **not** answer the question this project exists to ask, for the
> reasons listed immediately below. Every figure here is annotated with what
> it actually describes.


## Why this run cannot settle the question

1. **The classifier is not the LLM.** Routing accuracy below measures a deterministic rule table, not the few-shot LLM call the spec specifies. The two are not comparable and this figure must not be reported as the classifier's accuracy. Set `GROQ_API_KEY` or `GEMINI_API_KEY` and re-run.
2. **No LLM judge is available.** RAGAS faithfulness and answer relevance are reported as `not_measured` rather than zero. Context precision and recall are deterministic and are measured.

## Test set

60 hand-labelled queries, 20 `simple_factual`, 20 `multi_hop_reasoning`, 20 `relationship_network`.

Difficulty: 48 clear, 7 moderate, 5 ambiguous. The ambiguous cases are deliberate — a set of clear-cut queries cannot surface the systematic misrouting this section exists to report.

## Routing accuracy

> Measuring the **heuristic fallback**, not the few-shot LLM classifier.
> This is a property of a rule table written by hand. It is reported for
> completeness and is not the project's routing-accuracy result.

**78.3%** over 60 labelled queries (majority-class baseline 33.3%, lift +45.0%).

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| `simple_factual` | 0.625 | 1.000 | 0.769 | 20 |
| `multi_hop_reasoning` | 1.000 | 0.450 | 0.621 | 20 |
| `relationship_network` | 0.947 | 0.900 | 0.923 | 20 |

### Confusion matrix

| Actual ↓ / Predicted → | `simple_factual` | `multi_hop_reasoning` | `relationship_network` |
|---|---|---|---|
| `simple_factual` | 20 | 0 | 0 |
| `multi_hop_reasoning` | 10 | 9 | 1 |
| `relationship_network` | 2 | 0 | 18 |

### Accuracy by difficulty

| Difficulty | Accuracy | n |
|---|---|---|
| clear | 79.2% | 48 |
| moderate | 71.4% | 7 |
| ambiguous | 80.0% | 5 |

### Error direction

Leans cheap: 13 of 13 misroutes went to a cheaper path than needed, which returns thin answers to questions that deserved more.

Confidence separates signal from noise by +0.274 (mean 0.782 when correct vs 0.509 when wrong).

### Most confident mistakes

| Query | Expected | Predicted | Confidence |
|---|---|---|---|
| What is the total value of goods we import from China under chapter 61 | `multi_hop_reasoning` | `simple_factual` | 0.85 |
| What share of our HS 62 imports would be affected by a quota on Vietna | `multi_hop_reasoning` | `simple_factual` | 0.85 |
| What is the combined trade value across chapters 84 and 85 for 2023? | `multi_hop_reasoning` | `simple_factual` | 0.85 |
| How would a 10% duty increase on chapter 94 affect our landed costs? | `multi_hop_reasoning` | `simple_factual` | 0.85 |
| What is our exposure by value to partners outside the GCC? | `multi_hop_reasoning` | `relationship_network` | 0.85 |

## Cost and latency per path

Every one of 60 queries was forced down all three paths, so each row is a genuine counterfactual rather than a measurement of whichever path the router happened to choose.

| Path | Median | Mean | p95 | Cold start | Cost/query | Sources | Answer chars |
|---|---|---|---|---|---|---|---|
| `compute` | 0.0ms | 0.0ms | 0.0ms | 0ms | $0.00000 | 0.0 | 117 |
| `agentic` | 13.9ms | 15.5ms | 27.5ms | 2933ms | $0.00000 | 6.0 | 155 |
| `graph` | 63.4ms | 58.1ms | 108.6ms | 195ms | $0.00000 | 0.4 | 495 |
| `fast` | 450.6ms | 467.2ms | 618.8ms | 17355ms | $0.00000 | 5.0 | 1271 |

### What the numbers show

- `fast` is 45057× slower than `compute` at the median (450.6ms vs 0.0ms).
- `compute` shows 0.0ms, but this set is mostly not duty questions, so that figure is dominated by how fast it declines rather than how fast it computes. Its near-zero source count is the tell. A fair reading needs a duty-question subset; treat this row as a floor, not a comparison.
- The path named `fast` is NOT the fastest here — `compute` is. That is expected by design — the compute path makes no model call at all — but see the caveat above before reading it as a win.
- Cost is $0.00000 on every path because no LLM provider is configured — the classifier ran as a heuristic and no path invoked a model. **The cost comparison this project exists to make is not measured by this run.**
- Answers from `compute` average under 120 characters. That usually means a missing resource rather than a concise path — check the warning rate before reading it as brevity.
- `compute`, `graph` returns under one source per query on average, so its citation coverage is materially weaker than the other paths' regardless of answer length.

## Answer quality (RAGAS)

> Faithfulness and answer relevance require an LLM judge and are
> reported as `not_measured`. **That is not zero.** Context precision
> and recall are deterministic and are measured.

Relevance is scored on **13 of 20** fast-path queries. A ruling counts as relevant when **CBP** assigned it a code under the provision the question asks about — the labels are read off the source authority, not off what the retriever returned. 5 queries have no answering document in the corpus and are reported separately below; 2 admit no defensible single provision and are excluded rather than labelled generously.

| Path | Context precision | Context recall | Recall ceiling | Faithfulness | Answer relevance | n |
|---|---|---|---|---|---|---|
| `fast` | 0.538 | 0.212 | 0.461 | not_measured | not_measured | 13 |
| `graph` | 0.333 | 0.167 | 0.167 | not_measured | not_measured | 6 |

**Read recall against the ceiling, not against 1.0.** The path returns 5.0 sources against a mean of 19.4 relevant rulings, so the best recall attainable at this *k* is 0.461. Measured recall is 0.212 — that is the number to judge, and it reflects a deliberate choice to show five citations a user can actually read rather than to maximise a recall figure.

**`agentic`** — Not measured. The agentic path composes its own source descriptors (corridor and order keys) rather than citing documents with stable identifiers, so there is no relevance ground truth independent of the path itself. Reported as unmeasured rather than scored against its own output.

### Questions the corpus cannot answer

5 of the fast-path queries have no supporting document — two name rulings that were never ingested, three ask about provisions no ingested ruling classifies under. The correct behaviour is to say so.

**The fast path returned sources for 5 of 5.** It has no way to decline: retrieval always returns its top *k*, and *k* nearest neighbours exist in any non-empty index regardless of whether any of them bear on the question. This is the gap Phase J's abstention head is built to close, and these queries are its test set.

### The graph has no port layer

**10 of the 20 relationship queries name a port** — Jebel Ali, Singapore, Shanghai, Rotterdam, Nhava Sheva, Los Angeles, Busan, Tanger Med, Khalifa Port. The supply graph contains no port nodes at all: 38 countries keyed by ISO3, HTS codes, rulings, products and categories, and nothing else.

The cause is upstream. Port performance comes from the Container Port Performance Index, which the World Bank publishes as a report annex rather than through an API, so `marsa-ingest worldbank` cannot fetch it and the ports corpus was never written. Every port question therefore resolves against countries or falls through to nothing.

This is the single largest gap between what the graph path claims and what it can currently do, and no amount of retrieval tuning closes it — it needs the CPPI annex supplied as a local CSV via `marsa-ingest worldbank --cppi-csv`. These queries are excluded from the retrieval figures above rather than scored as failures, because what they measure is a missing corpus, not a bad traversal.

## Abstention (Phase J)

The question is not how often the system is right. It is how much accuracy a given willingness to decline buys, and whether it ever answers something it has no business answering.

Measured over 13 queries with a target provision and 5 the corpus cannot answer; 2 are excluded for having no defensible target. Correctness is judged against CBP's own code assignments, and on an unanswerable query the only correct behaviour is to decline.

| τ | Abstention rate | Accuracy on answered | Answered | Unsupported answered |
|---|---|---|---|---|
| 0.00 | 5.6% | 29.4% | 17 | 5/5 |
| 0.05 | 16.7% | 33.3% | 15 | 3/5 |
| 0.10 | 16.7% | 33.3% | 15 | 3/5 |
| 0.15 | 16.7% | 33.3% | 15 | 3/5 |
| 0.20 | 22.2% | 35.7% | 14 | 3/5 |
| 0.25 | 27.8% | 38.5% | 13 | 3/5 |
| 0.30 | 27.8% | 38.5% | 13 | 3/5 |
| 0.35 | 38.9% | 45.5% | 11 | 2/5 |
| 0.40 | 55.6% | 50.0% | 8 | 1/5 |
| 0.45 ← | 66.7% | 66.7% | 6 | 0/5 |
| 0.50 | 77.8% | 75.0% | 4 | 0/5 |
| 0.55 | 77.8% | 75.0% | 4 | 0/5 |
| 0.60 | 77.8% | 75.0% | 4 | 0/5 |
| 0.65 | 88.9% | 50.0% | 2 | 0/5 |
| 0.70 | 100.0% | 0.0% | 0 | 0/5 |
| 0.75 | 100.0% | 0.0% | 0 | 0/5 |
| 0.80 | 100.0% | 0.0% | 0 | 0/5 |
| 0.85 | 100.0% | 0.0% | 0 | 0/5 |
| 0.90 | 100.0% | 0.0% | 0 | 0/5 |
| 0.95 | 100.0% | 0.0% | 0 | 0/5 |
| 1.00 | 100.0% | 0.0% | 0 | 0/5 |

### Gate — no confident answer on a query the corpus cannot support

**PASS** at the shipped threshold τ=0.45.

At that point the system declines 66.7% of the set and is right 66.7% of the time on what it does answer (4 of 6).

Read the left end of the table as the system before Phase J: it answers nearly everything, including every query with no answering document, and accuracy on what it answers is correspondingly poor. The threshold buys accuracy by declining, and the table is what that costs.

Two honest caveats. The weights behind the confidence score are fixed round numbers, not fitted — fitting them on these same queries would report the fit rather than the behaviour. And thirteen scoreable queries is a small set: the shape of this curve is the finding, and no single cell in it should be quoted on its own.

## Honest limitations

- The complexity classifier is a single prompt (or, in this run, a rule table) rather than a trained model. Its accuracy is reported as measured.
- The CROSS corpus is a subset, not the full ~220,989 rulings.
- UN Comtrade's free preview tier is rate-limited and record-capped; the trade data is a sample, not a mirror.
- DataCo's `Late_delivery_risk` reflects one company's operations. The Phase D model demonstrates the technique, not a general prediction.
- The supply graph joins four corpora that share no keys. Every bridging assumption is marked `inferred` and listed on the `/data` page.
- The graph has no port layer. CPPI is a report annex rather than an API, so it was never ingested, and the relationship queries that name a port cannot be served at all. This is a missing corpus, not a weak traversal.
- Retrieval relevance is labelled in two steps: a hand judgement of which tariff provision each question asks about, then a mechanical lookup of which rulings CBP assigned to it. The first step is a reading of the question and is recorded with a rationale per query in `marsa/eval/relevance.py`; disagree with a line, not with the metric.
- Context recall is bounded by *k*. The ceiling is reported next to the measured value so the two are not confused.

---

**To produce a FINAL report:** configure an LLM provider (`GROQ_API_KEY` / `GEMINI_API_KEY`), run the Phase A ingestors against the live sources, rebuild the index and graph, then re-run `marsa-eval run`. No code changes are required.
