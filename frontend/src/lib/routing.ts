/**
 * Routing domain model.
 *
 * These types mirror the structured audit-log record the LangGraph router will
 * emit in Phase E (query, chosen path, sources, confidence, latency, cost), so
 * the UI built against these fixtures will bind to the real SSE stream without
 * reshaping. The fixtures below are clearly labelled as mock data and are the
 * only place placeholder numbers live.
 */

export type RoutePathId = "fast" | "agentic" | "graph" | "compute";

export type Classification =
  | "simple_factual"
  | "multi_hop_reasoning"
  | "relationship_network"
  /**
   * Phase H. Not a classifier output like the others — a deterministic
   * pre-filter decides this one before the classifier is consulted, because a
   * duty question reaching a model produces confident, plausible, wrong
   * arithmetic. Named here so the Route Badge can label it.
   */
  | "duty_calculation";

export interface RoutePath {
  id: RoutePathId;
  /** Human label used in the badge. */
  label: string;
  /** The classifier's output category that selects this path. */
  classification: Classification;
  /** One-line description of the retrieval strategy. */
  strategy: string;
  /** Corpora this path reads from. */
  corpora: string[];
  /** Tailwind token names — kept as literal classes so they survive purging. */
  accent: string;
  /**
   * Text colour for a chip filled with `accent`.
   *
   * Lives here rather than in a per-path conditional chain at the call site.
   * The chain version silently omitted `compute` when Phase H added it, so the
   * chip rendered light-on-light at 1.46:1 — a real AA failure that looked
   * merely "muted". A record keyed by RoutePathId makes the compiler catch the
   * next one.
   */
  accentForeground: string;
  accentMuted: string;
  accentText: string;
}

export const ROUTE_PATHS: Record<RoutePathId, RoutePath> = {
  fast: {
    id: "fast",
    label: "Fast Path",
    classification: "simple_factual",
    strategy: "Hybrid RAG — dense (MiniLM) + BM25, cross-encoder rerank",
    corpora: ["CBP CROSS rulings"],
    accent: "bg-route-fast",
    accentForeground: "text-route-fast-foreground",
    accentMuted: "bg-route-fast-muted",
    accentText: "text-route-fast",
  },
  agentic: {
    id: "agentic",
    label: "Agentic Path",
    classification: "multi_hop_reasoning",
    strategy: "LangGraph loop — plan → retrieve → critic → retry (max 2×)",
    corpora: ["UN Comtrade", "DataCo Supply Chain"],
    accent: "bg-route-agentic",
    accentForeground: "text-route-agentic-foreground",
    accentMuted: "bg-route-agentic-muted",
    accentText: "text-route-agentic",
  },
  graph: {
    id: "graph",
    label: "Graph Path",
    classification: "relationship_network",
    strategy: "GraphRAG — entity resolution → k-hop traversal → synthesis",
    corpora: ["NetworkX supply graph", "World Bank LPI 2.0"],
    accent: "bg-route-graph",
    accentForeground: "text-route-graph-foreground",
    accentMuted: "bg-route-graph-muted",
    accentText: "text-route-graph",
  },
  compute: {
    id: "compute",
    label: "Compute Path",
    classification: "duty_calculation",
    strategy: "Deterministic layered arithmetic — no model call in the answer",
    corpora: ["Regulatory instruments (Phase G)"],
    accent: "bg-route-compute",
    accentForeground: "text-route-compute-foreground",
    accentMuted: "bg-route-compute-muted",
    accentText: "text-route-compute",
  },
};

export interface RetrievalStep {
  label: string;
  detail: string;
}

export interface RoutedResponse {
  query: string;
  path: RoutePathId;
  /** Classifier confidence, 0–1. */
  confidence: number;
  /** Why the classifier chose this path — surfaced verbatim in the badge. */
  rationale: string;
  steps: RetrievalStep[];
  answer: string;
  citations: string[];
  latencyMs: number;
  /** Estimated USD cost. Free-tier in practice; priced at published rates so
   *  the cost-aware thesis is measurable rather than rhetorical. */
  costUsd: number;
}

/**
 * MOCK FIXTURES — Phase 1 only.
 *
 * Hand-written to exercise every branch of the UI (three paths, varying step
 * counts, citations present/absent). Every number here is illustrative and is
 * labelled as such in the interface. Real figures replace these in Phase F,
 * measured by the benchmark runner and published to RESULTS.md.
 */
