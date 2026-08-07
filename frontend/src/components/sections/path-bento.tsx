import { Gauge, Network, Workflow } from "lucide-react";

import { ROUTE_PATHS, type RoutePathId } from "@/lib/routing";
import { cn } from "@/lib/utils";

const ICONS = { fast: Gauge, agentic: Workflow, graph: Network } as const;

const TARGETS: Record<RoutePathId, string> = {
  fast: "Target < 2s, near-zero cost per query",
  agentic: "Planner → Critic → retry, max 2 reformulations",
  graph: "k-hop traversal with LPI + ML risk overlay",
};

/**
 * Bento grid of the three routing paths.
 *
 * Sizes are intentionally uneven — the agentic path spans two columns because
 * it carries the most explanation. Uniform cards would waste the format.
 */
export function PathBento() {
  return (
    <section
      aria-labelledby="paths-heading"
      className="mx-auto max-w-6xl px-4 py-16 sm:px-6"
    >
      <h2
        id="paths-heading"
        className="font-semibold tracking-tight"
        style={{ fontSize: "var(--text-step-3)" }}
      >
        Three paths, three genuinely different retrieval problems
      </h2>
      <p className="mt-3 max-w-2xl leading-relaxed text-muted-foreground">
        Each path reads its own real corpus, so the router is solving a real
        routing problem — not one dataset split three ways.
      </p>

      <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {(Object.keys(ROUTE_PATHS) as RoutePathId[]).map((id) => {
          const path = ROUTE_PATHS[id];
          const Icon = ICONS[id];
          return (
            <article
              key={id}
              className={cn(
                "flex flex-col rounded-2xl border border-border bg-card p-5",
                "transition-colors hover:border-border/40",
                id === "agentic" && "sm:col-span-2 lg:col-span-1",
              )}
            >
              <div className="flex items-center gap-2.5">
                <span
                  className={cn(
                    "inline-flex size-8 items-center justify-center rounded-lg",
                    path.accentMuted,
                  )}
                >
                  <Icon className={cn("size-4", path.accentText)} aria-hidden />
                </span>
                <h3 className="font-semibold">{path.label}</h3>
              </div>

              <code className="mt-3 self-start rounded bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">
                {path.classification}
              </code>

              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                {path.strategy}
              </p>

              <ul className="mt-4 flex flex-wrap gap-1.5">
                {path.corpora.map((corpus) => (
                  <li
                    key={corpus}
                    className="rounded-md border border-border px-2 py-0.5 text-xs text-muted-foreground"
                  >
                    {corpus}
                  </li>
                ))}
              </ul>

              <p className="mt-auto pt-4 text-xs text-muted-foreground">
                {TARGETS[id]}
              </p>
            </article>
          );
        })}

        {/* Benchmark card — deliberately empty. The spec is explicit that the
            comparison table is reported as measured, so shipping invented
            numbers here would undercut the whole thesis. */}
        <article className="rounded-2xl border border-dashed border-border bg-card/40 p-5 sm:col-span-2 lg:col-span-3">
          <h3 className="font-semibold">Routing accuracy &amp; per-path cost</h3>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            Populated by the Phase F evaluation harness: routing accuracy against
            a 60-query human-labelled set, RAGAS faithfulness and context
            precision per path, and measured cost/latency. Published to{" "}
            <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
              RESULTS.md
            </code>{" "}
            exactly as measured — including any case where the fast path beats
            the agentic one.
          </p>
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {(Object.keys(ROUTE_PATHS) as RoutePathId[]).map((id) => (
              <div
                key={id}
                className="rounded-lg border border-border bg-background px-4 py-3"
              >
                <p className="text-xs text-muted-foreground">
                  {ROUTE_PATHS[id].label}
                </p>
                <p className="tabular mt-1 font-mono text-lg text-muted-foreground/50">
                  — · — · —
                </p>
              </div>
            ))}
          </div>
        </article>
      </div>
    </section>
  );
}
