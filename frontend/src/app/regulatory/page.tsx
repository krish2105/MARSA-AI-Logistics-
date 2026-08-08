import type { Metadata } from "next";
import { AlertTriangle, CheckCircle2, Clock, Gavel, Layers } from "lucide-react";

import report from "@/data/regulatory.json";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: "Regulatory instruments",
  description:
    "Every tariff, listing and goods scope with the window it was in force — and a worked point-in-time resolution showing what it displaced.",
};

interface Instrument {
  id: string;
  issuer: string;
  kind: string;
  programme: string;
  title: string;
  scope: {
    hts_prefixes: string[];
    origin_countries: string[];
    entity_names: string[];
  };
  effect: {
    kind: string;
    rate_percent: number | null;
    additive: boolean;
    cap_percent: number | null;
    note: string;
  };
  effective_from: string;
  effective_to: string | null;
  date_basis: string;
  supersedes: string[];
  source_url: string;
  origin: string;
}

interface Payload {
  generatedAt: string;
  staleness: {
    instruments: number;
    ageDays: number | null;
    staleAfterDays: number;
    isStale: boolean;
    anySynthetic: boolean;
    note: string;
  };
  instruments: Instrument[];
  workedExample: {
    query: { hts: string; origin: string; on: string };
    effective: Instrument[];
    superseded: string[];
    contradictions: {
      left: string;
      right: string;
      reason: string;
      leftRate: number | null;
      rightRate: number | null;
    }[];
    inferredDates: string[];
    isTrustworthy: boolean;
  };
}

const PROGRAMME_LABEL: Record<string, string> = {
  mfn: "MFN base",
  section_232: "Section 232",
  section_301: "Section 301",
  ieepa: "IEEPA",
  cbam: "CBAM",
  uflpa: "UFLPA",
  ruling: "Ruling",
  unknown: "Unlabelled",
};

function effectLabel(effect: Instrument["effect"]) {
  if (effect.rate_percent === null) return effect.kind.replace(/_/g, " ");
  return `${effect.rate_percent.toFixed(1)}%${effect.additive ? " (stacks)" : ""}`;
}

