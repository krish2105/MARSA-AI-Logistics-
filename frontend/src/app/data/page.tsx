import type { Metadata } from "next";
import {
  AlertTriangle,
  Anchor,
  Ban,
  CheckCircle2,
  Database,
  Layers,
  Network,
} from "lucide-react";

import report from "@/data/ingestion-report.json";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: "Data provenance",
  description:
    "What Phase A ingested, from where, how much of it, and whether it is real — read directly from the ingestion manifests.",
};

const SOURCE_LABELS: Record<string, string> = {
  cbp_cross_rulings: "CBP CROSS rulings",
  un_comtrade: "UN Comtrade",
  dataco_supply_chain: "DataCo Supply Chain",
  worldbank_lpi_cppi: "World Bank LPI 2.0 / CPPI",
};

export default function DataPage() {
  const { corpora, anySynthetic, totalRecords, generatedAt } = report;
  const index = report.index as IndexManifest | null;
  const graph = report.graph as GraphManifest | null;
  const modelCard = report.modelCard as ModelCard | null;
  const congestion = (report.congestion ?? []) as PortCongestion[];

  return (
    <div className="mx-auto max-w-5xl px-4 py-14 sm:px-6">
      <header className="mb-10 max-w-3xl">
        <p className="mb-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Phase A · data provenance
        </p>
        <h1
          className="font-semibold tracking-tight text-balance"
          style={{ fontSize: "var(--text-step-5)", lineHeight: 1.08 }}
        >
          What was ingested, and whether it&rsquo;s real
        </h1>
        <p className="mt-5 leading-relaxed text-muted-foreground">
          Rendered straight from the ingestion manifests. Every corpus records
          its origin, how many upstream requests produced it, and why it is a
          subset rather than the whole source — so the project&rsquo;s claims
          about what it pulled are checkable rather than asserted.
        </p>
      </header>

      {anySynthetic && (
        <div
          role="alert"
          className="mb-10 flex gap-3 rounded-xl border border-risk-medium/40 bg-risk-medium-muted p-4"
        >
          <AlertTriangle
            className="mt-0.5 size-5 shrink-0 text-risk-medium"
            aria-hidden
          />
          <div className="space-y-1.5">
            <p className="font-semibold text-risk-medium">
              At least one corpus is synthetic
            </p>
            <p className="text-sm leading-relaxed text-foreground/80">
              Outbound access to rulings.cbp.gov, comtradeapi.un.org,
              api.worldbank.org and kaggle.com is blocked by network policy in
              the build environment, so the live ingestors could not run here.
              Seeded fixtures stand in to unblock Phases B&ndash;F. They match
              the real schemas exactly and are useless for anything else:{" "}
              <strong>
                no benchmark figure in RESULTS.md may be derived from them
              </strong>
              . Run the ingestors where egress is permitted to replace them.
            </p>
          </div>
        </div>
      )}

      <dl className="mb-10 grid gap-4 sm:grid-cols-3">
        <Stat label="Corpora" value={corpora.length.toLocaleString()} />
        <Stat label="Total records" value={totalRecords.toLocaleString()} />
        <Stat
          label="Report generated"
          value={new Date(generatedAt).toISOString().slice(0, 16).replace("T", " ")}
        />
      </dl>

      <div className="space-y-4">
        {corpora.map((corpus) => {
          const synthetic = corpus.origin === "synthetic";
          return (
            <article
              key={corpus.name}
              className="rounded-2xl border border-border bg-card p-5"
            >
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <Database
                  className="size-4 shrink-0 text-muted-foreground"
                  aria-hidden
                />
                <h2 className="font-semibold">
                  {SOURCE_LABELS[corpus.source] ?? corpus.source}
                </h2>
                <code className="rounded bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">
                  {corpus.name}.jsonl
                </code>
                <span
                  className={cn(
                    "ml-auto inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium",
                    synthetic
                      ? "bg-risk-medium-muted text-risk-medium"
                      : "bg-risk-low-muted text-risk-low",
                  )}
                >
                  {synthetic ? (
                    <AlertTriangle className="size-3.5" aria-hidden />
                  ) : (
                    <CheckCircle2 className="size-3.5" aria-hidden />
                  )}
                  {synthetic ? "SYNTHETIC" : "live"}
                </span>
              </div>

              <dl className="tabular mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
                <Field label="Records" value={corpus.recordCount.toLocaleString()} />
                <Field
                  label="Upstream requests"
                  value={corpus.requestCount.toLocaleString()}
                />
                <Field label="Cache hits" value={corpus.cacheHits.toLocaleString()} />
                <Field label="Duration" value={`${corpus.durationSeconds}s`} />
              </dl>

              {corpus.subsetRationale && (
                <div className="mt-4 border-t border-border/70 pt-3">
                  <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                    Why this subset
                  </p>
                  <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                    {corpus.subsetRationale}
                  </p>
                </div>
              )}

              <p className="mt-4 truncate font-mono text-xs text-muted-foreground">
                sha256:{corpus.sha256}…
              </p>
            </article>
          );
        })}
      </div>

      {index && <IndexSection index={index} />}
      {graph && <GraphSection graph={graph} />}
      {modelCard && <ModelSection card={modelCard} congestion={congestion} />}

      <p className="mt-10 text-sm leading-relaxed text-muted-foreground">
        Regenerate with{" "}
        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
          marsa-ingest export-report
        </code>{" "}
        after any ingestion or index run.
      </p>
    </div>
  );
}

