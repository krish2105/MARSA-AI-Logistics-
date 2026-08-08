# MARSA AI — Phase G–K Plan

_Written August 2026. Positioning grounded in the regulatory and competitive
landscape as it stands this month; every claim that drove a decision is cited
at the bottom._

**Mandate agreed:** academic core, product shell. The measured-routing thesis
stays the intellectual centre; a real product wraps it. No ICP committed yet —
this is a demo that must survive contact with a real one later. **Free tiers
only, solo.**

---

## 1. Four findings that change the plan

### 1.1 HS classification is already commoditised — do not lead with it

Zonos Classify advertises 50,000 products/hour. Thomson Reuters ONESOURCE
targets 95% accuracy. Trade Insight AI claims 70% of world imports today and
100% by mid-2026. Gaia Dynamics and Sphere are both shipping.

Entering as "we classify products" means fighting funded incumbents on their
own ground with no proprietary data and no distribution. **The differentiation
is not in the classification.** It is in what nobody is doing well: telling you
what the classification *costs* under four overlapping tariff regimes, and
proving the answer was not invented.

### 1.2 Duty stacking is the acute, unsolved 2026 pain

Section 232 sits at 25–50% and was modified again in June 2026. It is
**additive** with Section 301 and with IEEPA reciprocal tariffs. On top of
that: a 15% total-duty cap for the EU, UK, Japan, Korea, Switzerland, Taiwan
and others; USMCA rules that apply duty only to non-US content with a 15%
floor; and the full 50% for Brazil, India, Vietnam, Korea and Turkey.

That is a **layered, country-conditional, date-versioned computation**. It is
exactly the kind of thing an LLM should never be asked to do arithmetic on, and
exactly the kind of thing a well-built deterministic engine answers perfectly.

### 1.3 The regulatory tailwind is enormous and immediate

- **CBAM's definitive period began 1 January 2026.** Certificates must now be
  bought and surrendered; the penalty is €100 per tonne of uncovered CO₂e. And
  CBAM codes are now mandatory in EU import declarations — get it wrong and
  **the declaration is rejected outright**.
- **The UFLPA Entity List reached 187 entities on 3 August 2026**, after DHS
  added 43 Chinese companies. Section 301 forced-labour tariffs hit 60
  economies from 24 July 2026. A detention costs an importer real money, and
  the burden of proof is "clear and convincing evidence" within 30 days.
- The EU Forced Labour Regulation's risk database was due June 2026, with full
  enforcement December 2027.

Adoption is following: **40% of organisations now use generative AI for trade
compliance, up from 22% a year ago.**

### 1.4 MARSA's audit surface is already a regulatory asset

**EU AI Act Article 50 transparency obligations took effect 2 August 2026 and
are being enforced now.** High-risk Annex III obligations were deferred to
December 2027 by the Digital Omnibus — so there is a window, but transparency
is live.

The Route Badge, the audit record, the provenance page and RESULTS.md's
publication gate were built as engineering honesty. They are also, as of this
month, the beginnings of an AI Act transparency file. **Nobody in this
competitive set is marketing that.** It should become an explicit product
surface, not an implementation detail.

---

## 2. The architectural insight: all four wedges are one primitive

You ranked four capabilities. Built separately they are four codebases. Built
correctly they are **four applications over one missing primitive.**

Every one of them reduces to the same sentence:

> *Apply a versioned legal instrument to a shipment, and cite it.*

| Wedge | Instrument | Effect |
|---|---|---|
| Forced-labour screening | UFLPA Entity List entry | Detention risk |
| CBAM screening | CBAM Annex I goods scope | Certificate liability |
| Duty stacking | 232 / 301 / IEEPA proclamation | Rate, additive |
| Classification | CBP CROSS binding ruling | HTS code |
| Scenario graph | Any of the above, counterfactually | Exposure |

So Phase G builds the primitive, and G–K stop being a wishlist and become a
dependency chain.

