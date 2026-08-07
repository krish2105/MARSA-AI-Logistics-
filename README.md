<div align="center">

# MARSA AI (مرسى)

**Adaptive-RAG Trade & Logistics Copilot**

*Not all questions deserve the same amount of computation.*

</div>

---

## What this is

Marsa AI is a trade-and-logistics intelligence copilot built around a single
engineering thesis: a simple lookup, a multi-hop reasoning question, and a
network question are three genuinely different retrieval problems, and routing
all three through one pipeline is either wasteful or wrong.

Marsa AI classifies each incoming query and routes it to the cheapest path that
can actually answer it correctly — then **measures and reports** the
accuracy/cost/latency consequences of that decision rather than assuming more
complexity is better.

| Path | Query type | Strategy | Corpus |
|---|---|---|---|
| **Fast** | `simple_factual` | Hybrid RAG — dense (MiniLM) + BM25 → cross-encoder rerank | CBP CROSS rulings |
| **Agentic** | `multi_hop_reasoning` | LangGraph loop — plan → retrieve → critic → retry (max 2×) | UN Comtrade + DataCo |
| **Graph** | `relationship_network` | GraphRAG — entity resolution → k-hop traversal → synthesis | NetworkX graph + World Bank LPI 2.0 |

The **Route Badge** in the UI is the governance surface: it shows which path
was chosen, why, with what confidence, and at what cost and latency. That
decision is auditable in the interface, not buried in a log.

---

## Build status

| Phase | Scope | Status |
|---|---|---|
| **1** | Design system, app shell, theme system, Route Badge (mock stream) | ✅ **Shipped** |
| **A** | Data ingestion — CROSS, Comtrade, DataCo, World Bank LPI | ✅ **Shipped** (see caveat) |
| **B** | Fast-path index — chunking, pgvector, BM25, reranker | ✅ **Shipped** (see caveat) |
| **C** | Graph construction — NetworkX supplier/port/country graph | ✅ **Shipped** |
| D | ML layer — late-delivery risk, port-congestion tiering | ⬜ Planned |
| E | LangGraph router + FastAPI gateway + SSE streaming | ⬜ Planned |
| F | Evaluation harness → `RESULTS.md` | ⬜ Planned |

**No benchmark numbers are published yet** — the dashboard's results card is
deliberately empty rather than filled with invented figures, because the whole
thesis depends on those numbers being measured.

### Phase A caveat, stated plainly

The four ingestors are written, tested and runnable, but **they have not been
run against the live sources**, because outbound access to `rulings.cbp.gov`,
`comtradeapi.un.org`, `api.worldbank.org` and `kaggle.com` is blocked by
network policy in the build environment. Seeded synthetic fixtures stand in so
Phases B–F are not blocked.

Every synthetic record is marked `origin=SYNTHETIC` in its provenance, in the
manifest, in `marsa-ingest status`, and in the `/data` page of the UI. Run the
ingestors anywhere with normal egress and the fixtures are replaced by real
data with no code change.

The Comtrade client's contract *was* verified — read out of the official
`comtradeapicall` package source rather than guessed. The CROSS client could
not be, and says so: `marsa-ingest probe-cross` exists to confirm the shape in
one command before committing to a long scrape.

---

## Phase 1: the "Deep Harbour" design system

### Colour

All tokens are authored in **OKLCH**, Tailwind v4's default colour space.
OKLCH is perceptually uniform, which is what makes the three routing-path
colours read as peers rather than a hierarchy: holding lightness constant
across hues yields swatches that *look* equally bright.

Dark is the designed-first theme; light is a deliberate re-derivation, not a
mechanical inversion — accents drop in lightness and gain chroma so they
survive a white ground.

The three routing paths are separated by **hue distance** (cyan 210° / violet
296° / emerald 156°) rather than lightness, so they remain distinguishable
under deuteranopia. Colour never carries the routing decision alone: the path
name is always present as text and each path has its own icon.

