# Phase J — grounded classification with calibrated abstention

_Design, 2026-08-09._

## The problem this solves

Phase F measured that the router's confidence carries real signal: +0.274
separation between correct and incorrect routes. That measurement has so far
been a fact about a number in a report, not a behaviour a user can rely on.

The re-run on live corpora made the cost of that concrete. Five of the twenty
fast-path queries have no supporting document in the ingested CROSS subset —
two name rulings that were never ingested, three ask about provisions no
ingested ruling classifies under. **The fast path returned five sources for all
five.** It cannot do otherwise: retrieval returns its top *k*, and *k* nearest
neighbours exist in any non-empty index whether or not any of them bear on the
question.

For a compliance tool that is the worst available failure. CBP's reasonable-care
standard rewards documented uncertainty over undocumented confidence, and a
confident wrong classification is more damaging to an importer than no answer.

## Where the decision lives

ROADMAP §6 says "add a fourth class to the complexity classifier:
`insufficient_evidence`". Implemented literally, that puts the decision in the
router — before retrieval has run — and asks a few-shot classifier to infer
from the string `N302241` that no such ruling was ingested. It would be
measurable and it would be guessing.

**The abstention decision is made after retrieval, on the evidence that came
back.** The router keeps its three complexity classes. `insufficient_evidence`
becomes a fourth *outcome*, not a fourth route. You cannot know evidence is
insufficient before you look for it.

## Architecture

New module `backend/src/marsa/classify/`, following the shape of the existing
`duty/`, `screening/` and `regulatory/` phase modules.

### `evidence.py` — deterministic grounding signals

Four signals, computed from the retrieved rulings. No LLM, so this costs
nothing and cannot itself hallucinate.

| Signal | What it reads | Why it discriminates |
|---|---|---|
| provision agreement | concentration of CBP-assigned codes across top-*k* | five rulings pointing at one subheading is evidence; five pointing at five is not |
| arm agreement | overlap between the BM25 and dense result sets | the hybrid computes both and discards the disagreement; disagreement is the signal |
| lexical anchoring | query commodity terms against the CBP-authored subject | a ruling about power banks should mention power banks |
| code presence | whether any retrieved ruling carries a code the query names | direct, and only available when the query names one |

Arm agreement is the one worth dwelling on. Reciprocal rank fusion exists to
paper over the two arms disagreeing; keeping the residual tells us when both
arms independently found the same thing, which is a different and stronger
claim than either arm ranking it first.

**Amended during implementation — two changes the measurement forced.**

First, arm agreement was structurally dead on arrival: the reranker overwrote
`ScoredChunk.retriever`, so by the time the signal read it, every hit claimed a
single arm and the term contributed a constant zero. Fixed by carrying the arm
provenance in a field of its own that reranking preserves.

Second, a **fifth signal** was added, and it is a veto rather than a weight.
The first measured curve failed its own gate: "What does HQ H289765 say about
essential character?" scored 0.688 and answered confidently, because the five
rulings it retrieved all sat under one subheading and all discussed essential
character at length. Every weighted signal read as strong evidence and every
one of them was evidence about the wrong documents. When a query names a ruling
that retrieval did not return, confidence is zero — no quantity of evidence
about other documents is evidence about that one. Weighting it would have let
agreement and anchoring outvote it, which is exactly what went wrong.

### `abstain.py` — the head

Combines the signals into a confidence in `[0, 1]` and compares it against a
threshold τ. Below τ the outcome is `insufficient_evidence`, carrying the text
*"I cannot support a classification from the rulings I have"* **and the closest
rulings anyway**. A refusal that returns nothing is less useful than one that
returns the near misses and lets the user judge.

### `citations.py` — refusal by construction

A suggestion carries a `Citation` — ruling number, the HTS code CBP assigned to
that ruling, and its URL — or it does not exist. Uncited output is not rejected
by a policy check somewhere downstream that a future caller might bypass; it is
unrepresentable. This is the axis the roadmap picks to compete on, so it should
be a property of the type rather than a rule in a handler.

### `curve.py` — the acceptance measurement

Sweeps τ and emits, at each point, the abstention rate and the accuracy on the
queries still answered. Ground truth is the `marsa.eval.relevance` labels: a
suggestion is correct iff its cited ruling's CBP-assigned code falls under the
query's target provision, and for the five unanswerable queries the correct
behaviour is abstention.

The result is the shape of the trade-off. It is reported as a curve, not
collapsed to a single number, because on thirteen scoreable queries a single
number has a confidence interval wider than most differences worth detecting.

## Data flow

```
query
  └─> router (3 complexity classes, unchanged)
        └─> fast path retrieval ──> rulings + per-arm hit sets
              └─> evidence.assess(query, rulings, arms) ──> signals
                    └─> abstain.decide(signals, tau)
                          ├─ confident  ──> Suggestion(Citation, ...)
                          └─ below tau  ──> InsufficientEvidence(closest rulings)
```

## The gate

One hard gate joins `evaluate_gate`, binary and non-negotiable in the style the
repo already uses:

> **Zero confident answers on queries the corpus cannot support.**

At the time of writing the system fails it 5 of 5. That is the honest starting
point and the reason the gate exists.

## Frontend

The refusal surfaces in the Next.js app: a `/classify` view that renders either
a cited suggestion or the refusal with its near-miss rulings. A refusal the user
never sees is not a product behaviour, and the existing fixture-mode banner
already establishes the pattern for telling the user on screen when the system
is operating in a degraded mode.

## Testing

- unit coverage per evidence signal, including the degenerate cases (empty
  retrieval, single result, all-identical codes)
- a calibration test: confidence separates correct from incorrect suggestions
- a gate test: the five unanswerable queries force abstention at the shipped τ
- a monotonicity test: raising τ never lowers accuracy on answered queries
- schema tests for the API response shape, matching the existing `api/schemas`
  discipline

## Out of scope

Phase K's scenario graph. Extending abstention to the duty engine — the roadmap
lists calibrated abstention as a cross-cutting capability across the classifier
*and* the duty engine, but the duty stack has its own arithmetic-provenance
model and folding both into one change would obscure which one the curve
describes.