---

## 3. Phase G — the Regulatory Knowledge Layer

**The one thing to build next.** Everything else is an application on top.

### The model

```python
class Instrument:
    id: str                    # "USTR-232-2026-06-01"
    issuer: Issuer             # USTR | CBP | DHS | EU_COMMISSION
    kind: InstrumentKind       # TARIFF | ENTITY_LISTING | GOODS_SCOPE | RULING
    scope: Scope               # HTS prefixes, origin countries, entity names
    effect: Effect             # rate, additive?, cap, exemption
    effective_from: date
    effective_to: date | None  # None = still in force
    supersedes: list[str]      # the June 2026 proclamation supersedes earlier 232
    source_url: str
    retrieved_at: datetime
    text_hash: str             # detects silent upstream edits
```

### Why this is the whole game: point-in-time correctness

A tariff engine that answers with a **superseded rate** is worse than no
engine. It is not a wrong answer, it is a liability — someone files an entry on
it. Section 232 changed in June 2026 and is scheduled to run to December 2027;
anything that retrieved the old rate and did not know it had been replaced will
confidently produce a number that was true in May.

So the core query is not "what is the rate for HTS 7326.90" but:

```python
applicable(hts="7326.90.86", origin="VN", on=date(2026, 3, 15))
```

…returning the instrument set **in force on that date**, with the supersession
chain visible.

### Why this is also the research contribution

The Phase F thesis is *"not all questions deserve the same computation."* Phase
G extends it to a second, sharper claim:

> **"and no answer should cite an instrument that was not in force."**

Temporal grounding in regulatory RAG is a genuinely under-served problem, it is
measurable, and it produces a failure mode (confident citation of a superseded
rule) that is easy to demonstrate and embarrassing to ignore. That is a
dissertation chapter, not a feature.

### Deliverables

- `regulatory/schema.py` — the model above
- `regulatory/store.py` — point-in-time index (Postgres, `daterange` + GiST)
- `regulatory/supersede.py` — supersession graph, contradiction detection
- `regulatory/ingest/` — one ingestor per source, all public:
  - Federal Register API (232/301/IEEPA proclamations)
  - DHS UFLPA Entity List
  - EU CBAM Annex I goods scope
  - USITC HTS (MFN base rates)
- `marsa-reg` CLI: `ingest`, `asof`, `diff`, `staleness`
- **Staleness banner**: every answer states how old its instrument set is. An
  engine that cannot prove freshness must say so, exactly as RESULTS.md
  refuses to publish.

**Acceptance:** given an HTS code, origin and date, return the instrument set
in force on that date with supersession chain and source URLs, and refuse
rather than guess where coverage is absent.

---

## 4. Phase H — the Duty Stack Engine, and a fourth route

> **Shipped.** G3 passes (exact arithmetic, enforced in CI); G4 holds by
> construction — a deterministic pre-filter routes duty questions before the
> classifier is consulted, and the classifier's answer is recorded alongside so
> the override can be scored rather than hidden.

### The engine

Deterministic. No LLM in the arithmetic path, ever.

```
landed_duty(hts, origin, value, date) =
    MFN base
  + Section 301  (if origin=CN and HTS in list)
  + Section 232  (if HTS in covered/derivative list)
  + IEEPA reciprocal
  → apply country cap (15% for EU/UK/JP/KR/CH/TW…)
  → apply USMCA non-US-content rule (floor 15%)
  + CBAM certificate liability (EU imports, Annex I goods)
```

Every line item cites its instrument. The output is a **table an importer can
hand to a broker**, not a paragraph.

### The fourth route — and why it strengthens the thesis

This is the part I would put in the dissertation.

Today the router has three paths: fast, agentic, graph. Phase H adds a fourth:
**`compute`** — a deterministic tool call with *zero* LLM tokens in the answer
path. The LLM's only job is to explain the table afterwards, optionally.

