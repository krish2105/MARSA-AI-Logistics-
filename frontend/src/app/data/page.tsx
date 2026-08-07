import type { Metadata } from "next";
import { AlertTriangle, CheckCircle2, Database } from "lucide-react";

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

      <p className="mt-10 text-sm leading-relaxed text-muted-foreground">
        Regenerate with{" "}
        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
          marsa-ingest export-report
        </code>{" "}
        after any ingestion run.
      </p>
    </div>
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