export const SAMPLE_RESPONSES: RoutedResponse[] = [
  {
    query: "What HTS code applies to lithium-ion power banks re-exported from Jebel Ali?",
    path: "fast",
    confidence: 0.94,
    rationale:
      "Single-entity classification lookup. No cross-corpus join and no traversal required — hybrid retrieval over the rulings corpus is sufficient.",
    steps: [
      { label: "Dense retrieval", detail: "MiniLM · top-20 from pgvector" },
      { label: "Sparse retrieval", detail: "BM25 · top-20 from rulings index" },
      { label: "Rerank", detail: "Cross-encoder · 40 → 4 parent rulings" },
    ],
    answer:
      "Portable lithium-ion power banks are consistently classified under HTS 8507.60.00 as lithium-ion accumulators, not under 8504 as static converters. CROSS rulings turn on the article's principal function being energy storage rather than conversion, even where an integrated charging circuit is present.",
    citations: ["NY N302241", "HQ H289765", "NY N317884"],
    latencyMs: 1180,
    costUsd: 0.00021,
  },
  {
    query:
      "Which of our electronics shipments are exposed if the new tariff on HS 8541 takes effect next quarter?",
    path: "agentic",
    confidence: 0.88,
    rationale:
      "Requires decomposition: resolve affected HS lines, join against live trade flows, then intersect with shipment-level records. Three dependent hops — the fast path cannot chain them.",
    steps: [
      {
        label: "Plan",
        detail: "Decomposed into 3 sub-queries",
      },
      {
        label: "Retrieve",
        detail: "UN Comtrade · HS 8541 · AE↔CN, AE↔IN, 2023–2025",
      },
      { label: "Retrieve", detail: "DataCo · 1,284 matching order records" },
      { label: "Critic", detail: "Evidence sufficient · no retry" },
      { label: "Compose", detail: "Cross-referenced 2 corpora" },
    ],
    answer:
      "1,284 order records fall under HS 8541 (semiconductor devices, LEDs). 61% originate from partners covered by the proposed measure, concentrated in two shipping lanes. Median scheduled-to-actual delivery slack on those lanes is 1.8 days, so a tariff-driven customs hold longer than that would push the majority past contracted delivery dates.",
    citations: ["UN Comtrade 2025 · AE reporter", "DataCo orders 2015–2018"],
    latencyMs: 6420,
    costUsd: 0.00318,
  },
  {
    query: "Which suppliers are exposed if Jebel Ali congestion worsens?",
    path: "graph",
    confidence: 0.91,
    rationale:
      "Relationship query over the supplier–port–country network. Answering it means traversing edges, not matching text: no document contains the answer directly.",
    steps: [
      { label: "Entity resolution", detail: "\"Jebel Ali\" → node port:AEJEA" },
      { label: "Traversal", detail: "2-hop · 47 nodes, 112 edges" },
      { label: "Risk overlay", detail: "LPI 2.0 dwell-time + late-delivery model" },
      { label: "Synthesis", detail: "Subgraph → narrative" },
    ],
    answer:
      "Nineteen suppliers route through Jebel Ali, but exposure is not evenly distributed: six have no alternative port edge in the graph, and four of those six sit in product categories whose late-delivery-risk score is already above the median. Those four are the concentration risk — the remaining thirteen have at least one substitutable routing.",
    citations: ["World Bank LPI 2.0 (2025)", "Container Port Performance Index"],
    latencyMs: 3960,
    costUsd: 0.00142,
  },
  {
    query: "What duty applies to HTS 7326.90.86 from China on a $40,000 shipment?",
    path: "compute",
    // Not a classifier confidence. A deterministic pre-filter made this call
    // before the classifier was consulted, and the number is evidence count
    // rather than a probability — the badge labels it as such.
    confidence: 1,
    rationale:
      "A duty question, detected deterministically rather than classified. It is routed to a calculator because a language model asked to add 2.9% + 50% + 25% on $40,000 produces a number that looks right, and the number is the deliverable.",
    steps: [
      { label: "Parse", detail: "HTS 7326.90.86, origin CN" },
      { label: "Resolve instruments", detail: "3 layers in force on the entry date" },
      { label: "Compute", detail: "deterministic — no model call" },
    ],
    answer:
      "MFN 2.9% + Section 232 at 50% + Section 301 at 25% = 77.9%, or $31,160.00 on a $40,000 customs value. The February Section 232 instrument is excluded: its own window is still open, but the June proclamation replaced it. Every line cites the instrument that imposes it, and no language model touched any figure.",
    citations: [
      "HTS-73269086 (USITC column 1 general)",
      "FR-2026-06-232-STEEL",
      "FR-2026-301-CN",
    ],
    latencyMs: 4,
    costUsd: 0,
  },
];

/** Aggregate benchmark placeholders — replaced by the Phase F harness. */
export interface PathBenchmark {
  path: RoutePathId;
  routingAccuracy: number;
  medianLatencyMs: number;
  costPerQueryUsd: number;
  faithfulness: number;
}

export const BENCHMARKS: PathBenchmark[] = [
  { path: "fast", routingAccuracy: 0, medianLatencyMs: 0, costPerQueryUsd: 0, faithfulness: 0 },
  { path: "agentic", routingAccuracy: 0, medianLatencyMs: 0, costPerQueryUsd: 0, faithfulness: 0 },
  { path: "graph", routingAccuracy: 0, medianLatencyMs: 0, costPerQueryUsd: 0, faithfulness: 0 },
  { path: "compute", routingAccuracy: 0, medianLatencyMs: 0, costPerQueryUsd: 0, faithfulness: 0 },
];

export function formatLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`;
}

export function formatCost(usd: number): string {
  return `$${usd.toFixed(5)}`;
}