That is a strong result for the core thesis, because the new path is
**simultaneously the cheapest and the most correct**. The existing Phase F
finding is that the classifier under-routes to cheaper paths and loses quality.
A compute path inverts that relationship for one query class: cheaper *and*
better. The thesis stops being "spend less where you can afford to" and becomes
"**spend nothing where arithmetic is the right tool**" — a much more
interesting claim, and one your own benchmark harness can already measure.

The new failure mode to measure honestly: the classifier routing a duty
question to `agentic`, which will hallucinate plausible arithmetic. That is the
single most dangerous misroute in the system and deserves its own section in
RESULTS.md.

**Acceptance:** a hand-checked set of ~30 duty calculations with known correct
answers, including at least one of each: capped country, USMCA split, stacked
232+301, CBAM-liable good. 100% exact match required — this is arithmetic, not
retrieval, and 95% is a failing grade.

---

## 5. Phase I — Screening (your top-ranked wedge)

> **Shipped.** G5 passes: 4/4 recall on supplier-name variants with the hard
> negatives rejected, enforced in CI. Output is three-valued and none of the
> three is a clearance.

Given a supplier list, screen against the UFLPA Entity List and CBAM goods
scope, with provenance on every hit.

The hard part is **not** the lookup. It is entity resolution: "Hefei Bitland
Information Technology Co., Ltd." vs "Bitland (Hefei) Information Tech" vs a
Chinese-character name. Phase C's `resolve.py` already does fuzzy resolution
against the supply graph — this extends it rather than starting over.

Non-negotiable design rules:

- **Never auto-clear.** A screening tool that says "no match" with false
  confidence is how a container gets detained. Output is three-valued: `HIT` /
  `POSSIBLE` / `NO EVIDENCE FOUND` — never "clear".
- Every `POSSIBLE` shows the match score, the matched string, and the list
  version it was checked against.
- The Entity List moves (43 additions in one day this August). Show the list
  version and its age on every result.

**Acceptance:** precision/recall on a hand-labelled set of supplier-name
variants, including deliberately hard negatives — near-miss names that must
*not* match.

---

## 6. Phase J — Classification, grounded and abstaining

Commoditised as a category, so compete only on the axis incumbents are weak on:
**every suggestion cites the binding ruling that supports it**, and the system
declines when the rulings do not support one.

Add a fourth class to the complexity classifier: `insufficient_evidence`.

This matters more than it sounds. Phase F already measured that confidence
carries real signal (+0.274 separation). An abstention head turns that measured
signal into a product behaviour: below threshold, the system says *"I cannot
support a classification from the rulings I have"* and shows the closest
rulings anyway. For a compliance tool, a calibrated refusal is worth more than
a confident guess — and CBP's "reasonable care" standard rewards documented
uncertainty over undocumented confidence.

**Acceptance:** measured abstention curve — accuracy on answered queries vs
abstention rate. The useful result is the shape of that trade-off, not a single
number.

---

## 7. Phase K — Scenario graph

"If Vietnam gets a 20% tariff, what is my exposure?"

This is where Phases C, D, G and H compose, and it is the demo that no
competitor can show. It is also the hardest to evaluate objectively — there is
no ground truth for a counterfactual — so it ships **last** and is presented as
decision support with the inference chain fully visible, never as prediction.

---

## 8. Cross-cutting AI capabilities

Not phases; they cut across G–K.

| Capability | What it does | Why it earns its place |
|---|---|---|
| **Citation verification** | Post-hoc deterministic check that every cited instrument actually contains the claim | Cheap, no LLM, catches the highest-severity failure class |
| **Contradiction detection** | Two instruments imply different rates for the same entry → flag both, pick neither | Regulations genuinely conflict; silently choosing one is the worst option |
| **Temporal drift monitor** | Re-fetch instruments, diff by `text_hash`, alert on silent upstream edits | Sources edit in place without notice |
| **Calibrated abstention** | A refusal head across classifier and duty engine | Turns Phase F's measured confidence into product safety |
| **Quota-aware degradation** | On free-tier exhaustion, degrade to deterministic paths and *say so on screen* | The existing fixture-mode banner pattern, extended |
| **AI Act transparency file** | Auto-generated: model, provenance, limitations, human-oversight notes | Art. 50 is live now; you are 80% there already |

