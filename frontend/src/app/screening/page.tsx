import type { Metadata } from "next";
import { AlertTriangle, HelpCircle, ShieldAlert, SearchX } from "lucide-react";

import report from "@/data/screening.json";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: "Supplier screening",
  description:
    "Screening against the UFLPA Entity List and CBAM goods scope — three-valued, and none of the three is a clearance.",
};

interface Match {
  listedName: string;
  score: number;
  normalisedQuery: string;
  normalisedListed: string;
  sharedTokens: string[];
  isHit: boolean;
}

interface Supplier {
  supplier: string;
  finding: "hit" | "possible" | "no_evidence_found";
  bestScore: number;
  matches: Match[];
  instrumentIds: string[];
  notes: string[];
}

interface Goods {
  hts: string;
  finding: "hit" | "no_evidence_found";
  inScope: string[];
  notes: string[];
}

interface Payload {
  on: string;
  suppliers: Supplier[];
  goods: Goods[];
  listAgeDays: number | null;
  listIsStale: boolean;
  entitiesChecked: number;
  anySynthetic: boolean;
  counts: { hit: number; possible: number; noEvidenceFound: number };
  warnings: string[];
  disclaimer: string;
}

const FINDING = {
  hit: {
    label: "HIT",
    icon: ShieldAlert,
    chip: "bg-risk-high-muted text-risk-high",
    border: "border-risk-high/40",
  },
  possible: {
    label: "POSSIBLE",
    icon: HelpCircle,
    chip: "bg-risk-medium-muted text-risk-medium",
    border: "border-risk-medium/40",
  },
  // Deliberately neutral, never green. A green tick reads as a clearance,
  // which is the one claim this tool must never make.
  no_evidence_found: {
    label: "no evidence found",
    icon: SearchX,
    chip: "bg-muted text-muted-foreground",
    border: "border-border",
  },
} as const;

