# RESULTS

_Generated 2026-08-07 16:26 UTC · status **PROVISIONAL**_

> ## ⚠ PROVISIONAL — these numbers do not test the thesis
>
> This run exercised the full pipeline end to end, which is worth showing.
> It does **not** answer the question this project exists to ask, for the
> reasons listed immediately below. Every figure here is annotated with what
> it actually describes.


## Why this run cannot settle the question

1. **The classifier is not the LLM.** Routing accuracy below measures a deterministic rule table, not the few-shot LLM call the spec specifies. The two are not comparable and this figure must not be reported as the classifier's accuracy. Set `GROQ_API_KEY` or `GEMINI_API_KEY` and re-run.
2. **The corpora are synthetic.** Retrieval and answer-quality figures describe the fixture generator, not CBP CROSS, UN Comtrade, DataCo or World Bank data. Run the Phase A ingestors where outbound access is permitted and re-run.
3. **No LLM judge is available.** RAGAS faithfulness and answer relevance are reported as `not_measured` rather than zero. Context precision and recall are deterministic and are measured.

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

> Latency is **real** — the paths genuinely do this much work. It is
> measured over synthetic corpora at fixture scale, so it will not
> transfer to a full-size corpus, and cost is $0 because no model ran.

Every one of 60 queries was forced down all three paths, so each row is a genuine counterfactual rather than a measurement of whichever path the router happened to choose.

| Path | Median | Mean | p95 | Cold start | Cost/query | Sources | Answer chars |
|---|---|---|---|---|---|---|---|
| `agentic` | 0.8ms | 0.9ms | 1.8ms | 154ms | $0.00000 | 6.0 | 178 |
| `fast` | 6.0ms | 6.2ms | 8.4ms | 277ms | $0.00000 | 5.0 | 213 |
| `graph` | 477.3ms | 398.0ms | 906.8ms | 853ms | $0.00000 | 0.5 | 701 |

### What the numbers show

- `graph` is 575× slower than `agentic` at the median (477.3ms vs 0.8ms).
- The path named `fast` is NOT the fastest here — `agentic` is. That ordering is an artefact, not a finding: with no LLM configured the agentic path never makes a model call, so it degrades to in-memory list filtering and does far less work than its design intends. `fast` meanwhile pays a real pgvector round trip. Configure a provider and this ordering should invert.
- Cost is $0.00000 on every path because no LLM provider is configured — the classifier ran as a heuristic and no path invoked a model. **The cost comparison this project exists to make is not measured by this run.**
- `graph` returns under one source per query on average, so its citation coverage is materially weaker than the other paths' regardless of answer length.

## Answer quality (RAGAS)

> Faithfulness and answer relevance require an LLM judge and are
> reported as `not_measured`. **That is not zero.** Context precision
> and recall are deterministic and are measured.

| Path | Context precision | Context recall | Faithfulness | Answer relevance |
|---|---|---|---|---|
| `fast` | 0.200 | 1.000 | not_measured | not_measured |
| `agentic` | 0.200 | 1.000 | not_measured | not_measured |
| `graph` | 0.650 | 0.650 | not_measured | not_measured |

## Honest limitations

- The complexity classifier is a single prompt (or, in this run, a rule table) rather than a trained model. Its accuracy is reported as measured.
- The CROSS corpus is a subset, not the full ~220,989 rulings.
- UN Comtrade's free preview tier is rate-limited and record-capped; the trade data is a sample, not a mirror.
- DataCo's `Late_delivery_risk` reflects one company's operations. The Phase D model demonstrates the technique, not a general prediction.
- The supply graph joins four corpora that share no keys. Every bridging assumption is marked `inferred` and listed on the `/data` page.

---

**To produce a FINAL report:** configure an LLM provider (`GROQ_API_KEY` / `GEMINI_API_KEY`), run the Phase A ingestors against the live sources, rebuild the index and graph, then re-run `marsa-eval run`. No code changes are required.