---

## 9. Free-tier architecture

| Layer | Choice | Free-tier reality |
|---|---|---|
| LLM | Gemini | Only reachable provider from the build sandbox. Rate-limited — batch and cache |
| Vector | Neon pgvector | 0.5 GB is ample at this corpus size |
| Gateway | Render free | Spins down after 15 min; 30–60s cold start |
| Frontend | Vercel | Fine |
| Images | GHCR | Fine |
| Data | All public | Federal Register, DHS, EU Commission, USITC, CBP CROSS |

**The honest consequence:** the deterministic engine does the work, and the LLM
is a thin explanation layer. On free tiers that is a constraint. Architecturally
it is also just *correct* — which is a happy accident worth stating plainly
rather than dressing up.

---

## 10. Cost model

**Measured, not estimated.** Token counts come from the running system: the
few-shot classifier prompt is 318 tokens; CROSS ruling bodies average 177
tokens, so a top-5 whole-parent context is 885. Prices are Gemini 2.0 Flash
($0.10/M in, $0.40/M out) as already configured in `router/llm.py`.

### Per query

| Path | Input tok | Output tok | LLM calls | $/query | $/1k queries |
|---|---:|---:|---:|---:|---:|
| `fast` | 1,203 | 150 | 2 | $0.000180 | $0.18 |
| `agentic` | 2,973 | 400 | 4 | $0.000457 | $0.46 |
| `graph` | 1,218 | 250 | 2 | $0.000222 | $0.22 |
| **`compute`** (Phase H) | **318** | **0** | **1** | **$0.000032** | **$0.03** |

Note the `calls` column. **Every user query costs at least two LLM calls** —
one to classify, one or more to answer. The agentic path's plan → retrieve →
critic loop makes four. That column, not the dollar column, turns out to be the
one that matters.

### The finding that reframes the plan: rate limits bind long before cost does

At paid rates this system is almost free. Blended across the current Phase F
routing mix:

| Volume | Today | With the compute route | Saved |
|---|---:|---:|---:|
| 1,000 q/mo | $0.24 | $0.18 | 26% |
| 10,000 q/mo | $2.38 | $1.76 | 26% |
| 100,000 q/mo | $23.77 | $17.60 | 26% |

**$24/month at 100,000 queries.** Cost is not a constraint at any volume this
project will plausibly reach. Optimising it in dollar terms is theatre.

The binding constraint is the free tier's **request** ceiling, and because each
query costs 2–4 calls, it bites four times harder than it looks:

| Path | Gemini 2.5 Flash (250 req/day) | Flash-Lite (1,000 req/day) |
|---|---:|---:|
| `fast` | 125 user queries/day | 500/day |
| `agentic` | **62 user queries/day** | 250/day |
| `graph` | 125 user queries/day | 500/day |
| **`compute`** | **250 user queries/day** | **1,000/day** |

So the honest headline is: **on the free tier this demo supports roughly 60–125
real queries a day**, and a single enthusiastic reviewer clicking through the
console can exhaust a day's quota. That is a product fact, not a footnote, and
it is why quota-aware degradation (§8) is a shipping requirement rather than a
nicety.

### What this does to the case for Phase H

The argument for the compute route was correctness. The numbers make it
stronger on two further axes:

- **5.6× cheaper** than the fast path, 14× cheaper than agentic.
- **It doubles-to-quadruples free-tier throughput** — 250 queries/day versus 62
  on agentic — because it removes the answer-path LLM call entirely rather than
  merely shrinking it.