export default function ScreeningPage() {
  const d = report as Payload;

  return (
    <div className="mx-auto max-w-5xl px-4 py-14 sm:px-6">
      <header className="mb-10 max-w-3xl">
        <p className="mb-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Phase I · forced-labour and CBAM screening
        </p>
        <h1
          className="font-semibold tracking-tight text-balance"
          style={{ fontSize: "var(--text-step-5)", lineHeight: 1.08 }}
        >
          Three findings, and none of them is &ldquo;clear&rdquo;
        </h1>
        <p className="mt-5 leading-relaxed text-muted-foreground">
          A screening tool that reports &ldquo;no match&rdquo; reads as a
          clearance, and an importer acts on it. This one checked a list it
          holds, of a version it knows, at a moment it can name — so that is
          what it says. The UFLPA Entity List reached 187 entities on 3 August
          2026, after DHS added 43 in a single day.
        </p>
      </header>

      {/* Provenance before any finding. */}
      {d.warnings.length > 0 && (
        <div
          role="alert"
          className="mb-6 rounded-xl border border-risk-medium/40 bg-risk-medium-muted p-4"
        >
          <div className="flex gap-3">
            <AlertTriangle
              className="mt-0.5 size-5 shrink-0 text-risk-medium"
              aria-hidden
            />
            <ul className="space-y-2">
              {d.warnings.map((w) => (
                <li
                  key={w.slice(0, 40)}
                  className="text-sm leading-relaxed text-foreground/80"
                >
                  {w}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <dl className="mb-10 grid gap-4 sm:grid-cols-4">
        <Stat label="Hits" value={String(d.counts.hit)} tone="high" />
        <Stat label="Possible" value={String(d.counts.possible)} tone="medium" />
        <Stat
          label="No evidence found"
          value={String(d.counts.noEvidenceFound)}
          hint="not a clearance"
        />
        <Stat
          label="Entities checked"
          value={String(d.entitiesChecked)}
          hint={`as at ${d.on}`}
        />
      </dl>

      <section className="mb-10" aria-labelledby="suppliers-heading">
        <h2
          id="suppliers-heading"
          className="mb-4 font-semibold tracking-tight"
          style={{ fontSize: "var(--text-step-2)" }}
        >
          Suppliers
        </h2>

        <div className="space-y-3">
          {d.suppliers.map((s) => {
            const meta = FINDING[s.finding];
            const Icon = meta.icon;
            return (
              <article
                key={s.supplier}
                className={cn("rounded-2xl border bg-card p-5", meta.border)}
              >
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <span className="font-medium">{s.supplier}</span>
                  <span
                    className={cn(
                      "ml-auto inline-flex items-center gap-1.5 rounded-md px-2 py-1",
                      "text-xs font-medium",
                      meta.chip,
                    )}
                  >
                    <Icon className="size-3.5" aria-hidden />
                    {meta.label}
                  </span>
                </div>

                {s.matches.length > 0 && (
                  <div className="mt-4 border-t border-border/70 pt-3">
                    <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                      Why
                    </p>
                    <ul className="mt-2 space-y-2">
                      {s.matches.slice(0, 3).map((m) => (
                        <li key={m.listedName} className="text-sm leading-relaxed">
                          <span className="tabular font-medium">
                            {(m.score * 100).toFixed(0)}%
                          </span>{" "}
                          against{" "}
                          <span className="text-muted-foreground">
                            {m.listedName}
                          </span>
                          {m.sharedTokens.length > 0 && (
                            <span className="text-muted-foreground">
                              {" "}
                              — shared:{" "}
                              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                                {m.sharedTokens.join(", ")}
                              </code>
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {s.notes.map((note) => (
                  <p
                    key={note.slice(0, 30)}
                    className="mt-3 text-sm leading-relaxed text-muted-foreground"
                  >
                    {note}
                  </p>
                ))}

                {s.instrumentIds.length > 0 && (
                  <p className="mt-3 font-mono text-xs text-muted-foreground">
                    {s.instrumentIds.join(", ")}
                  </p>
                )}
              </article>
            );
          })}
        </div>
      </section>

      {d.goods.length > 0 && (
        <section className="mb-10" aria-labelledby="goods-heading">
          <h2
            id="goods-heading"
            className="mb-4 font-semibold tracking-tight"
            style={{ fontSize: "var(--text-step-2)" }}
          >
            Goods scope
          </h2>
          <div className="space-y-3">
            {d.goods.map((g) => {
              const meta = FINDING[g.finding];
              return (
                <article
                  key={g.hts}
                  className={cn("rounded-2xl border bg-card p-5", meta.border)}
                >
                  <div className="flex flex-wrap items-center gap-3">
                    <code className="rounded bg-muted px-2 py-0.5 font-mono text-sm">
                      {g.hts}
                    </code>
                    <span
                      className={cn(
                        "ml-auto rounded-md px-2 py-1 text-xs font-medium",
                        meta.chip,
                      )}
                    >
                      {meta.label}
                    </span>
                  </div>
                  {g.notes.map((note) => (
                    <p
                      key={note.slice(0, 30)}
                      className="mt-3 text-sm leading-relaxed text-muted-foreground"
                    >
                      {note}
                    </p>
                  ))}
                </article>
              );
            })}
          </div>
        </section>
      )}

      <p className="rounded-xl border border-dashed border-border px-4 py-4 text-sm leading-relaxed text-muted-foreground">
        {d.disclaimer}
      </p>

      <p className="mt-6 text-sm leading-relaxed text-muted-foreground">
        Regenerate with{" "}
        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
          marsa-screen export-report
        </code>
        , or screen your own list with{" "}
        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
          marsa-screen suppliers --file suppliers.txt
        </code>
        .
      </p>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "high" | "medium";
}) {
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "tabular mt-1 text-lg font-semibold",
          tone === "high" && "text-risk-high",
          tone === "medium" && "text-risk-medium",
        )}
      >
        {value}
      </dd>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
