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
| A | Data ingestion — CROSS, Comtrade, DataCo, World Bank LPI | ⬜ Planned |
| B | Fast-path index — chunking, pgvector, BM25, reranker | ⬜ Planned |
| C | Graph construction — NetworkX supplier/port/country graph | ⬜ Planned |
| D | ML layer — late-delivery risk, port-congestion tiering | ⬜ Planned |
| E | LangGraph router + FastAPI gateway + SSE streaming | ⬜ Planned |
| F | Evaluation harness → `RESULTS.md` | ⬜ Planned |

Phase 1 runs entirely on local fixtures. **No benchmark numbers are published
yet** — the dashboard's results card is deliberately empty rather than filled
with invented figures, because the whole thesis depends on those numbers being
measured.

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

## Running it

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000
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

Next.js 16 (App Router) · React 19 · TypeScript · Tailwind v4 · next-themes ·
Motion v13 · lucide-react

The backend (FastAPI + LangGraph + pgvector + NetworkX) lands in later phases —
see `backend/README.md` for the planned layout.

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