That last point is the one to lead with. Under a request-capped free tier,
*removing a call* is worth vastly more than *shortening a prompt*, and the
compute route is the only change on the roadmap that removes one.

### Corroboration and caveats

The 26% blended saving is worth flagging: independent work on cost-aware RAG
routing reports a 26% reduction in billed token cost from routing versus
always-heavy retrieval. Arriving at the same figure from measured token counts
on a different corpus is a useful sanity check on the method.

Three caveats, stated because the numbers are cheap to misread:

1. **Fixture scale.** CROSS bodies here average 708 characters. Real CBP
   rulings routinely run several thousand. Expect input tokens — and therefore
   cost — to rise several-fold on the real corpus. The *ratios* between paths
   should hold; the absolute figures will not.
2. **Output tokens are the degraded ones.** With no LLM configured, answers are
   template-generated. A real generator writes longer, so output cost is
   understated.
3. **Nothing here is measured against a live provider.** These are computed
   from measured token counts and published prices, not from a bill. The first
   real API key turns this table from a model into a measurement.

### Non-LLM infrastructure

| Service | Free allowance | Headroom at this scale |
|---|---|---|
| Neon | 100 CU-hours/mo, 0.5 GB, 5 GB transfer | Ample — corpus is ~6 MB |
| Render | 750 instance-hours/mo, 512 MB, 0.1 CPU | One always-on service fits; spins down after 15 min idle, ~1 min cold start |
| Vercel | Hobby | Ample |
| GHCR | Public/private packages | Ample |

Render's free Postgres expires after 30 days and has no pgvector, which is why
the database is Neon. Render's 512 MB is the tighter constraint: the boot-time
index build must stay inside it as the corpus grows.

---

## 11. Sequencing, and what I would cut

You ranked all four wedges. As CTO I have to say plainly: **all four at
publishable depth is not a solo free-tier project in any short timeframe.**
Attempting it produces four shallow features and a weak thesis.

**Recommended MVP: G + H + I.** That is a coherent product — *"what will this
shipment cost me, and will it be detained?"* — grounded, cited, date-correct,
and it exercises every existing phase.

| Phase | Ship | Effort | Cut if time is short |
|---|---|---|---|
| **G** Regulatory Knowledge Layer | Must | Large | Never — everything depends on it |
| **H** Duty Stack + compute route | Must | Medium | Never — this is the thesis extension |
| **I** Screening | Must | Medium | Reduce to UFLPA only, drop CBAM scope |
| **J** Classification + abstention | Should | Small | Ship abstention only, skip full coverage |
| **K** Scenario graph | Stretch | Medium | Cut entirely; Phase C already demos the idea |

If a deadline forces one thing: **G + H.** A date-correct duty engine with
cited instruments and a fourth compute route is a complete, defensible,
demonstrable contribution on its own.

---

## 12. Decision gates

A plan with no kill criteria is a wish. Each gate has a measurement, a
threshold, and a pre-agreed action if it fails — decided now, while nothing is
sunk, rather than in month three while defending prior effort.

No calendar dates: none has been set. Gates are ordered by dependency, and each
is evaluated when its phase completes.