export default function RegulatoryPage() {
  const { staleness, instruments, workedExample: worked } = report as Payload;
  const superseded = new Set(worked.superseded);

  return (
    <div className="mx-auto max-w-5xl px-4 py-14 sm:px-6">
      <header className="mb-10 max-w-3xl">
        <p className="mb-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Phase G · regulatory knowledge layer
        </p>
        <h1
          className="font-semibold tracking-tight text-balance"
          style={{ fontSize: "var(--text-step-5)", lineHeight: 1.08 }}
        >
          A rule is only an answer with a date attached
        </h1>
        <p className="mt-5 leading-relaxed text-muted-foreground">
          Section 232 was modified in June 2026. A system that retrieves May&rsquo;s
          rate has not made a small mistake &mdash; it has produced a number
          someone files a customs entry on. So every instrument here carries the
          window it was in force, and every query is a query at a date.
        </p>
      </header>

      {/* Provenance first, before any figure. */}
      {staleness.anySynthetic && (
        <div
          role="alert"
          className="mb-6 flex gap-3 rounded-xl border border-risk-medium/40 bg-risk-medium-muted p-4"
        >
          <AlertTriangle className="mt-0.5 size-5 shrink-0 text-risk-medium" aria-hidden />
          <div className="space-y-1.5">
            <p className="font-semibold text-risk-medium">
              These instruments are synthetic
            </p>
            <p className="text-sm leading-relaxed text-foreground/80">
              None of the four sources &mdash; Federal Register, USITC, DHS, the
              European Commission &mdash; is reachable from the build
              environment; all four return 403 from the egress proxy. Decision
              gate G1 therefore reports <code>NOT_EVALUATED</code>, which is
              neither a pass nor a failure. These fixtures reproduce the{" "}
              <em>shape</em> of the 2026 regime so the resolver can be exercised.{" "}
              <strong>No duty figure derived from them is valid.</strong>
            </p>
          </div>
        </div>
      )}

      <dl className="mb-10 grid gap-4 sm:grid-cols-3">
        <Stat label="Instruments" value={String(staleness.instruments)} />
        <Stat
          label="Set age"
          value={
            staleness.ageDays === null ? "—" : `${staleness.ageDays.toFixed(1)} days`
          }
          tone={staleness.isStale ? "warn" : "ok"}
        />
        <Stat
          label="Stale after"
          value={`${staleness.staleAfterDays} days`}
          hint="Section 232 changed twice in 2026"
        />
      </dl>

      {/* The worked example — the mechanism, shown rather than asserted. */}
      <section className="mb-10" aria-labelledby="worked-heading">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <Layers className="size-5 shrink-0 text-muted-foreground" aria-hidden />
          <h2
            id="worked-heading"
            className="font-semibold tracking-tight"
            style={{ fontSize: "var(--text-step-2)" }}
          >
            Resolved at a date
          </h2>
          <span
            className={cn(
              "ml-auto inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium",
              worked.isTrustworthy
                ? "bg-risk-low-muted text-risk-low"
                : "bg-risk-medium-muted text-risk-medium",
            )}
          >
            {worked.isTrustworthy ? (
              <CheckCircle2 className="size-3.5" aria-hidden />
            ) : (
              <AlertTriangle className="size-3.5" aria-hidden />
            )}
            {worked.isTrustworthy ? "resolved cleanly" : "cannot be published"}
          </span>
        </div>

        <p className="tabular mb-4 text-sm text-muted-foreground">
          HTS <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
            {worked.query.hts}
          </code>{" "}
          from <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
            {worked.query.origin}
          </code>{" "}
          on <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
            {worked.query.on}
          </code>
        </p>

        <div className="overflow-hidden rounded-2xl border border-border bg-card">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                  <th scope="col" className="px-4 py-2.5 text-left font-medium">
                    Instrument
                  </th>
                  <th scope="col" className="px-3 py-2.5 text-left font-medium">
                    Programme
                  </th>
                  <th scope="col" className="px-3 py-2.5 text-right font-medium">
                    Effect
                  </th>
                  <th scope="col" className="px-3 py-2.5 text-right font-medium">
                    In force
                  </th>
                </tr>
              </thead>
              <tbody>
                {worked.effective.map((i) => (
                  <tr key={i.id} className="border-b border-border/60 last:border-0">
                    <th scope="row" className="px-4 py-2.5 text-left font-normal">
                      <code className="font-mono text-xs">{i.id}</code>
                    </th>
                    <td className="px-3 py-2.5 text-muted-foreground">
                      {PROGRAMME_LABEL[i.programme] ?? i.programme}
                    </td>
                    <td className="tabular px-3 py-2.5 text-right font-medium">
                      {effectLabel(i.effect)}
                    </td>
                    <td className="tabular px-3 py-2.5 text-right text-muted-foreground">
                      {i.effective_from} → {i.effective_to ?? "open"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {worked.superseded.length > 0 && (
            <div className="border-t border-border px-5 py-4">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Superseded and excluded
              </p>
              <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                <code className="font-mono text-xs">
                  {worked.superseded.join(", ")}
                </code>{" "}
                &mdash; its own window is still open. It stops applying because a
                later instrument replaces it, not because it expired. A resolver
                that checked only dates would count both and double the duty.
              </p>
            </div>
          )}

          {worked.contradictions.map((c) => (
            <div
              key={`${c.left}-${c.right}`}
              className="border-t border-border bg-risk-high-muted px-5 py-4"
            >
              <p className="text-sm font-semibold text-risk-high">
                Contradiction &mdash; neither was chosen
              </p>
              <p className="mt-1.5 text-sm leading-relaxed text-foreground/80">
                <code className="font-mono text-xs">{c.left}</code> ({c.leftRate}%)
                vs <code className="font-mono text-xs">{c.right}</code> (
                {c.rightRate}%). {c.reason}
              </p>
            </div>
          ))}

          {worked.inferredDates.length > 0 && (
            <div className="border-t border-border px-5 py-4">
              <p className="text-sm font-semibold text-risk-medium">
                Inferred effective dates
              </p>
              <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                <code className="font-mono text-xs">
                  {worked.inferredDates.join(", ")}
                </code>{" "}
                fall back to publication date. Publication is not commencement
                &mdash; the gap is routinely weeks &mdash; so this window may be
                wrong.
              </p>
            </div>
          )}
        </div>
      </section>

      {/* The full set */}
      <section aria-labelledby="all-heading">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <Gavel className="size-5 shrink-0 text-muted-foreground" aria-hidden />
          <h2
            id="all-heading"
            className="font-semibold tracking-tight"
            style={{ fontSize: "var(--text-step-2)" }}
          >
            Every instrument held
          </h2>
        </div>

        <div className="space-y-3">
          {instruments.map((i) => {
            const displaced = superseded.has(i.id);
            return (
              <article
                key={i.id}
                className={cn(
                  "rounded-2xl border bg-card p-5",
                  displaced ? "border-border/50 opacity-70" : "border-border",
                )}
              >
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <code className="rounded bg-muted px-2 py-0.5 font-mono text-xs">
                    {i.id}
                  </code>
                  <span className="text-xs text-muted-foreground">
                    {PROGRAMME_LABEL[i.programme] ?? i.programme}
                  </span>
                  {displaced && (
                    <span className="rounded-md bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
                      superseded
                    </span>
                  )}
                  <span className="tabular ml-auto text-xs text-muted-foreground">
                    {i.effective_from} → {i.effective_to ?? "open"}
                  </span>
                </div>

                <p className="mt-2.5 text-sm leading-relaxed">{i.title}</p>

                <dl className="tabular mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
                  <Field label="Effect" value={effectLabel(i.effect)} />
                  <Field
                    label="Cap"
                    value={i.effect.cap_percent ? `${i.effect.cap_percent}%` : "—"}
                  />
                  <Field
                    label="Date basis"
                    value={i.date_basis === "explicit" ? "stated" : "inferred"}
                    tone={i.date_basis === "explicit" ? undefined : "warn"}
                  />
                  <Field
                    label="Scope"
                    value={
                      i.scope.hts_prefixes.length
                        ? `HTS ${i.scope.hts_prefixes.slice(0, 3).join(", ")}`
                        : i.scope.entity_names.length
                          ? "entity"
                          : i.scope.origin_countries.length
                            ? i.scope.origin_countries.slice(0, 4).join(", ")
                            : "unrestricted"
                    }
                  />
                </dl>

                {i.supersedes.length > 0 && (
                  <p className="mt-3 border-t border-border/70 pt-3 text-xs text-muted-foreground">
                    Supersedes{" "}
                    <code className="font-mono">{i.supersedes.join(", ")}</code>
                  </p>
                )}
              </article>
            );
          })}
        </div>
      </section>

      <p className="mt-10 flex items-start gap-2 text-sm leading-relaxed text-muted-foreground">
        <Clock className="mt-0.5 size-4 shrink-0" aria-hidden />
        <span>
          Regenerate with{" "}
          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
            marsa-reg export-report
          </code>
          . Run{" "}
          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
            marsa-reg probe
          </code>{" "}
          anywhere with outbound access to evaluate gate G1 against the real
          sources.
        </span>
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
  tone?: "ok" | "warn";
}) {
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "tabular mt-1 text-lg font-semibold",
          tone === "warn" && "text-risk-medium",
          tone === "ok" && "text-risk-low",
        )}
      >
        {value}
      </dd>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Field({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "warn";
}) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("mt-0.5 font-medium", tone === "warn" && "text-risk-medium")}>
        {value}
      </dd>
    </div>
  );
}