interface IndexManifest {
  built_at: string;
  duration_seconds: number;
  rulings: number;
  chunks: number;
  chunks_per_ruling: number;
  median_chunk_words: number;
  vector_store: string;
  embedding: { backend: string; dim: number; semantic: boolean };
  sections: Record<string, number>;
  semantic_embeddings: boolean;
}

/** Rollup weights, mirrored from backend chunking.py. */
const SECTION_WEIGHTS: Record<string, number> = {
  HOLDING: 1.0,
  "LAW AND ANALYSIS": 0.92,
  ANALYSIS: 0.92,
  SUBJECT: 0.8,
  ISSUE: 0.75,
  "DESCRIPTION OF MERCHANDISE": 0.62,
  MERCHANDISE: 0.62,
  FACTS: 0.62,
  PREAMBLE: 0.55,
  "EFFECT ON OTHER RULINGS": 0.5,
};

function IndexSection({ index }: { index: IndexManifest }) {
  const total = Object.values(index.sections).reduce((a, b) => a + b, 0);
  const ordered = Object.entries(index.sections).sort(
    (a, b) => (SECTION_WEIGHTS[b[0]] ?? 0.6) - (SECTION_WEIGHTS[a[0]] ?? 0.6),
  );

  return (
    <section className="mt-14" aria-labelledby="index-heading">
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <Layers className="size-5 shrink-0 text-muted-foreground" aria-hidden />
        <h2
          id="index-heading"
          className="font-semibold tracking-tight"
          style={{ fontSize: "var(--text-step-2)" }}
        >
          Phase B — fast-path index
        </h2>
        <span
          className={cn(
            "ml-auto inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium",
            index.semantic_embeddings
              ? "bg-risk-low-muted text-risk-low"
              : "bg-risk-medium-muted text-risk-medium",
          )}
        >
          {index.semantic_embeddings ? (
            <CheckCircle2 className="size-3.5" aria-hidden />
          ) : (
            <AlertTriangle className="size-3.5" aria-hidden />
          )}
          {index.semantic_embeddings ? "semantic" : "NON-SEMANTIC"}
        </span>
      </div>

      <p className="mb-6 max-w-3xl text-sm leading-relaxed text-muted-foreground">
        Parent-child chunking over the CROSS corpus: children are embedded and
        BM25-indexed, whole rulings are returned. Retrieval fuses dense and
        sparse results by reciprocal rank, then reranks.{" "}
        {!index.semantic_embeddings && (
          <>
            This index was built with hashed n-grams rather than MiniLM, because
            huggingface.co is unreachable here — it matches{" "}
            <strong>lexically, not semantically</strong>, so no retrieval-quality
            figure derived from it is publishable.
          </>
        )}
      </p>

      <dl className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Rulings indexed" value={index.rulings.toLocaleString()} />
        <Stat label="Child chunks" value={index.chunks.toLocaleString()} />
        <Stat label="Chunks per ruling" value={index.chunks_per_ruling.toFixed(1)} />
        <Stat label="Vector store" value={index.vector_store.replace("Store", "")} />
      </dl>

      <div className="rounded-2xl border border-border bg-card p-5">
        <h3 className="text-sm font-semibold">Chunks by section</h3>
        <p className="mt-1.5 text-xs text-muted-foreground">
          Rollup weight decides which ruling wins when several match — a holding
          is the decision, a description is context.
        </p>
        <ul className="mt-4 space-y-2.5">
          {ordered.map(([section, count]) => {
            const weight = SECTION_WEIGHTS[section] ?? 0.6;
            return (
              <li key={section} className="flex items-center gap-3">
                <span className="w-52 shrink-0 truncate text-sm">{section}</span>
                <span
                  aria-hidden
                  className="h-2 rounded-full bg-route-fast"
                  style={{ width: `${Math.max(4, weight * 55)}%`, opacity: 0.35 + weight * 0.65 }}
                />
                <span className="tabular ml-auto shrink-0 font-mono text-xs text-muted-foreground">
                  {count.toLocaleString()} · w{weight.toFixed(2)}
                </span>
              </li>
            );
          })}
        </ul>
        <p className="tabular mt-4 border-t border-border/70 pt-3 font-mono text-xs text-muted-foreground">
          {total.toLocaleString()} chunks · {index.embedding.backend} ·{" "}
          {index.embedding.dim}d · built in {index.duration_seconds}s
        </p>
      </div>
    </section>
  );
}