| # | Gate | Measured by | Pass | If it fails |
|---|---|---|---|---|
| **G1** | The RKL premise holds | `marsa-reg probe` — mean required-field coverage across the four sources | ≥ 80% coverage | < 60%: the "one primitive" thesis is wrong. Stop. Fall back to a single-regime tool (232 only) and re-plan. |
| **G2** | Instruments are genuinely versioned | Fraction of ingested instruments carrying a usable `effective_from` | ≥ 90% | < 70%: point-in-time queries are not supportable. Drop the temporal claim from the thesis rather than fake it. |
| **G3** | Duty arithmetic is exact | 30 hand-checked calculations incl. capped country, USMCA split, stacked 232+301, CBAM-liable | **100%** | Anything below 100% blocks shipping Phase H. Arithmetic has no partial credit. |
| **G4** | The router sends duty questions to `compute` | Routing accuracy on a duty-question slice of the labelled set | ≥ 95% to `compute` | < 95%: hard-route on a deterministic pre-filter *before* the classifier. A duty question reaching `agentic` produces hallucinated arithmetic — the highest-severity failure in the system. |
| **G5** | Screening does not false-negative | Recall on hand-labelled supplier-name variants | ≥ 95% recall | < 95%: ship as `POSSIBLE`/`NO EVIDENCE` only, remove any single-result view that could read as a clearance. |
| **G6** | RESULTS.md can leave PROVISIONAL | Gate blockers remaining after Phase G ingestion + an API key | ≤ 1 blocker | Still 3: the academic contribution is at risk. Escalate — either secure real corpus access or re-scope the thesis around Phase G's own real data, which is reachable. |
| **G7** | Free tier survives demand | Sustained daily query volume vs the 62–250/day ceiling in §10 | Under ceiling | Exceeded: decide explicitly — pay for Tier 1, or cap and queue with an honest on-screen message. Do not let it fail silently. |
| **G8** | Scope stays solo-sized | Phases complete vs the §11 cut list | G+H+I done before J starts | Behind: execute the cut list as written. It exists to be used, not admired. |

### G1 status: NOT_EVALUATED

`marsa-reg probe` exists and runs. From this environment it reports
`NOT_EVALUATED` with 0/4 sources reachable — **not a pass and not a failure.**
The gate deliberately refuses to return a verdict it has no evidence for, on
the same principle as RESULTS.md's publication gate.

Run it anywhere with normal outbound access:

```bash
marsa-reg probe          # exits 0 only on PASS
marsa-reg probe --json   # machine-readable
```

Until it returns PASS or MARGINAL, **Phase G is not started.** Building it
first would make the gate ceremonial, which is the failure mode gates exist to
prevent.

### The three that actually matter

**G3 and G4 are shipping blockers**, not health checks. A duty engine that is
99% right is a liability generator; a classifier that routes duty questions to
an LLM will invent numbers that look correct. Both fail closed.

**G1 is the one to run early and cheaply.** It is testable in a day against
public sources, before any real investment in Phase G. If the instrument data
is not there in usable form, everything downstream collapses — and that is much
better discovered in week one than month two.

### Pre-agreed reversals

Two things I will do without asking if the gates say so:

- If **G3** fails, Phase H does not ship, regardless of how complete it looks.
- If **G1** fails, I stop and bring you a re-plan rather than building G anyway.

---

## 13. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| **Stale instrument cited as current** | Critical | Point-in-time queries, staleness banner, `text_hash` drift monitor |
| **Perceived as customs advice** | High | Decision-support framing throughout, explicit "verify with a licensed broker", no filing integration |
| LLM does arithmetic | High | Deterministic compute route; the LLM never sees the numbers |
| Screening false-negative | High | Three-valued output; never "clear" |
| Free-tier quota exhaustion mid-demo | Medium | Deterministic fallbacks, visible degradation banner |
| Scope creep across four wedges | Medium | The cut list above, agreed in advance |
| Synthetic corpora still blocking RESULTS.md | Medium | Phase G's sources are public and unauthenticated — real data **anywhere with normal egress**, but see the correction below |

### Correction — an earlier claim in this document was wrong

The first version of this roadmap said Phase G's sources were "all public and
reachable" and that the blockage keeping RESULTS.md at PROVISIONAL "does not
apply to any of them." **That was asserted without being tested, and testing it
proved it false.**

`marsa-reg probe` was then written and run. All four sources return **403 from
the egress proxy** — via `curl` and via every other route available here:

```
Federal Register       UNREACHABLE   403 Forbidden
USITC HTS              UNREACHABLE   403 Forbidden
DHS UFLPA Entity List  UNREACHABLE   403 Forbidden
EU CBAM Annex I        UNREACHABLE   403 Forbidden

G1: NOT_EVALUATED — 0/4 sources reachable
```