### Contrast is measured, not claimed

`/theme` renders every token in both themes and computes WCAG contrast ratios
**at runtime, from the pixels the browser actually painted** (1×1 canvas
readback). Flip the theme and the ratios re-measure.

This caught three real failures during the build: the light-mode risk chips sat
at 4.49:1, 3.38:1 and 4.49:1 against their own tinted backgrounds. Amber was
the worst offender and had to drop from L 0.612 to L 0.536 to clear 4.5:1 — a
mid-lightness warm hue on a warm tint is the most common AA failure in status
UI. All 46 measured pairs now pass.

Decorative borders are reported **without** a threshold: WCAG 1.4.11 applies to
boundaries needed to identify a control, not to aesthetic dividers. Holding a
subtle card edge to 3:1 would make the report cry wolf.

### Theme toggle

A three-state control — **Light / Dark / System** — implemented as an ARIA
`radiogroup`, because the options are mutually exclusive and "System" is not an
on/off state. It ships the full radio keyboard contract:

- Roving tabindex — the group is one tab stop, not three
- Arrow keys move *and* select; `Home`/`End` jump to first/last
- The System option announces what the OS currently resolves to

**Transition.** The theme change is a circular reveal driven by the native
[View Transitions API](https://developer.mozilla.org/docs/Web/API/View_Transitions_API),
expanding from the exact point that was clicked. The animation always moves the
*dark* layer in both directions — the new dark snapshot grows in, or the old
dark snapshot shrinks away — so switching to light never blasts an expanding
white disc across a dark viewport.

It degrades in this order:

1. `prefers-reduced-motion: reduce` → instant swap, no transition at all
2. No View Transitions API (Firefox < 144, older Safari) → instant swap
3. No visible change (picking "System" while it already matches) → persist the
   choice without animating a no-op

**One implementation detail worth knowing:** `next-themes` applies the theme
class from a *passive effect*, so the obvious
`document.startViewTransition(() => setTheme(next))` snapshots an unchanged DOM
and animates a wipe between two identical frames. The class is therefore
applied deterministically inside the transition callback; next-themes' own
effect then re-applies the same value as a no-op. See
`src/components/theme/use-theme-transition.ts`.

No flash on load: next-themes injects a blocking inline script that sets the
class before first paint, which is why `<html>` carries
`suppressHydrationWarning`.

---

## Verification

Phase 1 was verified in a real browser (Chromium via Playwright), not by
inspection:

| Check | Result |
|---|---|
| System preference honoured on first paint (light + dark) | ✅ |
| Each toggle option applies class, `color-scheme` and `localStorage` | ✅ |
| "System" resolves back to the OS preference | ✅ |
| Theme persists across reload with no flash | ✅ |
| Roving tabindex exposes exactly one tab stop | ✅ |
| Arrow keys select; `Home` selects first | ✅ |
| `prefers-reduced-motion` skips `startViewTransition` entirely | ✅ |
| 46 contrast pairs measured across both themes | ✅ 0 failures |
| No horizontal overflow at 360px (`/` and `/theme`) | ✅ |
| Zero console errors | ✅ |

---

---

## Phase A: data ingestion

Four corpora, four different access stories — documented rather than smoothed
over:

| Corpus | Source | Auth | Honest constraint |
|---|---|---|---|
| CBP CROSS rulings | `rulings.cbp.gov` | none | **Undocumented API.** Client accepts multiple key spellings and fails loudly on a shape change |
| UN Comtrade | `comtradeapi.un.org` | none | Free tier caps a response at 500 records; this is a *sampled* 288-request grid, not a mirror |
| DataCo | Kaggle | **required** | Cannot be fetched anonymously — no unattended path exists |
| World Bank LPI 2.0 | `api.worldbank.org` | none | CPPI ships as a report annex, not an API, so it loads from a local CSV |

Everything shares one HTTP layer: token-bucket rate limiting, exponential
backoff with jitter, `Retry-After` honoured, an honest `User-Agent`, and an
on-disk cache that makes a multi-thousand-request scrape resumable. 4xx is
deliberately *not* retried — that means our request is wrong, and hammering a
public service five times to learn the same thing is rude.

Every corpus is written with a provenance manifest recording origin, record
count, upstream request count, content hash, and **why it is a subset**. Those
manifests are rendered at `/data` in the UI, because a promise about what you
pulled is only worth something if it is checkable.

### Two bugs the work surfaced

**The Comtrade parameter casing.** The preview endpoint takes `reportercode`
(lowercase `c`) while every other parameter is camelCase. Sending `reporterCode`
is silently ignored and returns data for *all* reporters — a wrong result that
looks like a working one. This was found by reading the official client's source
instead of trusting prose docs, and there is a test asserting the wire parameter.

**An HTS regex that truncated every 10-digit code.** `\d{4}\.\d{2}(?:\.\d{2}){0,2}`
matches only `.00` of `8507.60.0020`, fails the trailing word boundary, then
silently backtracks to the 6-digit `8507.60`. Caught by a test asserting the
full code, not by reading the pattern.

---

## Phase B: the fast-path index

```
query
  ├─ dense  (embed → pgvector cosine top-20)
  └─ sparse (BM25 top-20)
        ↓  reciprocal rank fusion (k=60)
        ↓  rerank (top-12)
        ↓  child → parent rollup
  whole rulings, with citations
```

**Parent-child chunking.** A CROSS ruling describes, states an issue, reasons,
then holds. Nearly all answer-bearing signal is in the last two sections; most
of the token count is in the first two. So children are embedded and parents
are returned — retrieval matches the holding paragraph, the LLM gets the whole
quotable ruling.

**RRF over score blending.** Dense cosine sits in [-1, 1] and clusters around
0.2–0.6; BM25 is unbounded and scales with corpus statistics. Normalising two
per-query distributions is a losing game, so fusion uses rank only. The honest
cost: RRF discards confidence, which is what the rerank stage restores.

**BM25 is not optional here.** Half of what this corpus answers is exact-identifier
lookup ("what falls under 8507.60.0020"). Dense embeddings are systematically bad
at those — every tariff code occupies nearly the same neighbourhood. That
complementarity is the whole argument for hybrid, and it only holds if the
tokenizer keeps `8507.60.0020` as one token, which it does.

### Phase B caveat

Postgres 16 + pgvector 0.6.0 runs for real here, so the vector store, HNSW index
and SQL are genuinely exercised — 18 integration tests hit a live database.

But `huggingface.co` is blocked, so **MiniLM and the cross-encoder could not be
loaded**. The index was built with a hashed n-gram embedder and a lexical
reranker instead. Both are real techniques, not stubs, and both emit 384
dimensions so swapping in MiniLM is a re-embed rather than a migration. Neither
is *semantic*: they cannot match "power bank" to "portable battery charger".

`is_semantic` is threaded through the retrieval trace, the index manifest and
the `/data` page, so a quality figure cannot be published from a fallback run by
accident.

### Bugs this phase surfaced

- **`HOLDING` was being deleted from the index.** A uniform 25-word minimum
  dropped every holding — and holdings are routinely 15 words and the single
  most important sentence in the ruling. Chunk count doubled after the fix.
- **psycopg rejects multi-statement SQL as a prepared statement**, and
  `VECTOR(n)` is a type modifier that cannot be a bind parameter.
- **The "never lose a ruling" guard was a no-op** — it ran through the same
  filter it existed to bypass. Caught by a test, not by review.

---

## Phase C: the supply graph

3,035 nodes and 3,873 edges joining suppliers, products, categories, ports,
countries, tariff codes and rulings — 99.8% in one connected component.

```
        ruling ──cites──► ruling
           │
    classified_under
           ▼
     hts ──part_of──► hts_chapter ◄──classified_under── category
                                                          │  ▲
                                            ships_to      │  │ ships_from
                                                          ▼  │
   port ◄──routes_through── country ──trades_with──► country
     │                         ▲
  located_in ──────────────────┘
```

**The four corpora share no keys.** CROSS speaks HTS codes, Comtrade speaks UN
M49 numbers, DataCo speaks Spanish country names and merchandising categories,
the World Bank speaks ISO3 and UN/LOCODE. Connecting them *is* the work, and
every join is a decision this project makes rather than a fact it reads.

So each is declared in one module (`graph/bridges.py`), every edge it produces
carries `inferred=True` plus the rule that made it, and the narrator discloses
them inline:

| Bridge | Edges | The honest caveat |
|---|---|---|
| `country_name_to_iso3` | 248 | Unmapped names are dropped, not guessed — a wrong corridor is worse than a missing one |
| `hts_code_to_chapter` | 14 | None — definitional |
| `country_to_gateway_port` | 10 | Countries use many ports; one gateway **overstates** concentration at that port |
| `category_to_hs_chapter` | 8 | Editorial mapping, not a customs classification — a broker would often disagree |

**Traversal is undirected**, deliberately: exposure propagates *against* the
flow of goods. If Jebel Ali congests, disruption travels backwards to the
suppliers who ship through it. A directed walk from the port reaches almost
nothing.

**Risk is damped diffusion, best-path not summed.** Exposure is the strongest
dependency, so a supplier with one critical link ranks above one with three
weak ones. Inferred edges conduct at 80% — exposure reaching a node only
through assumptions ranks below exposure backed by data. Citation edges don't
conduct at all: a legal relationship is not a physical route.

```
$ marsa-graph query "Which suppliers are exposed if Jebel Ali congestion worsens?"

Starting from Jebel Ali (CPPI rank 12, 21.4h average vessel time), a 3-hop
traversal reaches 34 nodes across 105 edges.

Of those, 7 have only one gateway port in this graph and therefore no
substitutable routing: ARE, IND, USA, SGP, CHN, DEU, KOR. That is the
concentration risk — the remainder have at least one alternative.

Affected product categories: Electronics (0.10, 61% historical late rate), …

Provenance: 71 of 105 edges here (68%) are bridging assumptions rather than
recorded facts. …
```

### Bugs this phase surfaced

- **Default traversal depth was structurally wrong.** The graph layers are
  port → country → category → product, so a supplier-exposure question seeded
  on a port *cannot* be answered at 2 hops — it stops one layer short of the
  goods. Default is now 3, and shallow traversals say so rather than letting
  the omission read as "there are none".
- **Fixture geography was incoherent** — market, region, country and city were
  sampled independently, producing "Alemania / Mumbai / East Africa". That
  builds a nonsense graph. Now nested and asserted by test.
- **The citation sampler crashed on small corpora** — `rng.sample` was asked
  for more rulings than existed.

---

## Running it

### Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000
```

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                     # 193 tests; pgvector tests skip without a DSN
ruff check src tests

marsa-ingest fixtures      # synthetic corpora, no network
marsa-ingest status        # what's on disk, and is it real?
marsa-ingest export-report # publish manifests to the /data page

# Phase B — fast-path index
marsa-index build          # chunk → embed → pgvector + BM25
marsa-index query "What HTS code applies to lithium-ion power banks?"
marsa-index stats

# Phase C — supply graph
marsa-graph build          # assemble from every corpus
marsa-graph query "Which suppliers are exposed if Jebel Ali congestion worsens?"
marsa-graph stats          # composition, connectivity, inferred share
marsa-graph node port:AEJEA

# Run the pgvector integration tests against a real database:
#   docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=marsa \
#     -e POSTGRES_USER=marsa -e POSTGRES_DB=marsa pgvector/pgvector:pg16
#   MARSA_TEST_DSN=postgresql://marsa:marsa@localhost:5432/marsa pytest

# Where egress is permitted:
marsa-ingest probe-cross   # verify the CROSS contract FIRST
marsa-ingest cross
marsa-ingest comtrade
marsa-ingest worldbank
marsa-ingest dataco --download   # needs KAGGLE_USERNAME / KAGGLE_KEY
```

```bash
npm run lint         # ESLint (incl. React 19 compiler rules)
npx tsc --noEmit     # type-check
npm run build        # production build
```

Or via Docker:

```bash
cp .env.example .env
docker compose up frontend
```

No backend is required for Phase 1.

### Stack

**Frontend** — Next.js 16 (App Router) · React 19 · TypeScript · Tailwind v4 ·
next-themes · Motion v13 · lucide-react

**Backend** — Python 3.11 · Pydantic v2 · httpx + tenacity · pandas ·
pgvector · rank-bm25 · numpy · networkx · typer. Optional `[ml]` extra adds
sentence-transformers for MiniLM and the cross-encoder.

FastAPI + LangGraph land in Phase E — see `backend/README.md`.

---

## Project layout

```
frontend/
  src/app/
    layout.tsx              # fonts, ThemeProvider, shell, skip link
    globals.css             # the entire token system + view-transition CSS
    page.tsx                # hero + query console + bento
    theme/page.tsx          # live token explorer
  src/components/
    theme/
      theme-provider.tsx    # next-themes configuration, one place
      theme-toggle.tsx      # 3-state ARIA radiogroup
      use-theme-transition.ts   # View Transitions circular reveal
      token-explorer.tsx    # measured-contrast token tables
    route/
      route-badge.tsx       # the governance surface
      query-console.tsx     # staged reveal: badge → steps → answer
    layout/                 # header, footer
    sections/               # hero, bento
  src/lib/
    routing.ts              # routing domain model + Phase 1 fixtures
    contrast.ts             # WCAG measurement via canvas readback
    use-is-hydrated.ts      # SSR-safe hydration flag
    utils.ts                # cn()
backend/                    # placeholder — see backend/README.md
```

---

## Honest limitations

Carried forward from the project spec, and stated up front rather than
discovered at viva:

- **CROSS subset.** The fast path will index a subset of the 220,989 CBP CROSS
  rulings — HTS chapters relevant to a plausible Dubai re-export business
  (electronics, textiles, machinery) — not the full database. The subsetting
  logic will be documented with the ingestion script.
- **Comtrade rate limits.** The free preview API is rate-limited. The actual
  query volume and time window pulled will be documented; this is not live
  real-time trade data.
- **The classifier is a prompt, not a model.** Routing is a single few-shot LLM
  call. Its accuracy on the labelled test set will be reported honestly,
  including any systematic misclassification pattern (over-routing ambiguous
  queries to the agentic path is a plausible and genuinely interesting finding).
- **DataCo's label is one company's history.** `Late_delivery_risk` reflects
  that firm's specific operations. The risk model demonstrates the technique,
  not a universally generalisable prediction.
- **Phase 1 figures are fixtures.** Every latency and cost number currently in
  the UI is an illustrative placeholder, labelled as such in the interface.

---

## Design decisions worth defending

- **The Route Badge is a governance decision, not a design flourish.** It makes
  an autonomous routing decision auditable, which is the direction enterprise
  agentic AI governance is moving.
- **The results card is empty on purpose.** Shipping plausible-looking benchmark
  numbers before the harness runs would undercut the one claim this project
  exists to make.
- **Sequencing is the demo.** The query console reveals *why* a path was chosen
  before revealing what it found. Reversing that order turns a router into a
  chat box.
- **Contrast is measured rather than asserted** — and the measurement found real
  bugs, which is the argument for doing it that way.