interface GraphManifest {
  built_at: string;
  size_bytes: number;
  stats: {
    nodes: number;
    edges: number;
    nodesByKind: Record<string, number>;
    edgesByKind: Record<string, number>;
    inferredEdges: number;
    inferredByBasis: Record<string, number>;
    inferredShare: number;
    components: number;
    largestComponent: number;
    isolatedNodes: number;
    origin: string;
  };
  bridge_rules: Record<string, { description: string; caveat: string }>;
}

function GraphSection({ graph }: { graph: GraphManifest }) {
  const s = graph.stats;
  const connected = s.nodes ? s.largestComponent / s.nodes : 0;
  const topNodes = Object.entries(s.nodesByKind).sort((a, b) => b[1] - a[1]);
  const topEdges = Object.entries(s.edgesByKind).sort((a, b) => b[1] - a[1]);
  const maxEdge = Math.max(...topEdges.map(([, n]) => n), 1);

  return (
    <section className="mt-14" aria-labelledby="graph-heading">
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <Network className="size-5 shrink-0 text-muted-foreground" aria-hidden />
        <h2
          id="graph-heading"
          className="font-semibold tracking-tight"
          style={{ fontSize: "var(--text-step-2)" }}
        >
          Phase C — supply graph
        </h2>
        <span
          className={cn(
            "ml-auto inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium",
            s.origin === "synthetic"
              ? "bg-risk-medium-muted text-risk-medium"
              : "bg-risk-low-muted text-risk-low",
          )}
        >
          {s.origin === "synthetic" ? (
            <AlertTriangle className="size-3.5" aria-hidden />
          ) : (
            <CheckCircle2 className="size-3.5" aria-hidden />
          )}
          {s.origin === "synthetic" ? "SYNTHETIC" : "live"}
        </span>
      </div>

      <p className="mb-6 max-w-3xl text-sm leading-relaxed text-muted-foreground">
        Suppliers, products, ports, countries and tariff chapters in one
        NetworkX graph. The four corpora share no keys, so joining them requires
        assumptions — every such edge is marked <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">inferred</code>{" "}
        and carries the rule that produced it, so an answer can never quietly
        rest on one.
      </p>

      <dl className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Nodes" value={s.nodes.toLocaleString()} />
        <Stat label="Edges" value={s.edges.toLocaleString()} />
        <Stat label="Connected" value={`${(connected * 100).toFixed(1)}%`} />
        <Stat
          label="Inferred edges"
          value={`${s.inferredEdges.toLocaleString()} (${(s.inferredShare * 100).toFixed(0)}%)`}
        />
      </dl>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-border bg-card p-5">
          <h3 className="text-sm font-semibold">Nodes by kind</h3>
          <ul className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2">
            {topNodes.map(([kind, count]) => (
              <li key={kind} className="flex items-baseline gap-2 text-sm">
                <span className="truncate text-muted-foreground">{kind}</span>
                <span className="tabular ml-auto font-mono text-xs">
                  {count.toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-2xl border border-border bg-card p-5">
          <h3 className="text-sm font-semibold">Edges by kind</h3>
          <ul className="mt-4 space-y-2">
            {topEdges.map(([kind, count]) => {
              const inferred = Boolean(s.inferredByBasis) && ["routes_through", "ships_from", "ships_to"].includes(kind);
              return (
                <li key={kind} className="flex items-center gap-3 text-sm">
                  <span className="w-36 shrink-0 truncate text-muted-foreground">
                    {kind}
                  </span>
                  <span
                    aria-hidden
                    className={cn(
                      "h-2 rounded-full",
                      inferred ? "bg-risk-medium" : "bg-route-graph",
                    )}
                    style={{ width: `${Math.max(3, (count / maxEdge) * 45)}%`, opacity: 0.75 }}
                  />
                  <span className="tabular ml-auto shrink-0 font-mono text-xs">
                    {count.toLocaleString()}
                    {inferred && <span className="text-risk-medium"> ·inf</span>}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      </div>

      <div className="mt-4 rounded-2xl border border-border bg-card p-5">
        <h3 className="text-sm font-semibold">Bridging assumptions</h3>
        <p className="mt-1.5 text-xs text-muted-foreground">
          Cross-corpus joins this project is asserting, not reading. Each is
          disclosed inline whenever an answer depends on it.
        </p>
        <dl className="mt-4 space-y-4">
          {Object.entries(graph.bridge_rules).map(([name, rule]) => (
            <div key={name}>
              <dt className="flex flex-wrap items-baseline gap-2">
                <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                  {name}
                </code>
                {s.inferredByBasis[name] !== undefined && (
                  <span className="tabular font-mono text-xs text-muted-foreground">
                    {s.inferredByBasis[name].toLocaleString()} edges
                  </span>
                )}
              </dt>
              <dd className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                {rule.description}
              </dd>
              <dd
                className={cn(
                  "mt-1 text-sm leading-relaxed",
                  rule.caveat.startsWith("None")
                    ? "text-muted-foreground/70"
                    : "text-risk-medium",
                )}
              >
                {rule.caveat}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}

interface ModelResultJson {
  name: string;
  metrics: Record<string, number>;
  trainSeconds: number;
  leaky: boolean;
}

interface ModelCard {
  task: string;
  target: string;
  results: {
    models: ModelResultJson[];
    best: string | null;
    splitSizes: Record<string, number>;
    splitBoundaries: Record<string, string>;
    baseRates: Record<string, number>;
    origin: string;
    leakageDemo: ModelResultJson | null;
  };
  features: { used: string[]; count: number };
  excluded_for_leakage: Record<string, string>;
  validation: { split: string; why: string };
  limitations: string[];
}

interface PortCongestion {
  unlocode: string;
  portName: string;
  score: number;
  tier: string;
}

const TIER_CLASS: Record<string, string> = {
  high: "bg-risk-high-muted text-risk-high",
  elevated: "bg-risk-medium-muted text-risk-medium",
  moderate: "bg-route-fast-muted text-route-fast",
  low: "bg-risk-low-muted text-risk-low",
};

function ModelSection({
  card,
  congestion,
}: {
  card: ModelCard;
  congestion: PortCongestion[];
}) {
  const { models, best, leakageDemo, baseRates, splitBoundaries } = card.results;
  const honest = models.filter((m) => !m.leaky);
  const bestModel = honest.find((m) => m.name === best);
  const testBaseRate = baseRates.test ?? 0;

  return (
    <section className="mt-14" aria-labelledby="model-heading">
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <Anchor className="size-5 shrink-0 text-muted-foreground" aria-hidden />
        <h2
          id="model-heading"
          className="font-semibold tracking-tight"
          style={{ fontSize: "var(--text-step-2)" }}
        >
          Phase D — risk models
        </h2>
        <span
          className={cn(
            "ml-auto inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium",
            card.results.origin === "synthetic"
              ? "bg-risk-medium-muted text-risk-medium"
              : "bg-risk-low-muted text-risk-low",
          )}
        >
          {card.results.origin === "synthetic" ? (
            <AlertTriangle className="size-3.5" aria-hidden />
          ) : (
            <CheckCircle2 className="size-3.5" aria-hidden />
          )}
          {card.results.origin === "synthetic" ? "SYNTHETIC" : "live"}
        </span>
      </div>

      <p className="mb-6 max-w-3xl text-sm leading-relaxed text-muted-foreground">
        {card.task} Validated on a <strong>{card.validation.split}</strong> —{" "}
        {card.validation.why}
      </p>

      {/* Comparison */}
      <div className="mb-4 overflow-x-auto rounded-2xl border border-border bg-card">
        <table className="w-full min-w-[36rem] text-sm">
          <caption className="sr-only">Model comparison on the held-out test slice</caption>
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wider text-muted-foreground">
              <th scope="col" className="px-4 py-3 font-medium">Model</th>
              <th scope="col" className="px-4 py-3 text-right font-medium">PR-AUC</th>
              <th scope="col" className="px-4 py-3 text-right font-medium">Lift</th>
              <th scope="col" className="px-4 py-3 text-right font-medium">ROC-AUC</th>
              <th scope="col" className="px-4 py-3 text-right font-medium">Brier</th>
              <th scope="col" className="px-4 py-3 text-right font-medium">Train</th>
            </tr>
          </thead>
          <tbody className="tabular divide-y divide-border/70">
            {[...honest]
              .sort((a, b) => b.metrics.pr_auc - a.metrics.pr_auc)
              .map((m) => (
                <tr key={m.name} className={m.name === best ? "bg-muted/40" : undefined}>
                  <td className="px-4 py-3 font-mono text-xs">
                    {m.name}
                    {m.name === best && (
                      <span className="ml-2 rounded bg-risk-low-muted px-1.5 py-0.5 text-[0.65rem] font-medium text-risk-low">
                        best
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right font-medium">
                    {m.metrics.pr_auc.toFixed(4)}
                  </td>
                  <td className="px-4 py-3 text-right text-muted-foreground">
                    +{m.metrics.pr_auc_lift.toFixed(4)}
                  </td>
                  <td className="px-4 py-3 text-right text-muted-foreground">
                    {m.metrics.roc_auc.toFixed(4)}
                  </td>
                  <td className="px-4 py-3 text-right text-muted-foreground">
                    {m.metrics.brier.toFixed(4)}
                  </td>
                  <td className="px-4 py-3 text-right text-muted-foreground">
                    {m.trainSeconds.toFixed(2)}s
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <p className="tabular mb-6 text-xs text-muted-foreground">
        No-skill PR-AUC baseline = test base rate ={" "}
        <strong className="text-foreground">{testBaseRate.toFixed(4)}</strong>. Test
        slice spans {splitBoundaries.test}.
        {bestModel && honest.length > 1 && (
          <>
            {" "}
            Logistic regression leads the boosted models here — on tabular data with a
            strong main effect, that happens, and reporting it beats assuming otherwise.
          </>
        )}
      </p>

      {/* The leakage trap */}
      {leakageDemo && bestModel && (
        <div
          role="alert"
          className="mb-6 flex gap-3 rounded-xl border border-risk-high/40 bg-risk-high-muted p-4"
        >
          <Ban className="mt-0.5 size-5 shrink-0 text-risk-high" aria-hidden />
          <div className="space-y-1.5">
            <p className="font-semibold text-risk-high">
              The leakage trap, quantified
            </p>
            <p className="text-sm leading-relaxed text-foreground/80">
              Given <code className="rounded bg-background/60 px-1 py-0.5 font-mono text-xs">days_for_shipping_real</code>{" "}
              and <code className="rounded bg-background/60 px-1 py-0.5 font-mono text-xs">delivery_status</code>,
              the same model scores{" "}
              <strong className="tabular">
                {leakageDemo.metrics.roc_auc.toFixed(4)}
              </strong>{" "}
              ROC-AUC against{" "}
              <strong className="tabular">
                {bestModel.metrics.roc_auc.toFixed(4)}
              </strong>{" "}
              honestly — a{" "}
              <strong className="tabular">
                +{(leakageDemo.metrics.roc_auc - bestModel.metrics.roc_auc).toFixed(4)}
              </strong>{" "}
              illusion. The label is <em>defined</em> as{" "}
              <code className="rounded bg-background/60 px-1 py-0.5 font-mono text-xs">
                real &gt; scheduled
              </code>
              , so those columns restate the answer — and neither is known when the
              order is booked, which is the only moment a prediction has value.
            </p>
          </div>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Excluded features */}
        <div className="rounded-2xl border border-border bg-card p-5">
          <h3 className="text-sm font-semibold">Excluded for leakage</h3>
          <dl className="mt-4 space-y-3">
            {Object.entries(card.excluded_for_leakage).map(([column, reason]) => (
              <div key={column}>
                <dt className="flex items-center gap-2">
                  <Ban className="size-3.5 shrink-0 text-risk-high" aria-hidden />
                  <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                    {column}
                  </code>
                </dt>
                <dd className="mt-1 pl-5 text-xs leading-relaxed text-muted-foreground">
                  {reason}
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-4 border-t border-border/70 pt-3 text-xs text-muted-foreground">
            {card.features.count} features survive — everything known at booking.
          </p>
        </div>

        {/* Port congestion */}
        <div className="rounded-2xl border border-border bg-card p-5">
          <h3 className="text-sm font-semibold">Port congestion tiers</h3>
          <p className="mt-1.5 text-xs text-muted-foreground">
            Rule-based composite of vessel hours, import dwell and CPPI rank. Not
            learned: there is no congestion label, and CPPI rank is an output of the
            same measurements.
          </p>
          <ul className="mt-4 space-y-2">
            {congestion.slice(0, 8).map((port) => (
              <li key={port.unlocode} className="flex items-center gap-3 text-sm">
                <span className="truncate">{port.portName}</span>
                <code className="shrink-0 font-mono text-xs text-muted-foreground">
                  {port.unlocode}
                </code>
                <span
                  className={cn(
                    "tabular ml-auto shrink-0 rounded px-1.5 py-0.5 text-xs font-medium",
                    TIER_CLASS[port.tier] ?? "bg-muted text-muted-foreground",
                  )}
                >
                  {port.score.toFixed(3)} · {port.tier}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* Limitations */}
      <div className="mt-4 rounded-2xl border border-border bg-card p-5">
        <h3 className="text-sm font-semibold">Limitations</h3>
        <ul className="mt-3 space-y-2">
          {card.limitations.map((item) => (
            <li
              key={item}
              className={cn(
                "flex gap-2.5 text-sm leading-relaxed",
                item.startsWith("TRAINED ON SYNTHETIC")
                  ? "text-risk-medium"
                  : "text-muted-foreground",
              )}
            >
              <span aria-hidden className="mt-2 size-1 shrink-0 rounded-full bg-current" />
              {item}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="tabular mt-1 text-lg font-semibold">{value}</dd>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 font-medium">{value}</dd>
    </div>
  );
}