What is still true: the sources are public, unauthenticated, and free. What is
not true is that this build environment can reach them. Phase G's data is real
**anywhere with normal outbound access** — and nowhere in this sandbox, exactly
like Phases A–F before it.

The practical consequence is that Phase G does not, on its own, get RESULTS.md
out of PROVISIONAL from here. It gets there from a laptop. That is a smaller
claim than the one originally made, and it is the accurate one.

---

## 14. What "ready to deploy" means here

Already true: CI green, both images built and the backend image proven to boot,
serve, answer a real query, and refuse to start on a silent pgvector fallback.

Still required for a genuine MVP:

1. Gateway deployed to Render + Neon (needs your accounts)
2. `NEXT_PUBLIC_API_BASE_URL` set so the frontend image stops failing by design
3. A Gemini key — converts RESULTS.md from PROVISIONAL to a real measurement on
   two of its three blockers
4. Phase G ingestors run against live sources — fixes the third
5. Rate limiting and abuse controls before any public URL is shared

Items 3 and 4 together are what turn this from "the pipeline runs" into "here
is what the router actually does."

---

## Sources

- [Top Customs Compliance Software 2026 — Sphere](https://www.getsphere.com/blog/customs-compliance-software)
- [Zonos Classify](https://zonos.com/classify)
- [Thomson Reuters — AI HS code classification](https://tax.thomsonreuters.com/blog/transform-your-trade-compliance-workflow-how-ai-eliminates-the-classification-guesswork/)
- [Gaia Dynamics](https://www.gaiadynamics.ai/)
- [Trade Insight AI](https://www.tradeinsightai.com/)
- [CBAM definitive regime 2026 — DEHSt](https://www.dehst.de/EN/Topics/CBAM/CBAM-definitive-regime-2026/cbam-definitive-regime-2026_node.html)
- [EU CBAM penalties 2026–2027](https://www.cbamjournal.com/post/eu-cbam-penalties-2026-2027)
- [Start of the CBAM definitive period — European Commission](https://trade.ec.europa.eu/access-to-markets/en/news/start-definitive-period-cbam-eu)
- [DHS expands UFLPA Entity List — Covington](https://www.cov.com/en/news-and-insights/insights/2026/08/dhs-expands-uflpa-entity-list-amid-intensifying-enforcement-landscape)
- [UFLPA enforcement & compliance — Kharon](https://www.kharon.com/resources/use-cases/forced-labor/uflpa-enforcement-and-compliance-strategy)
- [EUFLR 2026 preparation — Worldfavor](https://blog.worldfavor.com/the-eu-forced-labor-regulation-euflr-in-2026-how-to-prepare)
- [Section 232 updates June 2026 — C.H. Robinson](https://www.chrobinson.com/en-us/resources/insights-and-advisories/client-advisories/2026q2/06-02-2026-client-advisory-updates-to-section-232-tariffs-on-steel-aluminum-copper/)
- [US Tariffs 2026: Section 301, IEEPA, 232 — Suaid Global](https://suaidglobal.com/insights/us-tariffs-2026-guide/)
- [EU AI Act Omnibus — postponed high-risk deadlines — Gibson Dunn](https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/)
- [EU AI Act high-risk obligations — A&O Shearman](https://www.aoshearman.com/en/insights/ao-shearman-on-tech/zooming-in-on-ai-10-eu-ai-act-what-are-the-obligations-for-high-risk-ai-systems)
- [Cost-aware query routing in RAG](https://arxiv.org/html/2606.02581v1)
- [Lightweight query routing for adaptive RAG — RAGRouter-Bench](https://arxiv.org/pdf/2604.03455)
- [Agent-orchestrated adaptive RAG](https://arxiv.org/pdf/2606.05658)
