import type { Metadata } from "next";
import {
  AlertTriangle,
  Anchor,
  Ban,
  CheckCircle2,
  Database,
  FlaskConical,
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
  const evaluation = (report.evaluation ?? null) as Evaluation | null;

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
      {evaluation && <EvalSection evaluation={evaluation} />}

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

interface Evaluation {
  generatedAt: string;
  gate: {
    status: string;
    mayPublish: boolean;
    classifierIsLlm: boolean;
    corporaAreReal: boolean;
    judgeAvailable: boolean;
    blockers: string[];
  };
  dataset: {
    total: number;
    byClass: Record<string, number>;
    byDifficulty: Record<string, number>;
  };
  routing: {
    classifier: { method: string; isLlm: boolean };
    n: number;
    accuracy: number;
    baselineAccuracy: number;
    liftOverBaseline: number;
    confusion: Record<string, Record<string, number>>;
    perClass: Record<
      string,
      { precision: number; recall: number; f1: number; support: number }
    >;
    byDifficulty: Record<string, { accuracy: number; count: number }>;
    confidence: {
      meanConfidenceCorrect: number;
      meanConfidenceWrong: number;
      separation: number;
    };
    bias: { errors: number; toCheaper: number; toMoreExpensive: number; note: string };
    worstCases: {
      query: string;
      expected: string;
      predicted: string;
      confidence: number;
    }[];
  };
  benchmark: {
    queriesRun: number;
    byPath: {
      path: string;
      medianLatencyMs: number;
      p95LatencyMs: number;
      coldStartMs: number | null;
      costPerQueryUsd: number;
      meanSources: number;
      meanAnswerChars: number;
    }[];
    findings: string[];
  };
  quality: {
    byPath: Record<
      string,
      {
        contextPrecision: number;
        contextRecall: number;
        faithfulness: number | string;
        answerRelevance: number | string;
      }
    >;
    judgeAvailable: boolean;
    judgeNote: string;
  };
}

const CLASS_SHORT: Record<string, string> = {
  simple_factual: "factual",
  multi_hop_reasoning: "multi-hop",
  relationship_network: "network",
};

/**
 * Blockers are authored as markdown so RESULTS.md and this page render the same
 * string. Only `**bold**` is used, so a split beats pulling in a parser.
 */
function Emphasised({ text }: { text: string }) {
  return (
    <>
      {text.split("**").map((part, i) =>
        i % 2 ? <strong key={i}>{part}</strong> : <span key={i}>{part}</span>,
      )}
    </>
  );
}

function EvalSection({ evaluation }: { evaluation: Evaluation }) {
  const { gate, routing, benchmark, quality, dataset } = evaluation;
  const classes = Object.keys(routing.confusion);
  const notMeasured = (v: number | string) =>
    typeof v === "number" ? v.toFixed(3) : "not measured";

  return (
    <section className="mt-14" aria-labelledby="eval-heading">
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <FlaskConical className="size-5 shrink-0 text-muted-foreground" aria-hidden />
        <h2
          id="eval-heading"
          className="font-semibold tracking-tight"
          style={{ fontSize: "var(--text-step-2)" }}
        >
          Phase F — evaluation
        </h2>
        <span
          className={cn(
            "ml-auto inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium",
            gate.mayPublish
              ? "bg-risk-low-muted text-risk-low"
              : "bg-risk-medium-muted text-risk-medium",
          )}
        >
          {gate.mayPublish ? (
            <CheckCircle2 className="size-3.5" aria-hidden />
          ) : (
            <AlertTriangle className="size-3.5" aria-hidden />
          )}
          {gate.status}
        </span>
      </div>

      {/* The gate. Deliberately above the numbers: a reader must not be able to
          reach the accuracy figure without first learning what produced it. */}
      {gate.blockers.length > 0 && (
        <div
          role="alert"
          className="mb-6 rounded-xl border border-risk-medium/40 bg-risk-medium-muted p-4"
        >
          <div className="flex gap-3">
            <AlertTriangle
              className="mt-0.5 size-5 shrink-0 text-risk-medium"
              aria-hidden
            />
            <div>
              <p className="font-semibold text-risk-medium">
                These numbers do not test the thesis
              </p>
              <p className="mt-1.5 text-sm leading-relaxed text-foreground/80">
                The harness ran end to end, which is worth showing. It cannot
                answer the question this project exists to ask, for{" "}
                {gate.blockers.length} reason
                {gate.blockers.length === 1 ? "" : "s"}:
              </p>
              <ol className="mt-3 space-y-2">
                {gate.blockers.map((blocker, i) => (
                  <li
                    key={blocker.slice(0, 40)}
                    className="flex gap-2.5 text-sm leading-relaxed text-foreground/80"
                  >
                    <span className="tabular shrink-0 text-muted-foreground">
                      {i + 1}.
                    </span>
                    <span>
                      <Emphasised text={blocker} />
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </div>
      )}

      <dl className="mb-6 grid gap-4 sm:grid-cols-4">
        <Stat
          label={gate.classifierIsLlm ? "Routing accuracy" : "Heuristic accuracy"}
          value={`${(routing.accuracy * 100).toFixed(1)}%`}
        />
        <Stat
          label="Majority baseline"
          value={`${(routing.baselineAccuracy * 100).toFixed(1)}%`}
        />
        <Stat
          label="Lift over baseline"
          value={`${routing.liftOverBaseline >= 0 ? "+" : ""}${(
            routing.liftOverBaseline * 100
          ).toFixed(1)}%`}
        />
        <Stat label="Labelled queries" value={dataset.total.toLocaleString()} />
      </dl>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Confusion */}
        <div className="overflow-hidden rounded-2xl border border-border bg-card">
          <div className="border-b border-border px-5 py-4">
            <h3 className="text-sm font-semibold">Confusion matrix</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Actual ↓ / predicted →. The diagonal is correct routing.
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                  <th scope="col" className="px-4 py-2.5 text-left font-medium">
                    Actual
                  </th>
                  {classes.map((c) => (
                    <th
                      key={c}
                      scope="col"
                      className="px-3 py-2.5 text-right font-medium"
                    >
                      {CLASS_SHORT[c] ?? c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {classes.map((actual) => (
                  <tr key={actual} className="border-b border-border/60 last:border-0">
                    <th scope="row" className="px-4 py-2.5 text-left font-medium">
                      {CLASS_SHORT[actual] ?? actual}
                    </th>
                    {classes.map((predicted) => {
                      const value = routing.confusion[actual][predicted];
                      const hit = actual === predicted;
                      return (
                        <td
                          key={predicted}
                          className={cn(
                            "tabular px-3 py-2.5 text-right",
                            // A zero is a result, not decoration, so it keeps
                            // full body contrast. Weight does the recession.
                            hit
                              ? "font-semibold text-risk-low"
                              : value > 0
                                ? "text-risk-medium"
                                : "text-muted-foreground",
                          )}
                        >
                          {value}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Per class */}
        <div className="overflow-hidden rounded-2xl border border-border bg-card">
          <div className="border-b border-border px-5 py-4">
            <h3 className="text-sm font-semibold">Per class</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              20 queries per class, so support is balanced by construction.
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                  <th scope="col" className="px-4 py-2.5 text-left font-medium">
                    Class
                  </th>
                  {["Precision", "Recall", "F1"].map((h) => (
                    <th
                      key={h}
                      scope="col"
                      className="px-3 py-2.5 text-right font-medium"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(routing.perClass).map(([name, m]) => (
                  <tr key={name} className="border-b border-border/60 last:border-0">
                    <th scope="row" className="px-4 py-2.5 text-left font-medium">
                      {CLASS_SHORT[name] ?? name}
                    </th>
                    <td className="tabular px-3 py-2.5 text-right text-muted-foreground">
                      {m.precision.toFixed(3)}
                    </td>
                    <td className="tabular px-3 py-2.5 text-right text-muted-foreground">
                      {m.recall.toFixed(3)}
                    </td>
                    <td className="tabular px-3 py-2.5 text-right text-muted-foreground">
                      {m.f1.toFixed(3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Error direction — the finding the spec asks to be reported either way */}
      <div className="mt-4 rounded-2xl border border-border bg-card p-5">
        <h3 className="text-sm font-semibold">Which way the errors go</h3>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          {routing.bias.note}
        </p>
        <p className="tabular mt-3 border-t border-border/70 pt-3 text-xs text-muted-foreground">
          Confidence separates signal from noise by{" "}
          <strong className="text-foreground">
            {routing.confidence.separation >= 0 ? "+" : ""}
            {routing.confidence.separation.toFixed(3)}
          </strong>{" "}
          ({routing.confidence.meanConfidenceCorrect.toFixed(3)} when correct vs{" "}
          {routing.confidence.meanConfidenceWrong.toFixed(3)} when wrong) — which is
          what makes the confidence number on the Route Badge worth showing.
        </p>
      </div>

      {/* Cost and latency */}
      <div className="mt-4 overflow-hidden rounded-2xl border border-border bg-card">
        <div className="border-b border-border px-5 py-4">
          <h3 className="text-sm font-semibold">Cost and latency per path</h3>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            All {benchmark.queriesRun} queries were forced down all three paths, so
            every row is a counterfactual rather than a measurement of whichever
            path the router happened to pick. Cold starts are excluded from the
            medians.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                <th scope="col" className="px-4 py-2.5 text-left font-medium">
                  Path
                </th>
                {["Median", "p95", "Cold", "Cost/query", "Sources", "Chars"].map(
                  (h) => (
                    <th
                      key={h}
                      scope="col"
                      className="px-3 py-2.5 text-right font-medium"
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {benchmark.byPath.map((row) => (
                <tr key={row.path} className="border-b border-border/60 last:border-0">
                  <th scope="row" className="px-4 py-2.5 text-left">
                    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                      {row.path}
                    </code>
                  </th>
                  <td className="tabular px-3 py-2.5 text-right font-medium">
                    {row.medianLatencyMs.toFixed(1)}ms
                  </td>
                  <td className="tabular px-3 py-2.5 text-right text-muted-foreground">
                    {row.p95LatencyMs.toFixed(1)}ms
                  </td>
                  <td className="tabular px-3 py-2.5 text-right text-muted-foreground">
                    {row.coldStartMs ? `${row.coldStartMs.toFixed(0)}ms` : "—"}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right text-muted-foreground">
                    ${row.costPerQueryUsd.toFixed(5)}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right text-muted-foreground">
                    {row.meanSources.toFixed(1)}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right text-muted-foreground">
                    {row.meanAnswerChars.toFixed(0)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {benchmark.findings.length > 0 && (
          <ul className="space-y-2.5 border-t border-border px-5 py-4">
            {benchmark.findings.map((finding) => (
              <li
                key={finding.slice(0, 40)}
                className="flex gap-2.5 text-sm leading-relaxed text-muted-foreground"
              >
                <span
                  aria-hidden
                  className="mt-2 size-1 shrink-0 rounded-full bg-current"
                />
                <span>
                  <Emphasised text={finding} />
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* RAGAS */}
      <div className="mt-4 overflow-hidden rounded-2xl border border-border bg-card">
        <div className="border-b border-border px-5 py-4">
          <h3 className="text-sm font-semibold">Answer quality (RAGAS)</h3>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {quality.judgeNote} Context precision and recall need no judge and are
            measured. A missing metric shown as <code>0.000</code> would be worse
            than no metric, so it is not shown that way.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                <th scope="col" className="px-4 py-2.5 text-left font-medium">
                  Path
                </th>
                {["Ctx precision", "Ctx recall", "Faithfulness", "Answer relevance"].map(
                  (h) => (
                    <th
                      key={h}
                      scope="col"
                      className="px-3 py-2.5 text-right font-medium"
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {Object.entries(quality.byPath).map(([path, m]) => (
                <tr key={path} className="border-b border-border/60 last:border-0">
                  <th scope="row" className="px-4 py-2.5 text-left">
                    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                      {path}
                    </code>
                  </th>
                  <td className="tabular px-3 py-2.5 text-right text-muted-foreground">
                    {m.contextPrecision.toFixed(3)}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right text-muted-foreground">
                    {m.contextRecall.toFixed(3)}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right text-muted-foreground">
                    {notMeasured(m.faithfulness)}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right text-muted-foreground">
                    {notMeasured(m.answerRelevance)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Most confident mistakes */}
      {routing.worstCases.length > 0 && (
        <div className="mt-4 rounded-2xl border border-border bg-card p-5">
          <h3 className="text-sm font-semibold">Most confident mistakes</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Sorted by confidence, so the top row is where the classifier was most
            sure and most wrong.
          </p>
          <ul className="mt-4 space-y-3">
            {routing.worstCases.slice(0, 5).map((c) => (
              <li key={c.query} className="text-sm">
                <p className="leading-snug">{c.query}</p>
                <p className="tabular mt-1 text-xs text-muted-foreground">
                  expected <code className="font-mono">{c.expected}</code> · routed to{" "}
                  <code className="font-mono text-risk-medium">{c.predicted}</code> ·
                  confidence {c.confidence.toFixed(2)}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
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
